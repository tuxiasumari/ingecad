# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Object snaps (osnap) — the AutoCAD drawing "feel".

The engine extracts snappable geometry from the ezdxf modelspace into
NumPy arrays once (lazily, invalidated on edits), so each cursor move is a
vectorized query instead of an entity walk — a cadastre-sized drawing
stays interactive.

Supported: END, MID, CEN, NOD, INT, PER, NEA. Priorities follow AutoCAD:
an endpoint beats a nearby midpoint beats "nearest".
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

# Lower = wins when within threshold.
PRIORITY = {"END": 0, "INT": 1, "MID": 2, "CEN": 3, "NOD": 4, "PER": 5, "NEA": 6}
ALL_KINDS = frozenset(PRIORITY)


@dataclass(frozen=True)
class SnapHit:
    x: float
    y: float
    kind: str


class SnapEngine:
    """Snappable-geometry cache over a Document's modelspace."""

    def __init__(self, document) -> None:
        self.document = document
        self._dirty = True
        self._segs = np.empty((0, 4))     # x1 y1 x2 y2
        self._circles = np.empty((0, 3))  # cx cy r (full circles)
        self._arcs = np.empty((0, 5))     # cx cy r a0 a1 (ccw radians)
        self._points = np.empty((0, 2))   # NOD targets

    def invalidate(self) -> None:
        self._dirty = True

    # -- extraction -----------------------------------------------------------
    def _build(self) -> None:
        segs: list[tuple] = []
        circles: list[tuple] = []
        arcs: list[tuple] = []
        points: list[tuple] = []
        msp = self.document.modelspace()
        for e in msp:
            t = e.dxftype()
            try:
                if t == "LINE":
                    s, w = e.dxf.start, e.dxf.end
                    segs.append((s.x, s.y, w.x, w.y))
                elif t == "LWPOLYLINE":
                    pts = e.get_points("xy")
                    for a, b in zip(pts, pts[1:]):
                        segs.append((a[0], a[1], b[0], b[1]))
                    if e.closed and len(pts) > 2:
                        segs.append((pts[-1][0], pts[-1][1], pts[0][0], pts[0][1]))
                elif t == "CIRCLE":
                    c = e.dxf.center
                    circles.append((c.x, c.y, e.dxf.radius))
                elif t == "ARC":
                    c = e.dxf.center
                    a0 = math.radians(e.dxf.start_angle)
                    a1 = math.radians(e.dxf.end_angle)
                    if a1 <= a0:
                        a1 += math.tau
                    arcs.append((c.x, c.y, e.dxf.radius, a0, a1))
                elif t == "POINT":
                    l = e.dxf.location
                    points.append((l.x, l.y))
            except Exception:
                continue  # malformed entity: not snappable, not fatal
        self._segs = np.asarray(segs, dtype=np.float64).reshape(-1, 4)
        self._circles = np.asarray(circles, dtype=np.float64).reshape(-1, 3)
        self._arcs = np.asarray(arcs, dtype=np.float64).reshape(-1, 5)
        self._points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        self._dirty = False

    # -- query ----------------------------------------------------------------
    def find(
        self,
        cursor: tuple[float, float],
        threshold: float,
        kinds: frozenset[str] = ALL_KINDS,
        from_point: Optional[tuple[float, float]] = None,
    ) -> Optional[SnapHit]:
        """Best snap within ``threshold`` world units of the cursor.

        ``from_point`` anchors PER (perpendicular from the previous point).
        """
        if self._dirty:
            self._build()
        cx, cy = cursor
        best: Optional[tuple[int, float, SnapHit]] = None

        def offer(kind: str, x: float, y: float) -> None:
            nonlocal best
            d = math.hypot(x - cx, y - cy)
            if d > threshold:
                return
            key = (PRIORITY[kind], d)
            if best is None or key < (best[0], best[1]):
                best = (PRIORITY[kind], d, SnapHit(x, y, kind))

        segs, circles, arcs, points = (
            self._segs, self._circles, self._arcs, self._points)

        if "END" in kinds and len(segs):
            for exy in (segs[:, 0:2], segs[:, 2:4]):
                d2 = (exy[:, 0] - cx) ** 2 + (exy[:, 1] - cy) ** 2
                i = int(np.argmin(d2))
                offer("END", exy[i, 0], exy[i, 1])
        if "END" in kinds and len(arcs):
            for a_idx in (3, 4):
                ex = arcs[:, 0] + arcs[:, 2] * np.cos(arcs[:, a_idx])
                ey = arcs[:, 1] + arcs[:, 2] * np.sin(arcs[:, a_idx])
                d2 = (ex - cx) ** 2 + (ey - cy) ** 2
                i = int(np.argmin(d2))
                offer("END", float(ex[i]), float(ey[i]))
        if "MID" in kinds and len(segs):
            mx = (segs[:, 0] + segs[:, 2]) / 2.0
            my = (segs[:, 1] + segs[:, 3]) / 2.0
            d2 = (mx - cx) ** 2 + (my - cy) ** 2
            i = int(np.argmin(d2))
            offer("MID", float(mx[i]), float(my[i]))
        if "CEN" in kinds:
            for arr in (circles, arcs):
                if len(arr):
                    d2 = (arr[:, 0] - cx) ** 2 + (arr[:, 1] - cy) ** 2
                    i = int(np.argmin(d2))
                    offer("CEN", float(arr[i, 0]), float(arr[i, 1]))
        if "NOD" in kinds and len(points):
            d2 = (points[:, 0] - cx) ** 2 + (points[:, 1] - cy) ** 2
            i = int(np.argmin(d2))
            offer("NOD", float(points[i, 0]), float(points[i, 1]))

        near_idx = self._segs_near(cursor, threshold)
        if "INT" in kinds and len(near_idx) >= 2:
            for j, a in enumerate(near_idx):
                for b in near_idx[j + 1:]:
                    hit = _seg_intersection(segs[a], segs[b])
                    if hit is not None:
                        offer("INT", hit[0], hit[1])
        if "PER" in kinds and from_point is not None and len(near_idx):
            fx, fy = from_point
            for a in near_idx:
                p = _project_on_segment(segs[a], fx, fy)
                if p is not None:
                    offer("PER", p[0], p[1])
        if "NEA" in kinds:
            for a in near_idx:
                p = _closest_on_segment(segs[a], cx, cy)
                offer("NEA", p[0], p[1])
            for arr, full in ((circles, True), (arcs, False)):
                for row in arr:
                    p = _closest_on_circle(row, cx, cy, full)
                    if p is not None:
                        offer("NEA", p[0], p[1])

        return best[2] if best else None

    def _segs_near(self, cursor, threshold) -> np.ndarray:
        if not len(self._segs):
            return np.empty(0, dtype=int)
        cx, cy = cursor
        s = self._segs
        min_x = np.minimum(s[:, 0], s[:, 2]) - threshold
        max_x = np.maximum(s[:, 0], s[:, 2]) + threshold
        min_y = np.minimum(s[:, 1], s[:, 3]) - threshold
        max_y = np.maximum(s[:, 1], s[:, 3]) + threshold
        mask = (cx >= min_x) & (cx <= max_x) & (cy >= min_y) & (cy <= max_y)
        idx = np.nonzero(mask)[0]
        return idx[:64]  # dense crossings: cap the pairwise work


def _closest_on_segment(seg, px, py):
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return (x1 + t * dx, y1 + t * dy)


def _project_on_segment(seg, px, py):
    """Foot of the perpendicular, only when it lands inside the segment."""
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return None
    t = ((px - x1) * dx + (py - y1) * dy) / L2
    if t < 0.0 or t > 1.0:
        return None
    return (x1 + t * dx, y1 + t * dy)


def _closest_on_circle(row, px, py, full: bool):
    cx, cy, r = row[0], row[1], row[2]
    d = math.hypot(px - cx, py - cy)
    if d == 0:
        return None
    ang = math.atan2(py - cy, px - cx)
    if not full:
        a0, a1 = row[3], row[4]
        a = ang % math.tau
        if a < a0:
            a += math.tau
        if a > a1:
            return None
    return (cx + r * math.cos(ang), cy + r * math.sin(ang))


def _seg_intersection(s1, s2):
    """Intersection point of two segments, or None."""
    x1, y1, x2, y2 = s1
    x3, y3, x4, y4 = s2
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-12:
        return None
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None
