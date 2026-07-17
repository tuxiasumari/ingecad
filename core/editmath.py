# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Geometry for the editing commands: trim, extend, offset, fillet.

Pure 2D math over plain tuples — headless and exhaustively testable,
because TRIM/EXTEND are the highest bar of the editing phase ("se sienten
como AutoCAD").
"""
from __future__ import annotations

import math
from typing import Optional

Point = tuple[float, float]
Seg = tuple[float, float, float, float]

EPS = 1e-9


# -- intersections -------------------------------------------------------------

def line_line_intersection(s1: Seg, s2: Seg, infinite2: bool = False):
    """Intersection of segment s1 (as param t in [0,1]) with s2.

    Returns (t, point) or None. ``infinite2`` treats s2 as an infinite line
    (EXTEND's edge behaves that way in AutoCAD's default Edge=Extend? No —
    default is no-extend; we keep edges finite unless asked).
    """
    x1, y1, x2, y2 = s1
    x3, y3, x4, y4 = s2
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < EPS:
        return None
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    if not infinite2 and not (-EPS <= u <= 1.0 + EPS):
        return None
    return t, (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def circle_circle_intersections(c1: Point, r1: float,
                                c2: Point, r2: float) -> list[Point]:
    """Intersection points of two circles (0, 1 or 2)."""
    dx, dy = c2[0] - c1[0], c2[1] - c1[1]
    d = math.hypot(dx, dy)
    if d < EPS or d > r1 + r2 + EPS or d < abs(r1 - r2) - EPS:
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2.0 * d)
    h2 = r1 * r1 - a * a
    h = math.sqrt(max(0.0, h2))
    mx = c1[0] + a * dx / d
    my = c1[1] + a * dy / d
    if h < EPS:
        return [(mx, my)]
    ox, oy = -dy / d * h, dx / d * h
    return [(mx + ox, my + oy), (mx - ox, my - oy)]


def line_circle_intersections(seg: Seg, center: Point, r: float):
    """Params t (may be outside [0,1]) where the segment's line meets circle."""
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    fx, fy = x1 - center[0], y1 - center[1]
    a = dx * dx + dy * dy
    if a < EPS:
        return []
    b = 2 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - r * r
    disc = b * b - 4 * a * c
    if disc < 0:
        return []
    sq = math.sqrt(disc)
    return [(-b - sq) / (2 * a), (-b + sq) / (2 * a)]


# -- TRIM ----------------------------------------------------------------------

def trim_segment(seg: Seg, cutters: list[Seg],
                 circles: list[tuple[Point, float]],
                 pick_t: float) -> Optional[list[Seg]]:
    """Remove the span of ``seg`` around ``pick_t`` between cutting edges.

    Returns the surviving pieces (0, 1 or 2 segments), or None when no
    cutter crosses the segment (nothing to trim — AutoCAD says so too).
    """
    ts: list[float] = []
    for c in cutters:
        hit = line_line_intersection(seg, c)
        if hit is not None and -EPS < hit[0] < 1.0 + EPS:
            ts.append(min(max(hit[0], 0.0), 1.0))
    for center, r in circles:
        for t in line_circle_intersections(seg, center, r):
            if -EPS < t < 1.0 + EPS:
                ts.append(min(max(t, 0.0), 1.0))
    ts = sorted({round(t, 12) for t in ts if EPS < t < 1.0 - EPS})
    if not ts:
        return None
    lo = max((t for t in ts if t <= pick_t), default=0.0)
    hi = min((t for t in ts if t >= pick_t), default=1.0)
    x1, y1, x2, y2 = seg

    def at(t: float) -> Point:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))

    pieces: list[Seg] = []
    if lo > 0.0:
        p = at(lo)
        pieces.append((x1, y1, p[0], p[1]))
    if hi < 1.0:
        p = at(hi)
        pieces.append((p[0], p[1], x2, y2))
    return pieces


def _circle_crossing_angles(center: Point, r: float, cutters: list[Seg],
                            cutter_circles: list[tuple[Point, float]]):
    angles: list[float] = []
    for cseg in cutters:
        for t in line_circle_intersections(cseg, center, r):
            if -EPS <= t <= 1.0 + EPS:
                x = cseg[0] + t * (cseg[2] - cseg[0])
                y = cseg[1] + t * (cseg[3] - cseg[1])
                angles.append(math.atan2(y - center[1], x - center[0]) % math.tau)
    for c2, r2 in cutter_circles:
        for x, y in circle_circle_intersections(center, r, c2, r2):
            angles.append(math.atan2(y - center[1], x - center[0]) % math.tau)
    return angles


