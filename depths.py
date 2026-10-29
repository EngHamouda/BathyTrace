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

"""Order contours by crossings along a user-selected downhill transect."""
import math


def cross(a,b):
    return a[0]*b[1]-a[1]*b[0]


def nearest_point(points, p):
    best = None
    for a,b in zip(points,points[1:]):
        v = [b[k]-a[k] for k in (0,1)]
        t = max(0,min(1,sum((p[k]-a[k])*v[k] for k in (0,1))/max(sum(x*x for x in v),1e-12)))
        q = [a[k]+t*v[k] for k in (0,1)]
        candidate = (math.dist(p,q),q)
        if best is None or candidate[0] < best[0]:
            best = candidate
    return best[1]


def estimate_depths(features, seed_id, origin, toward, base=0, interval=.5):
    if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in [base,interval,*origin,*toward]):
        raise ValueError('Depths and direction must be finite numbers.')
    if len(origin)!=2 or len(toward)!=2 or base > 0 or interval <= 0:
        raise ValueError('Use a shallow value of 0 or a negative number, and a positive interval.')
    seed = next((f for f in features if f['id']==seed_id),None)
    if seed is None:
        raise ValueError('Select the shallowest contour first.')
    origin = nearest_point(seed['points'],origin)
    direction = [toward[k]-origin[k] for k in (0,1)]
    length = math.hypot(*direction)
    if length < 10:
        raise ValueError('Click farther from the reference contour toward deeper water.')
    direction = [v/length for v in direction]
    rows = []
    missed = []
    stop = math.inf
    for f in features:
        hits=[]
        for a,b in zip(f['points'],f['points'][1:]):
            v=[b[k]-a[k] for k in (0,1)]
            delta=[a[k]-origin[k] for k in (0,1)]
            den=cross(direction,v)
            if abs(den)<1e-9:
                if abs(cross(delta,direction))<1e-6:
                    ta=sum(delta[k]*direction[k] for k in (0,1))
                    tb=sum((b[k]-origin[k])*direction[k] for k in (0,1))
                    if max(ta,tb)>=0:
                        raise ValueError('The direction runs along a contour. Choose a direction across the lines.')
                continue
            t=cross(delta,v)/den
            u=cross(delta,direction)/den
            if t>=-1e-6 and -1e-7<=u<=1+1e-7:
                hits.append(max(0,t))
        hits.sort()
        unique=[]
        for t in hits:
            if not unique or t-unique[-1]>.5:
                unique.append(t)
        if len(unique)>1:
            # A return crossing matters even for open, clipped, or almost
            # closed contours. Do not require coincident endpoints and do
            # not assign another depth to the same physical line.
            stop = min(stop, unique[1])
        if f['id']==seed_id:
            rows.append((0,f))
        elif unique:
            rows.append((unique[0],f))
        else:
            missed.append(f['id'])
    rows.sort(key=lambda r:r[0])
    missed.extend(f['id'] for distance,f in rows if distance >= stop)
    rows = [(distance,f) for distance,f in rows if distance < stop]
    for (ta,_),(tb,_) in zip(rows,rows[1:]):
        if tb-ta < 1:
            raise ValueError('Overlapping contours make the depth order ambiguous. Join or remove duplicates first.')
    assignments=[]
    for rank,(distance,f) in enumerate(rows):
        depth=round(base-rank*interval,9)
        if not math.isfinite(depth):
            raise ValueError('Depth range is too large.')
        assignments.append(dict(id=f['id'],depth=depth,rank=rank,
                                point=[origin[k]+distance*direction[k] for k in (0,1)]))
    return dict(assignments=assignments,skipped=missed,origin=origin,direction=direction,
                base=base,interval=interval,unit='m',seed_id=seed_id,
                stopped_at_return=math.isfinite(stop))
