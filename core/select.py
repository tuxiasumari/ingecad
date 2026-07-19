# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Entity selection: pick box, window (fully inside), crossing (touching).

A GeometryIndex extracts pickable geometry per entity into NumPy arrays
(the same lazy strategy as the snap engine). Exotic entity types fall back
to their bounding box, so everything on screen is selectable even when we
do not understand its exact shape.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np

from ezdxf import bbox as ezbbox


class GeometryIndex:
    """Per-entity pick geometry over a Document's modelspace."""

    def __init__(self, document) -> None:
        self.document = document
        self._dirty = True
        self._segs = np.empty((0, 4))
        self._seg_owner: list[str] = []
        # cx cy r arc_flag a0 a1 (radians, ccw, a1 > a0; full circle: 0..tau)
        self._circles = np.empty((0, 6))
        self._circle_owner: list[str] = []
        self._boxes = np.empty((0, 4))     # min_x min_y max_x max_y per entity
        self._box_owner: list[str] = []

    def invalidate(self) -> None:
        self._dirty = True

    def entity(self, handle: str):
        return self.document.doc.entitydb.get(handle)

    @staticmethod
    def _extract(e, segs, seg_owner, circles, circle_owner,
                 boxes, box_owner) -> None:
        t = e.dxftype()
        h = e.dxf.handle
        try:
            if t == "LINE":
                s, w = e.dxf.start, e.dxf.end
                segs.append((s.x, s.y, w.x, w.y))
                seg_owner.append(h)
            elif t == "LWPOLYLINE":
                pts = e.get_points("xy")
                pairs = list(zip(pts, pts[1:]))
                if e.closed and len(pts) > 2:
                    pairs.append((pts[-1], pts[0]))
                for a, b in pairs:
                    segs.append((a[0], a[1], b[0], b[1]))
                    seg_owner.append(h)
            elif t == "CIRCLE":
                c = e.dxf.center
                circles.append((c.x, c.y, e.dxf.radius, 0.0, 0.0, math.tau))
                circle_owner.append(h)
            elif t == "ARC":
                c = e.dxf.center
                a0 = math.radians(e.dxf.start_angle) % math.tau
                a1 = math.radians(e.dxf.end_angle) % math.tau
                if a1 <= a0:
                    a1 += math.tau
                circles.append((c.x, c.y, e.dxf.radius, 1.0, a0, a1))
                circle_owner.append(h)
            elif t == "POINT":
                l = e.dxf.location
                segs.append((l.x, l.y, l.x, l.y))
                seg_owner.append(h)
            else:
                box = ezbbox.extents([e], fast=True)
                if box.has_data:
                    boxes.append((box.extmin.x, box.extmin.y,
                                  box.extmax.x, box.extmax.y))
                    box_owner.append(h)
        except Exception:
            pass

    def _build(self) -> None:
        segs, seg_owner = [], []
        circles, circle_owner = [], []
        boxes, box_owner = [], []
        for e in self.document.modelspace():
            self._extract(e, segs, seg_owner, circles, circle_owner,
                          boxes, box_owner)
        self._segs = np.asarray(segs, dtype=np.float64).reshape(-1, 4)
        self._seg_owner = seg_owner
        self._circles = np.asarray(circles, dtype=np.float64).reshape(-1, 6)
        self._circle_owner = circle_owner
        self._boxes = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
        self._box_owner = box_owner
        self._dirty = False

    def remove_handles(self, handles) -> None:
        """Drop the pick geometry of erased/modified entities (no rebuild).

        Modified entities are re-added via ``add_entities`` right after —
        the full rebuild pays ezdxf bbox extents for every exotic entity in
        the drawing (>1 s on a real 10k-entity plan) and used to freeze the
        first pick after every MOVE/TRIM. No-op while dirty.
        """
        if self._dirty:
            return
        dead = set(handles)
        if not dead:
            return
        for arr_name, owner_name in (("_segs", "_seg_owner"),
                                     ("_circles", "_circle_owner"),
                                     ("_boxes", "_box_owner")):
            owners = getattr(self, owner_name)
            if not owners:
                continue
            keep = np.fromiter((h not in dead for h in owners), bool,
                               len(owners))
            if not keep.all():
                setattr(self, arr_name, getattr(self, arr_name)[keep])
                setattr(self, owner_name,
                        [h for h, k in zip(owners, keep) if k])

    def add_entities(self, entities) -> None:
        """Append pick geometry of freshly added entities (no full rebuild).

        Additive edits (drawn segments, paste copies) stay O(new) instead of
        re-walking the whole modelspace. No-op while dirty: the pending
        rebuild includes them anyway.
        """
        if self._dirty:
            return
        segs, seg_owner = [], []
        circles, circle_owner = [], []
        boxes, box_owner = [], []
        for e in entities:
            self._extract(e, segs, seg_owner, circles, circle_owner,
                          boxes, box_owner)
        if segs:
            self._segs = np.vstack(
                [self._segs, np.asarray(segs, dtype=np.float64).reshape(-1, 4)])
            self._seg_owner.extend(seg_owner)
        if circles:
            self._circles = np.vstack(
                [self._circles,
                 np.asarray(circles, dtype=np.float64).reshape(-1, 6)])
            self._circle_owner.extend(circle_owner)
        if boxes:
            self._boxes = np.vstack(
                [self._boxes,
                 np.asarray(boxes, dtype=np.float64).reshape(-1, 4)])
            self._box_owner.extend(box_owner)

    # -- queries --------------------------------------------------------------
    def pick(self, cursor: tuple[float, float], tolerance: float) -> Optional[str]:
        """Handle of the closest entity within ``tolerance`` of the cursor."""
        if self._dirty:
            self._build()
        cx, cy = cursor
        best: Optional[tuple[float, str]] = None

        if len(self._segs):
            d = _dist_point_segments(self._segs, cx, cy)
            i = int(np.argmin(d))
            if d[i] <= tolerance:
                best = (float(d[i]), self._seg_owner[i])
        if len(self._circles):
            c = self._circles
            dc = np.hypot(c[:, 0] - cx, c[:, 1] - cy)
            d = np.abs(dc - c[:, 2])
            # arcs only count when the cursor angle falls inside their sweep
            ang = np.arctan2(cy - c[:, 1], cx - c[:, 0]) % math.tau
            rel = (ang - c[:, 4]) % math.tau
            on_span = (c[:, 3] == 0.0) | (rel <= (c[:, 5] - c[:, 4]))
            d = np.where(on_span, d, np.inf)
            i = int(np.argmin(d))
            if d[i] <= tolerance and (best is None or d[i] < best[0]):
                best = (float(d[i]), self._circle_owner[i])
        if len(self._boxes) and best is None:
            b = self._boxes
            inside = ((cx >= b[:, 0] - tolerance) & (cx <= b[:, 2] + tolerance)
                      & (cy >= b[:, 1] - tolerance) & (cy <= b[:, 3] + tolerance))
            hits = np.nonzero(inside)[0]
            if len(hits):
                areas = ((b[hits, 2] - b[hits, 0]) * (b[hits, 3] - b[hits, 1]))
                best = (tolerance, self._box_owner[hits[int(np.argmin(areas))]])
        return best[1] if best else None

    def window(self, rect: tuple[float, float, float, float]) -> list[str]:
        """Entities FULLY inside the rect (left-to-right blue window)."""
        if self._dirty:
            self._build()
        x0, y0, x1, y1 = rect
        inside: dict[str, bool] = {}

        def clamp(owner: str, ok: bool) -> None:
            inside[owner] = inside.get(owner, True) and ok

        for i, s in enumerate(self._segs):
            ok = (min(s[0], s[2]) >= x0 and max(s[0], s[2]) <= x1
                  and min(s[1], s[3]) >= y0 and max(s[1], s[3]) <= y1)
            clamp(self._seg_owner[i], ok)
        for i, c in enumerate(self._circles):
            ok = (c[0] - c[2] >= x0 and c[0] + c[2] <= x1
                  and c[1] - c[2] >= y0 and c[1] + c[2] <= y1)
            clamp(self._circle_owner[i], ok)
        for i, b in enumerate(self._boxes):
            ok = b[0] >= x0 and b[2] <= x1 and b[1] >= y0 and b[3] <= y1
            clamp(self._box_owner[i], ok)
        return [h for h, ok in inside.items() if ok]

    def crossing(self, rect: tuple[float, float, float, float]) -> list[str]:
        """Entities touching the rect (right-to-left green crossing)."""
        if self._dirty:
            self._build()
        x0, y0, x1, y1 = rect
        hit: set[str] = set()
        for i, s in enumerate(self._segs):
            if _seg_intersects_rect(s, x0, y0, x1, y1):
                hit.add(self._seg_owner[i])
        for i, c in enumerate(self._circles):
            if _circle_intersects_rect(c, x0, y0, x1, y1):
                hit.add(self._circle_owner[i])
        for i, b in enumerate(self._boxes):
            if not (b[2] < x0 or b[0] > x1 or b[3] < y0 or b[1] > y1):
                hit.add(self._box_owner[i])
        return sorted(hit)

    def segments_of(self, handles: Iterable[str]) -> np.ndarray:
        """Pick segments of the given entities (for highlight drawing)."""
        if self._dirty:
            self._build()
        wanted = set(handles)
        rows = [i for i, h in enumerate(self._seg_owner) if h in wanted]
        return self._segs[rows] if rows else np.empty((0, 4))

    def circles_of(self, handles: Iterable[str]) -> np.ndarray:
        if self._dirty:
            self._build()
        wanted = set(handles)
        rows = [i for i, h in enumerate(self._circle_owner) if h in wanted]
        return self._circles[rows] if rows else np.empty((0, 4))

    def boxes_of(self, handles: Iterable[str]) -> np.ndarray:
        if self._dirty:
            self._build()
        wanted = set(handles)
        rows = [i for i, h in enumerate(self._box_owner) if h in wanted]
        return self._boxes[rows] if rows else np.empty((0, 4))


