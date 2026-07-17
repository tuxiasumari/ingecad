# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Editing tools: ERASE, MOVE, COPY, ROTATE, SCALE, MIRROR, OFFSET,
TRIM, EXTEND, FILLET.

Noun-verb (preselect then command) and verb-noun (command then "Select
objects:") both work — the controller feeds selections through
``on_selection``. TRIM/EXTEND honor Shift as the modern AutoCAD toggle.
"""
from __future__ import annotations

import math

from core import actions, editmath
from core.i18n import tr
from tools.base import Point, Tool


class EraseTool(Tool):
    wants_selection = True

    def start(self) -> None:
        self.name = "ERASE"

    def on_selection(self, entities: list) -> None:
        if entities:
            self.ctx.execute(actions.EraseCommand(entities))
            self.ctx.echo(tr("{count} erased.", count=len(entities)))
        self.ctx.finish()


class MoveTool(Tool):
    wants_selection = True

    def start(self) -> None:
        self.name = "MOVE"
        self._entities: list = []
        self._base: Point | None = None

    def on_selection(self, entities: list) -> None:
        if not entities:
            self.ctx.finish()
            return
        self._entities = entities
        self.ctx.prompt(tr("Specify base point:"))

    def on_point(self, point: Point) -> None:
        if self._base is None:
            self._base = point
            self.last_point = point
            self.ctx.prompt(tr("Specify second point:"))
        else:
            dx, dy = point[0] - self._base[0], point[1] - self._base[1]
            self.ctx.execute(actions.move_entities(self._entities, dx, dy))
            self.ctx.finish()

    def preview_segments(self, cursor: Point):
        return [(self._base, cursor)] if self._base else []


class CopyTool(Tool):
    wants_selection = True

    def start(self) -> None:
        self.name = "COPY"
        self._entities: list = []
        self._base: Point | None = None

    def on_selection(self, entities: list) -> None:
        if not entities:
            self.ctx.finish()
            return
        self._entities = entities
        self.ctx.prompt(tr("Specify base point:"))

    def on_point(self, point: Point) -> None:
        if self._base is None:
            self._base = point
            self.last_point = point
            self.ctx.prompt(tr("Specify second point (multiple; Enter ends):"))
        else:
            dx, dy = point[0] - self._base[0], point[1] - self._base[1]
            self.ctx.execute(actions.copy_entities(self._entities, dx, dy))
            # AutoCAD COPY stays active for multiple placements.

    def preview_segments(self, cursor: Point):
        return [(self._base, cursor)] if self._base else []


class RotateTool(Tool):
    wants_selection = True

    def start(self) -> None:
        self.name = "ROTATE"
        self._entities: list = []
        self._base: Point | None = None
        self._reference: float | None = None
        self._ref_first: Point | None = None

    def on_selection(self, entities: list) -> None:
        if not entities:
            self.ctx.finish()
            return
        self._entities = entities
        self.ctx.prompt(tr("Specify base point:"))

    def on_option(self, text: str) -> bool:
        t = text.upper()
        if t in ("R", "REFERENCE") and self._base is not None:
            self._reference = -1.0  # waiting for reference angle
            self.ctx.prompt(tr("Specify reference angle (two points or typed):"))
            return True
        # typed angle
        if self._base is not None:
            try:
                angle = float(text)
            except ValueError:
                return False
            if self._reference == -1.0:
                self._reference = angle
                self.ctx.prompt(tr("Specify new angle:"))
            elif self._reference is not None:
                self.ctx.execute(actions.rotate_entities(
                    self._entities, self._base, angle - self._reference))
                self.ctx.finish()
            else:
                self.ctx.execute(actions.rotate_entities(
                    self._entities, self._base, angle))
                self.ctx.finish()
            return True
        return False

    def on_point(self, point: Point) -> None:
        if self._base is None:
            self._base = point
            self.last_point = point
            self.ctx.prompt(tr("Specify rotation angle or [Reference]:"))
            return
        ang = math.degrees(math.atan2(point[1] - self._base[1],
                                      point[0] - self._base[0]))
        if self._reference == -1.0:
            if self._ref_first is None:
                self._ref_first = point
                self.ctx.prompt(tr("Specify second point of reference angle:"))
                return
            self._reference = math.degrees(math.atan2(
                point[1] - self._ref_first[1], point[0] - self._ref_first[0]))
            self.ctx.prompt(tr("Specify new angle:"))
            return
        if self._reference is not None:
            ang -= self._reference
        self.ctx.execute(actions.rotate_entities(self._entities, self._base, ang))
        self.ctx.finish()

    def preview_segments(self, cursor: Point):
        return [(self._base, cursor)] if self._base else []


class ScaleTool(Tool):
    wants_selection = True

    def start(self) -> None:
        self.name = "SCALE"
        self._entities: list = []
        self._base: Point | None = None
        self._ref_length: float | None = None

    def on_selection(self, entities: list) -> None:
        if not entities:
            self.ctx.finish()
            return
        self._entities = entities
        self.ctx.prompt(tr("Specify base point:"))

    def on_option(self, text: str) -> bool:
        t = text.upper()
        if t in ("R", "REFERENCE") and self._base is not None:
            self._ref_length = -1.0
            self.ctx.prompt(tr("Specify reference length:"))
            return True
        if self._base is not None:
            try:
                value = float(text)
            except ValueError:
                return False
            if value <= 0:
                self.ctx.echo(tr("Value must be positive."))
                return True
            if self._ref_length == -1.0:
                self._ref_length = value
                self.ctx.prompt(tr("Specify new length:"))
            elif self._ref_length is not None:
                self.ctx.execute(actions.scale_entities(
                    self._entities, self._base, value / self._ref_length))
                self.ctx.finish()
            else:
                self.ctx.execute(actions.scale_entities(
                    self._entities, self._base, value))
                self.ctx.finish()
            return True
        return False

    def on_point(self, point: Point) -> None:
        if self._base is None:
            self._base = point
            self.last_point = point
            self.ctx.prompt(tr("Specify scale factor or [Reference]:"))

    def preview_segments(self, cursor: Point):
        return [(self._base, cursor)] if self._base else []


class MirrorTool(Tool):
    wants_selection = True

    def start(self) -> None:
        self.name = "MIRROR"
        self._entities: list = []
        self._p1: Point | None = None
        self._p2: Point | None = None

    def on_selection(self, entities: list) -> None:
        if not entities:
            self.ctx.finish()
            return
        self._entities = entities
        self.ctx.prompt(tr("Specify first point of mirror line:"))

    def on_point(self, point: Point) -> None:
        if self._p1 is None:
            self._p1 = point
            self.last_point = point
            self.ctx.prompt(tr("Specify second point of mirror line:"))
        elif self._p2 is None:
            self._p2 = point
            self.ctx.prompt(tr("Erase source objects? [Yes/No] <N>:"))

    def on_option(self, text: str) -> bool:
        if self._p2 is None:
            return False
        t = text.upper()
        if t in ("Y", "YES", "S", "SI"):
            self.ctx.execute(actions.mirror_entities(
                self._entities, self._p1, self._p2, keep_source=False))
            self.ctx.finish()
            return True
        if t in ("N", "NO", ""):
            self.ctx.execute(actions.mirror_entities(
                self._entities, self._p1, self._p2, keep_source=True))
            self.ctx.finish()
            return True
        return False

    def on_enter(self) -> None:
        if self._p2 is not None:
            self.on_option("N")
        else:
            self.ctx.finish()

    def preview_segments(self, cursor: Point):
        return [(self._p1, cursor)] if self._p1 and self._p2 is None else []


class OffsetTool(Tool):
    entity_picker = True

    def start(self) -> None:
        self.name = "OFFSET"
        self._distance: float | None = None
        self._entity = None
        self.ctx.prompt(tr("Specify offset distance:"))

    def on_option(self, text: str) -> bool:
        if self._distance is None:
            try:
                d = float(text)
            except ValueError:
                return False
            if d <= 0:
                self.ctx.echo(tr("Value must be positive."))
                return True
            self._distance = d
            self.ctx.prompt(tr("Select object to offset (Enter ends):"))
            return True
        return False

    def on_point(self, point: Point) -> None:
        if self._distance is None:
            return
        if self._entity is None:
            entity = self.ctx.services.pick_entity(point)
            if entity is None:
                self.ctx.echo(tr("Nothing there."))
                return
            if entity.dxftype() not in ("LINE", "CIRCLE", "ARC"):
                self.ctx.echo(tr("OFFSET supports LINE, CIRCLE and ARC for now."))
                return
            self._entity = entity
            self.ctx.prompt(tr("Specify side to offset:"))
            return
        e, side = self._entity, point
        self._entity = None
        t = e.dxftype()
        if t == "LINE":
            seg = (e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y)
            n = editmath.offset_line(seg, self._distance, side)
            self.ctx.execute(actions.add_line((n[0], n[1]), (n[2], n[3])))
        else:
            center = (e.dxf.center.x, e.dxf.center.y)
            new_r = editmath.offset_circle_radius(
                e.dxf.radius, self._distance, center, side)
            if new_r is None:
                self.ctx.echo(tr("Radius would vanish."))
            elif t == "CIRCLE":
                self.ctx.execute(actions.add_circle(center, new_r))
            else:
                a0, a1 = e.dxf.start_angle, e.dxf.end_angle
                self.ctx.execute(actions.AddEntityCommand(
                    "OFFSET",
                    lambda msp, c=center, r=new_r, s=a0, en=a1:
                        msp.add_arc(c, r, s, en)))
        self.ctx.prompt(tr("Select object to offset (Enter ends):"))


class _TrimExtendBase(Tool):
    wants_selection = True   # the cutting/boundary edges
    entity_picker = True
    accepts_target_windows = True
    trim_mode = True

    def start(self) -> None:
        self._edges_handles: list[str] | None = None

    def selection_prompt(self) -> str:
        return (tr("Select cutting edges <Enter selects all>:") if self.trim_mode
                else tr("Select boundary edges <Enter selects all>:"))

    def on_selection(self, entities: list) -> None:
        # Enter with empty selection = all entities are edges (modern AutoCAD)
        self._edges_handles = [e.dxf.handle for e in entities] or None
        self.ctx.prompt(
            tr("Select object to trim (Shift extends):") if self.trim_mode
            else tr("Select object to extend (Shift trims):"))

    def on_point(self, point: Point) -> None:
        entity = self.ctx.services.pick_entity(point)
        if entity is None:
            self.ctx.echo(tr("Nothing there."))
            return
        self.apply_to_entity(entity, point)

    def on_target_entities(self, entities: list, rect) -> None:
        """Window/crossing over targets: trim each near the rect center."""
        cx = (rect[0] + rect[2]) / 2.0
        cy = (rect[1] + rect[3]) / 2.0
        for entity in entities:
            point = _point_on_entity_near(entity, (cx, cy))
            if point is not None:
                self.apply_to_entity(entity, point)

    def apply_to_entity(self, entity, point: Point) -> None:
        if not entity.is_alive:
            return
        segs, circles = self.ctx.services.edges_geometry(
            self._edges_handles, exclude=entity.dxf.handle)
        trim = self.trim_mode != self.shift  # Shift flips the mode
        if trim:
            self._trim(entity, point, segs, circles)
        else:
            self._extend(entity, point, segs, circles)

    def _replace(self, name: str, entity, factories) -> None:
        """Execute the swap and keep the edge list alive across it.

        A trimmed cutting edge keeps cutting in AutoCAD: when the replaced
        entity was one of our edges, its surviving pieces take its place.
        """
        cmd = actions.ReplaceEntitiesCommand(name, [entity], factories)
        self.ctx.execute(cmd)
        if self._edges_handles is not None:
            handle = None
            for e in cmd.old_entities:
                handle = e.dxf.handle
                if handle in self._edges_handles:
                    self._edges_handles.remove(handle)
                    self._edges_handles.extend(
                        n.dxf.handle for n in cmd.new_entities)

    def _trim(self, entity, point, segs, circles) -> None:
        t = entity.dxftype()
        if t == "LINE":
            seg = (entity.dxf.start.x, entity.dxf.start.y,
                   entity.dxf.end.x, entity.dxf.end.y)
            pick_t = _param_on_segment(seg, point)
            pieces = editmath.trim_segment(seg, segs, circles, pick_t)
            if pieces is None:
                self.ctx.echo(tr("No cutting edge crosses it."))
                return
            factories = [
                (lambda msp, p=p: msp.add_line((p[0], p[1]), (p[2], p[3])))
                for p in pieces
            ]
            self._replace("TRIM", entity, factories)
        elif t == "CIRCLE":
            center = (entity.dxf.center.x, entity.dxf.center.y)
            pick_ang = math.atan2(point[1] - center[1], point[0] - center[0])
            arc = editmath.trim_circle(center, entity.dxf.radius, segs, pick_ang,
                                       cutter_circles=circles)
            if arc is None:
                self.ctx.echo(tr("A circle needs two crossings to trim."))
                return
            a0, a1 = arc
            self._replace("TRIM", entity,
                          [lambda msp, c=center, r=entity.dxf.radius,
                                  s=a0, e=a1: msp.add_arc(c, r, s, e)])
        elif t == "ARC":
            center = (entity.dxf.center.x, entity.dxf.center.y)
            pick_ang = math.atan2(point[1] - center[1], point[0] - center[0])
            spans = editmath.trim_arc(
                center, entity.dxf.radius, entity.dxf.start_angle,
                entity.dxf.end_angle, segs, pick_ang, cutter_circles=circles)
            if spans is None:
                self.ctx.echo(tr("No cutting edge crosses it."))
                return
            factories = [
                (lambda msp, c=center, r=entity.dxf.radius, s=s0, e=e0:
                     msp.add_arc(c, r, s, e))
                for s0, e0 in spans
            ]
            self._replace("TRIM", entity, factories)
        elif t == "LWPOLYLINE":
            pts = entity.get_points("xyb")
            if any(abs(p[2]) > 1e-12 for p in pts):
                self.ctx.echo(tr("Curved polyline segments not supported yet."))
                return
            chains = editmath.trim_polyline(
                [(p[0], p[1]) for p in pts], entity.closed, point,
                segs, circles)
            if chains is None:
                self.ctx.echo(tr("No cutting edge crosses it."))
                return
            factories = [
                (lambda msp, c=chain: msp.add_lwpolyline(c))
                for chain in chains
            ]
            self._replace("TRIM", entity, factories)
        else:
            self.ctx.echo(tr("TRIM supports LINE, PLINE, CIRCLE and ARC for now."))

    def _extend(self, entity, point, segs, circles) -> None:
        t = entity.dxftype()
        if t == "LINE":
            seg = (entity.dxf.start.x, entity.dxf.start.y,
                   entity.dxf.end.x, entity.dxf.end.y)
            pick_t = _param_on_segment(seg, point)
            new_seg = editmath.extend_segment(seg, segs, circles, pick_t)
            if new_seg is None:
                self.ctx.echo(tr("No boundary edge to extend to."))
                return
            self._replace("EXTEND", entity,
                          [lambda msp, p=new_seg:
                               msp.add_line((p[0], p[1]), (p[2], p[3]))])
        elif t == "LWPOLYLINE":
            pts = entity.get_points("xyb")
            if any(abs(p[2]) > 1e-12 for p in pts):
                self.ctx.echo(tr("Curved polyline segments not supported yet."))
                return
            if entity.closed:
                self.ctx.echo(tr("A closed polyline cannot be extended."))
                return
            new_pts = editmath.extend_polyline(
                [(p[0], p[1]) for p in pts], False, point, segs, circles)
            if new_pts is None:
                self.ctx.echo(tr("No boundary edge to extend to."))
                return
            self._replace("EXTEND", entity,
                          [lambda msp, c=new_pts: msp.add_lwpolyline(c)])
        else:
            self.ctx.echo(tr("EXTEND supports LINE and PLINE for now."))


class TrimTool(_TrimExtendBase):
    trim_mode = True

    def start(self) -> None:
        super().start()
        self.name = "TRIM"


class ExtendTool(_TrimExtendBase):
    trim_mode = False

    def start(self) -> None:
        super().start()
        self.name = "EXTEND"


class FilletTool(Tool):
    entity_picker = True
    radius = 0.0  # session-sticky, AutoCAD-style

    def start(self) -> None:
        self.name = "FILLET"
        self._first = None
        self.ctx.prompt(tr("FILLET (radius {radius}) select first line or [Radius]:",
                           radius=type(self).radius))

    def on_option(self, text: str) -> bool:
        t = text.upper()
        if t in ("R", "RADIUS"):
            self.ctx.prompt(tr("Specify fillet radius:"))
            self._waiting_radius = True
            return True
        if getattr(self, "_waiting_radius", False):
            try:
                r = float(text)
            except ValueError:
                return False
            if r < 0:
                self.ctx.echo(tr("Value must be positive."))
                return True
            type(self).radius = r
            self._waiting_radius = False
            self.ctx.prompt(tr("Select first line:"))
            return True
        return False

    def on_point(self, point: Point) -> None:
        entity = self.ctx.services.pick_entity(point)
        if entity is None or entity.dxftype() != "LINE":
            self.ctx.echo(tr("FILLET supports LINE pairs for now."))
            return
        if self._first is None:
            self._first = entity
            self.ctx.prompt(tr("Select second line:"))
            return
        if entity is self._first:
            self.ctx.echo(tr("Pick a different line."))
            return
        s1 = (self._first.dxf.start.x, self._first.dxf.start.y,
              self._first.dxf.end.x, self._first.dxf.end.y)
        s2 = (entity.dxf.start.x, entity.dxf.start.y,
              entity.dxf.end.x, entity.dxf.end.y)
        r = type(self).radius
        if r == 0:
            result = editmath.fillet_corner(s1, s2)
            if result is None:
                self.ctx.echo(tr("Lines are parallel."))
                self.ctx.finish()
                return
            n1, n2 = result
            factories = [
                lambda msp, p=n1: msp.add_line((p[0], p[1]), (p[2], p[3])),
                lambda msp, p=n2: msp.add_line((p[0], p[1]), (p[2], p[3])),
            ]
        else:
            result = editmath.fillet_arc(s1, s2, r)
            if result is None:
                self.ctx.echo(tr("Radius does not fit."))
                self.ctx.finish()
                return
            center, radius, a0, a1, t1, t2 = result
            corner = editmath.line_line_intersection(s1, s2, infinite2=True)[1]

            def far_piece(seg, tangent):
                d_start = math.hypot(seg[0] - corner[0], seg[1] - corner[1])
                d_end = math.hypot(seg[2] - corner[0], seg[3] - corner[1])
                far = (seg[0], seg[1]) if d_start >= d_end else (seg[2], seg[3])
                return (far[0], far[1], tangent[0], tangent[1])

            n1 = far_piece(s1, t1)
            n2 = far_piece(s2, t2)
            factories = [
                lambda msp, p=n1: msp.add_line((p[0], p[1]), (p[2], p[3])),
                lambda msp, p=n2: msp.add_line((p[0], p[1]), (p[2], p[3])),
                lambda msp, c=center, rr=radius, s=a0, e=a1:
                    msp.add_arc(c, rr, s, e),
            ]
        self.ctx.execute(actions.ReplaceEntitiesCommand(
            "FILLET", [self._first, entity], factories))
        self.ctx.finish()


def _point_on_entity_near(entity, target: Point):
    """Closest point ON the entity to a target point (window-trim picks)."""
    t = entity.dxftype()
    if t == "LINE":
        seg = (entity.dxf.start.x, entity.dxf.start.y,
               entity.dxf.end.x, entity.dxf.end.y)
        u = _param_on_segment(seg, target)
        return (seg[0] + u * (seg[2] - seg[0]), seg[1] + u * (seg[3] - seg[1]))
    if t in ("CIRCLE", "ARC"):
        c = entity.dxf.center
        ang = math.atan2(target[1] - c.y, target[0] - c.x)
        if t == "ARC":
            a0 = math.radians(entity.dxf.start_angle) % math.tau
            a1 = math.radians(entity.dxf.end_angle) % math.tau
            if a1 <= a0:
                a1 += math.tau
            rel = (ang - a0) % math.tau
            if rel > (a1 - a0):
                # clamp to the nearest arc end
                ang = a0 if rel - (a1 - a0) > (math.tau - rel) else a1
        r = entity.dxf.radius
        return (c.x + r * math.cos(ang), c.y + r * math.sin(ang))
    if t == "LWPOLYLINE":
        pts = entity.get_points("xy")
        pairs = list(zip(pts, pts[1:]))
        if entity.closed and len(pts) > 2:
            pairs.append((pts[-1], pts[0]))
        best = None
        for a, b in pairs:
            seg = (a[0], a[1], b[0], b[1])
            u = _param_on_segment(seg, target)
            q = (seg[0] + u * (seg[2] - seg[0]), seg[1] + u * (seg[3] - seg[1]))
            d = math.hypot(q[0] - target[0], q[1] - target[1])
            if best is None or d < best[0]:
                best = (d, q)
        return best[1] if best else None
    return None


def _param_on_segment(seg, point) -> float:
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return 0.0
    return max(0.0, min(1.0, ((point[0] - x1) * dx + (point[1] - y1) * dy) / L2))


EDIT_TOOL_CLASSES = {
    "ERASE": EraseTool,
    "MOVE": MoveTool,
    "COPY": CopyTool,
    "ROTATE": RotateTool,
    "SCALE": ScaleTool,
    "MIRROR": MirrorTool,
    "OFFSET": OffsetTool,
    "TRIM": TrimTool,
    "EXTEND": ExtendTool,
    "FILLET": FilletTool,
}
