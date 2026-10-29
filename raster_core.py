# Copyright (C) 2026 Mohamed Hamouda
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of BathyTrace.
#
# BathyTrace is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# BathyTrace is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with BathyTrace. If not, see <https://www.gnu.org/licenses/>.

import heapq, math, time, uuid
from pathlib import Path
from collections import deque
import numpy as np
from PIL import Image, ImageFilter
R = 6378137.0

def simplify(points, tolerance=1.0):
    if len(points) < 3:
        return points
    a = np.asarray(points, dtype=float)
    keep = {0, len(a)-1}
    stack = [(0, len(a)-1)]
    while stack:
        lo, hi = stack.pop()
        if hi-lo < 2:
            continue
        v = a[hi]-a[lo]
        q = a[lo+1:hi]-a[lo]
        t = np.clip(q @ v / max(float(v @ v), 1e-12), 0, 1)
        d = np.linalg.norm(q-t[:, None]*v, axis=1)
        k = int(d.argmax())
        if d[k] > tolerance:
            k += lo+1
            keep.add(k)
            stack.extend([(lo, k), (k, hi)])
    return a[sorted(keep)].tolist()

class Chart:
    def __init__(self, path, display_name=None):
        self.path = Path(path).resolve()
        self.name = display_name or self.path.name
        self.token = uuid.uuid4().hex
        with Image.open(path) as src:
            if src.format != 'TIFF':
                raise ValueError('Select a TIFF or GeoTIFF image.')
            tags = src.tag_v2
            keys = tags.get(34735, ())
            keys = {keys[i]: keys[i+3] for i in range(4, len(keys), 4) if keys[i+1] == 0}
            self.epsg = keys.get(3072, keys.get(2048))
            if self.epsg not in (3857, 4326):
                raise ValueError('This version supports GeoTIFF EPSG:3857 and EPSG:4326 only.')
            if tags.get(34264) or 33550 not in tags or 33922 not in tags:
                raise ValueError('This version requires an unrotated GeoTIFF with pixel scale and tiepoint tags.')
            if tags.get(274, 1) != 1:
                raise ValueError('Nonstandard TIFF orientation is not supported.')
            self.scale = list(tags[33550])[:2]
            self.tie = list(tags[33922])[:6]
            self.pixel_type = keys.get(1025, 1)
            rgba = src.convert('RGBA')
            bg = Image.new('RGBA', src.size, 'white')
            bg.alpha_composite(rgba)
            self.image = bg.convert('RGB')
        self.w, self.h = self.image.size
        self.levels = [self.image]
        # QGIS handles raster rendering and zoom levels.
        self.signature = f'{self.name}:{self.w}:{self.h}:{self.tie}:{self.scale}'

    def metadata(self):
        return dict(name=self.name, token=self.token, width=self.w, height=self.h, epsg=self.epsg,
                    scale=self.scale, tie=self.tie, pixel_type=self.pixel_type,
                    levels=len(self.levels), signature=self.signature)

    def projected(self, p):
        # Canvas coordinates measure from the outer image corner, including half-pixel centers.
        offset = 0.5 if self.pixel_type == 2 else 0
        i, j, _, x, y, _ = self.tie
        return [x+(p[0]-offset-i)*self.scale[0], y-(p[1]-offset-j)*self.scale[1]]

    def lonlat(self, p):
        x, y = self.projected(p)
        if self.epsg == 4326:
            return [x, y]
        return [math.degrees(x/R), math.degrees(math.atan(math.sinh(y/R)))]

    def trace(self, start, end):
        for p in (start, end):
            if len(p) != 2 or not all(math.isfinite(v) for v in p) or not (0 <= p[0] < self.w and 0 <= p[1] < self.h):
                raise ValueError('Choose points inside the chart.')
        if math.dist(start, end) > 1600:
            raise ValueError('Place the next anchor closer (less than 1,600 image pixels).')
        x0 = max(0, int(min(start[0], end[0]))-70)
        y0 = max(0, int(min(start[1], end[1]))-70)
        x1 = min(self.w, int(max(start[0], end[0]))+71)
        y1 = min(self.h, int(max(start[1], end[1]))+71)
        crop = self.image.crop((x0, y0, x1, y1))
        gray = np.asarray(crop.convert('L'), dtype=float)
        background = np.asarray(crop.convert('L').filter(ImageFilter.BoxBlur(7)), dtype=float)
        strength = np.maximum(background-gray, 0)
        cost = 1+24*np.exp(-strength/24)
        h, w = cost.shape

        def snap(p):
            x, y = int(p[0])-x0, int(p[1])-y0
            xa, xb, ya, yb = max(0, x-12), min(w, x+13), max(0, y-12), min(h, y+13)
            yy, xx = np.mgrid[ya:yb, xa:xb]
            score = strength[ya:yb, xa:xb]-2*np.hypot(xx-x, yy-y)
            sy, sx = np.unravel_index(score.argmax(), score.shape)
            return (sx+xa, sy+ya)

        s, e = snap(start), snap(end)
        dist = np.full((h, w), np.inf)
        previous = {}
        dist[s[1], s[0]] = 0
        queue = [(math.dist(s, e), 0, s)]
        began = time.monotonic()
        while queue:
            _, g, p = heapq.heappop(queue)
            x, y = p
            if g != dist[y, x]:
                continue
            if p == e:
                path = [p]
                while path[-1] != s:
                    path.append(previous[path[-1]])
                path.reverse()
                return simplify([[x+x0+0.5, y+y0+0.5] for x, y in path], 0.7)
            if time.monotonic()-began > 8:
                raise ValueError('Trace search was too broad. Try a closer anchor or manual mode.')
            for dx, dy in NEIGHBORS:
                nx, ny = x+dx, y+dy
                if 0 <= nx < w and 0 <= ny < h:
                    ng = g+(cost[y, x]+cost[ny, nx])*0.5*(1.41421356237 if dx and dy else 1)
                    if ng < dist[ny, nx]:
                        dist[ny, nx] = ng
                        previous[(nx, ny)] = p
                        heapq.heappush(queue, (ng+math.hypot(nx-e[0], ny-e[1]), ng, (nx, ny)))
        raise ValueError('No path found. Try manual mode.')

    def detect(self, threshold=150, minimum=150):
        if not 30 <= threshold <= 200 or not 30 <= minimum <= 5000:
            raise ValueError('Invalid detection settings.')
        a = np.asarray(self.image)
        # Dark neutral strokes. Bright water fills and pale copyright marks are excluded.
        mask = (a.max(axis=2) < threshold)
        ys, xs = np.nonzero(mask)
        if len(xs) > 1500000:
            raise ValueError('Too many candidate pixels. Reduce the darkness threshold.')
        remaining = set(zip(xs.tolist(), ys.tolist()))
        result = []

        def walk(seed, allowed, remove=False):
            q = deque([seed])
            parents = {seed: None}
            if remove:
                allowed.remove(seed)
            last = seed
            while q:
                last = q.popleft()
                x, y = last
                for dx, dy in NEIGHBORS:
                    n = (x+dx, y+dy)
                    if n in allowed and n not in parents:
                        parents[n] = last
                        q.append(n)
                        if remove:
                            allowed.remove(n)
            return last, parents

        while remaining:
            far, component = walk(next(iter(remaining)), remaining, True)
            if len(component) < minimum:
                continue
            end, parents = walk(far, component)
            path = [end]
            while parents[path[-1]] is not None:
                path.append(parents[path[-1]])
            if len(path) < minimum:
                continue
            result.append(dict(id=f'auto-{len(result)+1}', points=simplify([[x+0.5,y+0.5] for x,y in path]),
                               depth=None, unit='unknown', reviewed=False, method='automatic'))
        return result

NEIGHBORS = [(dx,dy) for dx in (-1,0,1) for dy in (-1,0,1) if dx or dy]