def _dist_point_segments(segs: np.ndarray, px: float, py: float) -> np.ndarray:
    dx = segs[:, 2] - segs[:, 0]
    dy = segs[:, 3] - segs[:, 1]
    L2 = dx * dx + dy * dy
    L2s = np.where(L2 == 0.0, 1.0, L2)
    t = ((px - segs[:, 0]) * dx + (py - segs[:, 1]) * dy) / L2s
    t = np.clip(np.where(L2 == 0.0, 0.0, t), 0.0, 1.0)
    qx = segs[:, 0] + t * dx
    qy = segs[:, 1] + t * dy
    return np.hypot(px - qx, py - qy)


def _seg_intersects_rect(s, x0, y0, x1, y1) -> bool:
    if max(s[0], s[2]) < x0 or min(s[0], s[2]) > x1:
        return False
    if max(s[1], s[3]) < y0 or min(s[1], s[3]) > y1:
        return False
    # endpoint inside?
    for px, py in ((s[0], s[1]), (s[2], s[3])):
        if x0 <= px <= x1 and y0 <= py <= y1:
            return True
    # crosses any rect edge?
    edges = ((x0, y0, x1, y0), (x1, y0, x1, y1),
             (x1, y1, x0, y1), (x0, y1, x0, y0))
    for e in edges:
        if _segments_cross(s, e):
            return True
    return False