def trim_circle(center: Point, r: float, cutters: list[Seg],
                pick_angle: float,
                cutter_circles: Optional[list[tuple[Point, float]]] = None,
                ) -> Optional[tuple[float, float]]:
    """Trim a full circle at its cutter crossings: the surviving ARC.

    Cutters can be segments AND other circles/arcs (the classic
    two-intersecting-circles trim). Returns (start_angle, end_angle) in
    degrees ccw, or None if fewer than two crossings exist.
    """
    angles = _circle_crossing_angles(center, r, cutters, cutter_circles or [])
    angles = sorted(set(angles))
    if len(angles) < 2:
        return None
    a = pick_angle % math.tau
    # find the arc span containing the pick, remove it: survivor is the rest
    for i, a0 in enumerate(angles):
        a1 = angles[(i + 1) % len(angles)]
        span = (a1 - a0) % math.tau or math.tau
        if (a - a0) % math.tau <= span:
            return (math.degrees(a1), math.degrees(a0))
    return None


def trim_arc(center: Point, r: float, a_start: float, a_end: float,
             cutters: list[Seg], pick_angle: float,
             cutter_circles: Optional[list[tuple[Point, float]]] = None):
    """Trim an ARC (degrees) around pick_angle. Returns list of (a0, a1)."""
    s = math.radians(a_start) % math.tau
    e = math.radians(a_end) % math.tau
    sweep = (e - s) % math.tau or math.tau
    cuts: list[float] = []
    for ang in _circle_crossing_angles(center, r, cutters, cutter_circles or []):
        rel = (ang - s) % math.tau
        if EPS < rel < sweep - EPS:
            cuts.append(rel)
    cuts = sorted(set(cuts))
    if not cuts:
        return None
    pick_rel = (pick_angle - s) % math.tau
    lo = max((c for c in cuts if c <= pick_rel), default=0.0)
    hi = min((c for c in cuts if c >= pick_rel), default=sweep)
    out = []
    if lo > 0.0:
        out.append((math.degrees(s), math.degrees(s + lo)))
    if hi < sweep:
        out.append((math.degrees(s + hi), math.degrees(s + sweep)))
    return out


def trim_polyline(points: list[Point], closed: bool, pick_point: Point,
                  cutters: list[Seg],
                  circles: list[tuple[Point, float]]):
    """Trim the picked span of a straight-segment polyline.

    The polyline's OWN other segments also act as cutting edges (AutoCAD
    self-trim: the crossing tails of a sketch trim against the shape).
    Returns a list of vertex chains (0, 1 or 2 polylines) or None when no
    edge crosses the picked segment.
    """
    n = len(points)
    if n < 2:
        return None
    seg_count = n if closed else n - 1
    segs = [(points[i][0], points[i][1],
             points[(i + 1) % n][0], points[(i + 1) % n][1])
            for i in range(seg_count)]

    # picked segment: closest to the pick point
    def dist2(seg: Seg) -> float:
        x1, y1, x2, y2 = seg
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((pick_point[0] - x1) * dx
                                                   + (pick_point[1] - y1) * dy) / L2))
        qx, qy = x1 + t * dx, y1 + t * dy
        return (pick_point[0] - qx) ** 2 + (pick_point[1] - qy) ** 2

    k = min(range(seg_count), key=lambda i: dist2(segs[i]))
    seg_k = segs[k]
    dx, dy = seg_k[2] - seg_k[0], seg_k[3] - seg_k[1]
    L2 = dx * dx + dy * dy
    pick_t = 0.0 if L2 == 0 else max(0.0, min(1.0, (
        (pick_point[0] - seg_k[0]) * dx + (pick_point[1] - seg_k[1]) * dy) / L2))

    all_cutters = list(cutters) + [s for i, s in enumerate(segs) if i != k]
    pieces = trim_segment(seg_k, all_cutters, circles, pick_t)
    if pieces is None:
        return None
    piece1 = next((p for p in pieces
                   if (p[0], p[1]) == (seg_k[0], seg_k[1])), None)
    piece2 = next((p for p in pieces
                   if (p[2], p[3]) == (seg_k[2], seg_k[3])), None)

    chains: list[list[Point]] = []
    if closed:
        # the ring opens at the removed span: one chain from the trim end
        # forward around to the trim start
        idxs = [(k + 1 + i) % n for i in range(n)]
        chain: list[Point] = []
        if piece2 is not None:
            chain.append((piece2[0], piece2[1]))
        chain.extend(points[i] for i in idxs)
        if piece1 is not None:
            chain.append((piece1[2], piece1[3]))
        if len(chain) >= 2:
            chains.append(chain)
    else:
        chain_a: list[Point] = list(points[:k + 1])
        if piece1 is not None:
            chain_a.append((piece1[2], piece1[3]))
        chain_b: list[Point] = []
        if piece2 is not None:
            chain_b.append((piece2[0], piece2[1]))
        chain_b.extend(points[k + 1:])
        for chain in (chain_a, chain_b):
            if len(chain) >= 2:
                chains.append(chain)
    return chains


# -- EXTEND --------------------------------------------------------------------

