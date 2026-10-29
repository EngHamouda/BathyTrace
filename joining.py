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

"""Conservative endpoint stitching in original raster pixel coordinates."""
import copy
import math


def tangent(points, end, reach=45):
    """Outward direction measured over a short arc, independent of vertex density."""
    pts = points if end == 0 else points[::-1]
    origin = pts[0]
    distance = 0
    inside = origin
    for a, b in zip(pts, pts[1:]):
        length = math.dist(a, b)
        if length == 0:
            continue
        fraction = min(1, (reach-distance)/length)
        inside = [a[k]+fraction*(b[k]-a[k]) for k in (0, 1)]
        distance += length
        if distance >= reach:
            break
    v = [origin[k]-inside[k] for k in (0, 1)]
    n = math.hypot(*v)
    return [x/n for x in v] if n > 1e-9 else None


def crosses(a, b, c, d):
    def orient(p, q, r):
        return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    # Include touching/collinear strokes, except the intended endpoint contacts.
    if any(math.dist(p, q) < 1e-7 for p in (a,b) for q in (c,d)):
        return False
    if max(a[0],b[0]) < min(c[0],d[0]) or max(c[0],d[0]) < min(a[0],b[0]) or max(a[1],b[1]) < min(c[1],d[1]) or max(c[1],d[1]) < min(a[1],b[1]):
        return False
    return orient(a,b,c)*orient(a,b,d) <= 0 and orient(c,d,a)*orient(c,d,b) <= 0


def compatible(a, b):
    if a.get('depth') is not None and b.get('depth') is not None and a['depth'] != b['depth']:
        return False
    ua, ub = a.get('unit','unknown'), b.get('unit','unknown')
    return ua == ub or ua == 'unknown' or ub == 'unknown'


def join_segments(features, max_gap=300):
    if not math.isfinite(max_gap) or not 1 <= max_gap <= 500:
        raise ValueError('Maximum gap must be between 1 and 500 original-image pixels.')
    if len(features) > 3000:
        raise ValueError('Join at most 3,000 segments at a time.')
    lines = copy.deepcopy(features)
    joins = 0
    while True:
        endpoints = []
        for i, f in enumerate(lines):
            if len(f['points']) < 2 or math.dist(f['points'][0],f['points'][-1]) < 1e-7:
                continue
            for end in (0,1):
                v = tangent(f['points'],end)
                if v:
                    endpoints.append((i,end,f['points'][0 if end == 0 else -1],v))
        options = {k:[] for k in range(len(endpoints))}
        for ia, (i,ei,a,va) in enumerate(endpoints):
            for ib in range(ia+1,len(endpoints)):
                j,ej,b,vb = endpoints[ib]
                if i == j or not compatible(lines[i],lines[j]):
                    continue
                delta = [b[k]-a[k] for k in (0,1)]
                d = math.hypot(*delta)
                if d > max_gap:
                    continue
                if d > 1e-7:
                    direction = [x/d for x in delta]
                    ca = sum(x*y for x,y in zip(va,direction))
                    cb = -sum(x*y for x,y in zip(vb,direction))
                    # Both endpoints must face into the same gap, not sideways to a neighbor.
                    agreement = -sum(x*y for x,y in zip(va,vb))
                    # Raster tile seams can offset a stroke sideways by 1–3 pixels.
                    # Use tangent agreement at that scale instead of a noisy gap angle.
                    angular_limit = math.cos(math.radians(30))
                    lateral_allowance = 3/d
                    if agreement < math.cos(math.radians(40)) or min(ca,cb) < -lateral_allowance or (d > 4 and min(ca,cb) < angular_limit-lateral_allowance):
                        continue
                    score = d*(1+4*(1-agreement)+2*max(0,2-ca-cb-6/d))
                else:
                    if sum(x*y for x,y in zip(va,vb)) > -.866:
                        continue
                    score = 0
                options[ia].append((score,ib))
                options[ib].append((score,ia))
        best = {}
        for k, candidates in options.items():
            candidates.sort()
            # Leave similarly plausible continuations disconnected for human review.
            if candidates and (len(candidates)==1 or candidates[1][0] > candidates[0][0]*1.3+2):
                best[k] = candidates[0]
        pairs = sorted((score,a,b) for a,(score,b) in best.items() if a < b and b in best and best[b][1] == a)
        used, replacements = set(), []
        bridges = []
        for _, ia, ib in pairs:
            i,ei,a,_ = endpoints[ia]
            j,ej,b,_ = endpoints[ib]
            if i in used or j in used:
                continue
            if any(crosses(a,b,c,d) for f in lines for c,d in zip(f['points'],f['points'][1:])):
                continue
            if any(crosses(a,b,c,d) for c,d in bridges):
                continue
            left, right = lines[i],lines[j]
            pa = left['points'] if ei == 1 else left['points'][::-1]
            pb = right['points'] if ej == 0 else right['points'][::-1]
            result = dict(left)
            result.update(points=pa+(pb[1:] if a == b else pb),reviewed=False,method='joined',
                          depth=left.get('depth') if left.get('depth') is not None else right.get('depth'),
                          unit=left.get('unit','unknown') if left.get('unit','unknown') != 'unknown' else right.get('unit','unknown'),
                          bridges=left.get('bridges',[])+right.get('bridges',[])+([[a,b]] if a != b else []),
                          joined_segments=left.get('joined_segments',1)+right.get('joined_segments',1))
            replacements.append(result)
            used.update((i,j))
            bridges.append((a,b))
            joins += 1
        if not used:
            break
        lines = [f for i,f in enumerate(lines) if i not in used]+replacements
    return dict(features=lines, joins=joins, remaining=len(lines), max_gap=max_gap)