def _segments_cross(s1, s2) -> bool:
    x1, y1, x2, y2 = s1
    x3, y3, x4, y4 = s2
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-15:
        return False
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    return 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0


def _circle_intersects_rect(c, x0, y0, x1, y1) -> bool:
    cx, cy, r = c[0], c[1], c[2]
    # closest point of rect to center within r AND rect not fully inside circle
    qx = min(max(cx, x0), x1)
    qy = min(max(cy, y0), y1)
    if math.hypot(cx - qx, cy - qy) > r:
        return False
    # if all four corners are inside the circle, the circle does not touch
    # the rect boundary (rect fully inside circle: crossing should still
    # select it? AutoCAD: crossing selects if the curve crosses the window
    # OR is inside; a rect inside the circle does not touch the curve).
    corners_in = all(math.hypot(cx - X, cy - Y) < r
                     for X in (x0, x1) for Y in (y0, y1))
    center_in = x0 <= cx <= x1 and y0 <= cy <= y1
    if corners_in and not center_in:
        return False
    return True


def entity_grips(entity) -> list[tuple[float, float, str]]:
    """Grip points of an entity: (x, y, role).

    Roles drive editing: 'end'/'mid'/'vertex' move that point, 'center'
    moves the whole entity, 'radius'/'quadrant' resize. Mirrors AutoCAD's
    grip set for the supported types.
    """
    import math

    t = entity.dxftype()
    grips: list[tuple[float, float, str]] = []
    if t == "LINE":
        s, e = entity.dxf.start, entity.dxf.end
        grips.append((s.x, s.y, "end"))
        grips.append(((s.x + e.x) / 2, (s.y + e.y) / 2, "mid"))
        grips.append((e.x, e.y, "end"))
    elif t == "LWPOLYLINE":
        pts = entity.get_points("xy")
        for x, y in pts:
            grips.append((x, y, "vertex"))
        pairs = list(zip(pts, pts[1:]))
        if entity.closed and len(pts) > 2:
            pairs.append((pts[-1], pts[0]))
        for a, b in pairs:
            grips.append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, "mid"))
    elif t == "CIRCLE":
        c, r = entity.dxf.center, entity.dxf.radius
        grips.append((c.x, c.y, "center"))
        for ang in (0, 90, 180, 270):
            grips.append((c.x + r * math.cos(math.radians(ang)),
                          c.y + r * math.sin(math.radians(ang)), "quadrant"))
    elif t == "ARC":
        c, r = entity.dxf.center, entity.dxf.radius
        grips.append((c.x, c.y, "center"))
        for a in (entity.dxf.start_angle, entity.dxf.end_angle):
            grips.append((c.x + r * math.cos(math.radians(a)),
                          c.y + r * math.sin(math.radians(a)), "end"))
        mid = math.radians((entity.dxf.start_angle + entity.dxf.end_angle) / 2)
        grips.append((c.x + r * math.cos(mid), c.y + r * math.sin(mid), "mid"))
    elif t == "POINT":
        l = entity.dxf.location
        grips.append((l.x, l.y, "center"))
    return grips


