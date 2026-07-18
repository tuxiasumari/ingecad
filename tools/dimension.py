# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Dimension tools: DIMLINEAR, DIMALIGNED, DIMRADIUS, DIMDIAMETER.

Each follows AutoCAD's prompt sequence and creates the dimension with the
current dimension style ($DIMSTYLE). Linear/aligned accept the two extension
points *or* Enter to select a single line/arc (AutoCAD's ``<select object>``),
and preview the dimension frame live as you place the dimension line.
"""
from __future__ import annotations

import math

from core import actions
from core.i18n import tr
from tools.base import Point, Tool


def _entity_endpoints(entity):
    """The two extension-line origins for a selected object, or None."""
    t = entity.dxftype()
    if t == "LINE":
        s, e = entity.dxf.start, entity.dxf.end
        return (s.x, s.y), (e.x, e.y)
    if t == "ARC":
        c, r = entity.dxf.center, entity.dxf.radius
        a0 = math.radians(entity.dxf.start_angle)
        a1 = math.radians(entity.dxf.end_angle)
        return ((c.x + r * math.cos(a0), c.y + r * math.sin(a0)),
                (c.x + r * math.cos(a1), c.y + r * math.sin(a1)))
    return None


class _TwoPointDim(Tool):
    """origin, second origin (or Enter -> select an object), then location."""

    def start(self) -> None:
        self._p1: Point | None = None
        self._p2: Point | None = None
        self._select_mode = False
        self.ctx.prompt(
            tr("Specify first extension line origin or <select object>:"))

    def _make(self, location: Point):
        raise NotImplementedError

    def _dim_preview(self, cursor: Point):
        raise NotImplementedError

    def on_enter(self) -> None:
        # Enter on the first prompt switches to AutoCAD's select-object mode.
        if self._p1 is None and not self._select_mode:
            self._select_mode = True
            self.entity_picker = True   # raw cursor for object picking
            self.ctx.prompt(tr("Select line or arc to dimension:"))
            return
        self.ctx.finish()

    def on_point(self, point: Point) -> None:
        if self._select_mode and self._p1 is None:
            e = self.ctx.services.pick_entity(point) if self.ctx.services else None
            ends = _entity_endpoints(e) if e is not None else None
            if ends is None:
                self.ctx.echo(tr("Select a line or arc."))
                return
            self._p1, self._p2 = ends
            self.entity_picker = False   # snap returns for the line location
            self.ctx.prompt(tr("Specify dimension line location:"))
            return
        if self._p1 is None:
            self._p1 = point
            self.last_point = point
            self.ctx.prompt(tr("Specify second extension line origin:"))
        elif self._p2 is None:
            self._p2 = point
            self.last_point = point
            self.ctx.prompt(tr("Specify dimension line location:"))
        else:
            self.ctx.execute(self._make(point))
            self.ctx.finish()

    def preview_segments(self, cursor: Point):
        if self._p1 is None:
            return []
        if self._p2 is None:
            return [(self._p1, cursor)]
        return self._dim_preview(cursor)


class DimLinearTool(_TwoPointDim):
    def start(self) -> None:
        self.name = "DIMLINEAR"
        super().start()

    def _orientation(self, cursor: Point) -> bool:
        mid = ((self._p1[0] + self._p2[0]) / 2.0,
               (self._p1[1] + self._p2[1]) / 2.0)
        return abs(cursor[1] - mid[1]) >= abs(cursor[0] - mid[0])  # horizontal?

    def _make(self, location: Point):
        return actions.dim_linear(self._p1, self._p2, location)

    def _dim_preview(self, cursor: Point):
        p1, p2 = self._p1, self._p2
        if self._orientation(cursor):
            y = cursor[1]
            d1, d2 = (p1[0], y), (p2[0], y)
        else:
            x = cursor[0]
            d1, d2 = (x, p1[1]), (x, p2[1])
        return [(p1, d1), (p2, d2), (d1, d2)]


class DimAlignedTool(_TwoPointDim):
    def start(self) -> None:
        self.name = "DIMALIGNED"
        super().start()

    def _make(self, location: Point):
        return actions.dim_aligned(self._p1, self._p2, location)

    def _dim_preview(self, cursor: Point):
        p1, p2 = self._p1, self._p2
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        dist = (cursor[0] - p1[0]) * nx + (cursor[1] - p1[1]) * ny
        d1 = (p1[0] + nx * dist, p1[1] + ny * dist)
        d2 = (p2[0] + nx * dist, p2[1] + ny * dist)
        return [(p1, d1), (p2, d2), (d1, d2)]


class _CurvedDim(Tool):
    """Select an arc/circle, then the dimension-line location."""

    entity_picker = True   # object picking suppresses osnap, AutoCAD-style

    def start(self) -> None:
        self._ent = None
        self.ctx.prompt(tr("Select arc or circle:"))

    def _make(self, center, radius, location):
        raise NotImplementedError

    def on_point(self, point: Point) -> None:
        if self._ent is None:
            e = self.ctx.services.pick_entity(point) if self.ctx.services else None
            if e is None or e.dxftype() not in ("CIRCLE", "ARC"):
                self.ctx.echo(tr("Select an arc or circle."))
                return
            self._ent = e
            self.ctx.prompt(tr("Specify dimension line location:"))
        else:
            c = self._ent.dxf.center
            self.ctx.execute(self._make((c.x, c.y), self._ent.dxf.radius, point))
            self.ctx.finish()

    def preview_segments(self, cursor: Point):
        if self._ent is None:
            return []
        c = self._ent.dxf.center
        return [((c.x, c.y), cursor)]


class DimRadiusTool(_CurvedDim):
    def start(self) -> None:
        self.name = "DIMRADIUS"
        super().start()

    def _make(self, center, radius, location):
        return actions.dim_radius(center, radius, location)


class DimDiameterTool(_CurvedDim):
    def start(self) -> None:
        self.name = "DIMDIAMETER"
        super().start()

    def _make(self, center, radius, location):
        return actions.dim_diameter(center, radius, location)


DIM_TOOL_CLASSES = {
    "DIMLINEAR": DimLinearTool,
    "DIMALIGNED": DimAlignedTool,
    "DIMRADIUS": DimRadiusTool,
    "DIMDIAMETER": DimDiameterTool,
}