def extend_segment(seg: Seg, edges: list[Seg],
                   circles: list[tuple[Point, float]],
                   pick_t: float) -> Optional[Seg]:
    """Extend the picked end of ``seg`` to the nearest boundary crossing."""
    forward = pick_t >= 0.5  # extend the end nearest to the pick
    best: Optional[float] = None
    for e in edges:
        hit = line_line_intersection(seg, e)
        if hit is None:
            continue
        t = hit[0]
        if forward and t > 1.0 + EPS:
            best = t if best is None else min(best, t)
        elif not forward and t < -EPS:
            best = t if best is None else max(best, t)
    for center, r in circles:
        for t in line_circle_intersections(seg, center, r):
            if forward and t > 1.0 + EPS:
                best = t if best is None else min(best, t)
            elif not forward and t < -EPS:
                best = t if best is None else max(best, t)
    if best is None:
        return None
    x1, y1, x2, y2 = seg
    px = x1 + best * (x2 - x1)
    py = y1 + best * (y2 - y1)
    return (x1, y1, px, py) if forward else (px, py, x2, y2)


# -- OFFSET --------------------------------------------------------------------

def offset_line(seg: Seg, distance: float, side_point: Point) -> Seg:
    """Parallel copy of the segment, on the side of ``side_point``."""
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    if L < EPS:
        raise ValueError("zero-length line")
    nx, ny = -dy / L, dx / L
    # choose the normal pointing toward the side point
    side = (side_point[0] - x1) * nx + (side_point[1] - y1) * ny
    if side < 0:
        nx, ny = -nx, -ny
    ox, oy = nx * distance, ny * distance
    return (x1 + ox, y1 + oy, x2 + ox, y2 + oy)


def offset_circle_radius(r: float, distance: float,
                         center: Point, side_point: Point) -> Optional[float]:
    """New radius: outward if the side point is outside, inward otherwise."""
    d = math.hypot(side_point[0] - center[0], side_point[1] - center[1])
    new_r = r + distance if d > r else r - distance
    return new_r if new_r > EPS else None


# -- FILLET --------------------------------------------------------------------

def fillet_corner(s1: Seg, s2: Seg) -> Optional[tuple[Seg, Seg]]:
    """Radius-0 fillet: trim/extend both lines to their intersection.

    Keeps each line's far end (the endpoint farther from the corner).
    """
    hit = line_line_intersection(s1, (s2[0], s2[1], s2[2], s2[3]), infinite2=True)
    if hit is None:
        return None
    # also require s2's line to actually reach the corner (infinite both)
    corner = hit[1]

    def keep_far(seg: Seg) -> Seg:
        d_start = math.hypot(seg[0] - corner[0], seg[1] - corner[1])
        d_end = math.hypot(seg[2] - corner[0], seg[3] - corner[1])
        if d_start >= d_end:
            return (seg[0], seg[1], corner[0], corner[1])
        return (corner[0], corner[1], seg[2], seg[3])

    return keep_far(s1), keep_far(s2)


def fillet_arc(s1: Seg, s2: Seg, radius: float):
    """Fillet arc between two lines: (center, r, a0_deg, a1_deg, t1, t2).

    t1/t2 are the tangent points on s1/s2 (the lines get trimmed there).
    Returns None for parallel lines or when the radius does not fit.
    """
    hit = line_line_intersection(s1, s2, infinite2=True)
    if hit is None:
        return None
    corner = hit[1]

    def unit_away(seg: Seg) -> tuple[float, float]:
        # direction from the corner toward the segment's farther endpoint
        d_start = math.hypot(seg[0] - corner[0], seg[1] - corner[1])
        far = (seg[0], seg[1]) if d_start >= math.hypot(
            seg[2] - corner[0], seg[3] - corner[1]) else (seg[2], seg[3])
        vx, vy = far[0] - corner[0], far[1] - corner[1]
        L = math.hypot(vx, vy)
        if L < EPS:
            raise ValueError("degenerate")
        return vx / L, vy / L

    try:
        u1 = unit_away(s1)
        u2 = unit_away(s2)
    except ValueError:
        return None
    cos2a = u1[0] * u2[0] + u1[1] * u2[1]
    angle = math.acos(max(-1.0, min(1.0, cos2a))) / 2.0
    if angle < EPS or abs(angle - math.pi / 2) < EPS:
        return None
    dist_along = radius / math.tan(angle)
    t1 = (corner[0] + u1[0] * dist_along, corner[1] + u1[1] * dist_along)
    t2 = (corner[0] + u2[0] * dist_along, corner[1] + u2[1] * dist_along)
    bx, by = u1[0] + u2[0], u1[1] + u2[1]
    bl = math.hypot(bx, by)
    if bl < EPS:
        return None
    center_dist = radius / math.sin(angle)
    cx = corner[0] + bx / bl * center_dist
    cy = corner[1] + by / bl * center_dist
    a1 = math.degrees(math.atan2(t1[1] - cy, t1[0] - cx))
    a2 = math.degrees(math.atan2(t2[1] - cy, t2[0] - cx))
    # arc sweeps the short way between the tangent points
    if (a2 - a1) % 360.0 > 180.0:
        a1, a2 = a2, a1
    return ((cx, cy), radius, a1, a2, t1, t2)