def apply_grip_edit(entity, grip_index: int, role: str, new_point):
    """Move the grip at ``grip_index`` to ``new_point``, editing the entity
    in place. Returns True on success (undo is handled by the caller through
    a snapshot Command)."""
    import math

    t = entity.dxftype()
    nx, ny = new_point
    if t == "LINE":
        if role == "mid":               # move whole line
            s, e = entity.dxf.start, entity.dxf.end
            dx = nx - (s.x + e.x) / 2
            dy = ny - (s.y + e.y) / 2
            entity.dxf.start = (s.x + dx, s.y + dy, 0)
            entity.dxf.end = (e.x + dx, e.y + dy, 0)
        elif grip_index == 0:
            entity.dxf.start = (nx, ny, 0)
        else:
            entity.dxf.end = (nx, ny, 0)
        return True
    if t == "LWPOLYLINE":
        pts = entity.get_points("xyseb")
        n = len(pts)
        if role == "vertex" and grip_index < n:
            p = list(pts[grip_index])
            p[0], p[1] = nx, ny
            pts[grip_index] = tuple(p)
            entity.set_points(pts, format="xyseb")
            return True
        if role == "mid":
            # AutoCAD/BricsCAD: the midpoint (triangle) grip MOVES the whole
            # segment — it translates both its endpoints by the drag delta,
            # keeping the segment straight; adjacent segments stretch to
            # follow. No vertex is inserted.
            seg = grip_index - n
            a, b = seg, (seg + 1) % n if entity.closed else seg + 1
            if b >= len(pts):
                return False
            mid_x = (pts[a][0] + pts[b][0]) / 2.0
            mid_y = (pts[a][1] + pts[b][1]) / 2.0
            dx, dy = nx - mid_x, ny - mid_y
            for idx in (a, b):
                p = list(pts[idx])
                p[0] += dx
                p[1] += dy
                pts[idx] = tuple(p)
            entity.set_points(pts, format="xyseb")
            return True
        return False
    if t == "CIRCLE":
        if role == "center":
            entity.dxf.center = (nx, ny, 0)
        else:                            # quadrant: new radius
            c = entity.dxf.center
            entity.dxf.radius = max(1e-9, math.hypot(nx - c.x, ny - c.y))
        return True
    if t == "ARC":
        c = entity.dxf.center
        if role == "center":
            entity.dxf.center = (nx, ny, 0)
        elif role == "mid":              # new radius, angles kept
            entity.dxf.radius = max(1e-9, math.hypot(nx - c.x, ny - c.y))
        else:                            # end grip: move that angle
            ang = math.degrees(math.atan2(ny - c.y, nx - c.x))
            if grip_index == 1:
                entity.dxf.start_angle = ang
            else:
                entity.dxf.end_angle = ang
        return True
    if t == "POINT":
        entity.dxf.location = (nx, ny, 0)
        return True
    return False
