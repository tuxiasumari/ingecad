# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Drawing tools: LINE, CIRCLE, ARC, PLINE, RECTANG, POLYGON.

Prompt wording mirrors AutoCAD so the muscle memory transfers; every
mutation goes through core.actions Commands (exact undo).
"""
from __future__ import annotations

import math

from core import actions
from core.i18n import tr
from tools.base import Point, Tool


def _circle_preview(center: Point, radius: float, n: int = 48):
    pts = [
        (center[0] + radius * math.cos(i * math.tau / n),
         center[1] + radius * math.sin(i * math.tau / n))
        for i in range(n + 1)
    ]
    return list(zip(pts, pts[1:]))


class LineTool(Tool):
    def start(self) -> None:
        self.name = "LINE"
        self._points: list[Point] = []
        self.ctx.prompt(tr("LINE Specify first point:"))

    def on_point(self, point: Point) -> None:
        if self._points:
            self.ctx.execute(actions.add_line(self._points[-1], point))
        self._points.append(point)
        self.last_point = point
        self.ctx.prompt(tr("Specify next point or [Close/Undo] <Enter ends>:"))

    def on_option(self, text: str) -> bool:
        t = text.upper()
        if t in ("C", "CLOSE") and len(self._points) >= 3:
            self.ctx.execute(actions.add_line(self._points[-1], self._points[0]))
            self.ctx.finish()
            return True
        if t in ("U", "UNDO") and self._points:
            # AutoCAD: U inside LINE backs up one segment.
            self._points.pop()
            self.last_point = self._points[-1] if self._points else None
            self.ctx.echo(tr("*segment removed — undo the entity with U after the command*"))
            return True
        return False

    def preview_segments(self, cursor: Point):
        return [(self._points[-1], cursor)] if self._points else []


class CircleTool(Tool):
    def start(self) -> None:
        self.name = "CIRCLE"
        self._mode = "CR"
        self._pts: list[Point] = []
        self.ctx.prompt(tr("CIRCLE Specify center point or [2P/3P]:"))

    def on_option(self, text: str) -> bool:
        t = text.upper()
        if t == "2P" and not self._pts:
            self._mode = "2P"
            self.ctx.prompt(tr("Specify first end point of diameter:"))
            return True
        if t == "3P" and not self._pts:
            self._mode = "3P"
            self.ctx.prompt(tr("Specify first point on circle:"))
            return True
        # center-radius mode accepts a typed radius after the center
        if self._mode == "CR" and self._pts:
            try:
                radius = float(text)
            except ValueError:
                return False
            if radius > 0:
                self.ctx.execute(actions.add_circle(self._pts[0], radius))
                self.ctx.finish()
                return True
        return False

    def on_point(self, point: Point) -> None:
        self._pts.append(point)
        self.last_point = point
        if self._mode == "CR":
            if len(self._pts) == 1:
                self.ctx.prompt(tr("Specify radius:"))
            else:
                radius = math.dist(self._pts[0], self._pts[1])
                if radius > 0:
                    self.ctx.execute(actions.add_circle(self._pts[0], radius))
                self.ctx.finish()
        elif self._mode == "2P":
            if len(self._pts) == 1:
                self.ctx.prompt(tr("Specify second end point of diameter:"))
            else:
                center, radius = actions.circle_from_2p(*self._pts)
                if radius > 0:
                    self.ctx.execute(actions.add_circle(center, radius))
                self.ctx.finish()
        else:  # 3P
            if len(self._pts) < 3:
                self.ctx.prompt(tr("Specify next point on circle:"))
            else:
                try:
                    center, radius = actions.circle_from_3p(*self._pts)
                except ValueError:
                    self.ctx.echo(tr("Collinear points — no circle."))
                else:
                    self.ctx.execute(actions.add_circle(center, radius))
                self.ctx.finish()

    def preview_segments(self, cursor: Point):
        if self._mode == "CR" and self._pts:
            r = math.dist(self._pts[0], cursor)
            return _circle_preview(self._pts[0], r) + [(self._pts[0], cursor)]
        if self._mode == "2P" and self._pts:
            center, r = actions.circle_from_2p(self._pts[0], cursor)
            return _circle_preview(center, r)
        if self._mode == "3P" and len(self._pts) == 2:
            try:
                center, r = actions.circle_from_3p(self._pts[0], self._pts[1], cursor)
            except ValueError:
                return []
            return _circle_preview(center, r)
        return []


class ArcTool(Tool):
    def start(self) -> None:
        self.name = "ARC"
        self._pts: list[Point] = []
        self.ctx.prompt(tr("ARC Specify start point:"))

    def on_point(self, point: Point) -> None:
        self._pts.append(point)
        self.last_point = point
        if len(self._pts) == 1:
            self.ctx.prompt(tr("Specify second point on arc:"))
        elif len(self._pts) == 2:
            self.ctx.prompt(tr("Specify end point of arc:"))
        else:
            try:
                self.ctx.execute(actions.add_arc_3p(*self._pts))
            except ValueError:
                self.ctx.echo(tr("Collinear points — no arc."))
            self.ctx.finish()

    def preview_segments(self, cursor: Point):
        if len(self._pts) == 1:
            return [(self._pts[0], cursor)]
        if len(self._pts) == 2:
            try:
                center, r = actions.circle_from_3p(self._pts[0], self._pts[1], cursor)
            except ValueError:
                return [(self._pts[0], self._pts[1]), (self._pts[1], cursor)]
            return _circle_preview(center, r)
        return []


class PlineTool(Tool):
    def start(self) -> None:
        self.name = "PLINE"
        self._pts: list[Point] = []
        self.ctx.prompt(tr("PLINE Specify start point:"))

    def on_point(self, point: Point) -> None:
        self._pts.append(point)
        self.last_point = point
        self.ctx.prompt(tr("Specify next point or [Close] <Enter ends>:"))

    def on_option(self, text: str) -> bool:
        if text.upper() in ("C", "CLOSE") and len(self._pts) >= 3:
            self.ctx.execute(actions.add_polyline(self._pts, closed=True))
            self._pts = []
            self.ctx.finish()
            return True
        return False

    def on_enter(self) -> None:
        if len(self._pts) >= 2:
            self.ctx.execute(actions.add_polyline(self._pts))
        self._pts = []
        self.ctx.finish()

    def on_cancel(self) -> None:
        # AutoCAD keeps what was drawn on Esc too (segments are committed);
        # our PLINE builds one entity, so Esc keeps the collected ones.
        self.on_enter()

    def preview_segments(self, cursor: Point):
        segs = list(zip(self._pts, self._pts[1:]))
        if self._pts:
            segs.append((self._pts[-1], cursor))
        return segs


class RectangTool(Tool):
    def start(self) -> None:
        self.name = "RECTANG"
        self._first: Point | None = None
        self.ctx.prompt(tr("RECTANG Specify first corner:"))

    def on_point(self, point: Point) -> None:
        if self._first is None:
            self._first = point
            self.last_point = point
            self.ctx.prompt(tr("Specify other corner:"))
        else:
            self.ctx.execute(actions.add_rectangle(self._first, point))
            self.ctx.finish()

    def preview_segments(self, cursor: Point):
        if self._first is None:
            return []
        p1, p2 = self._first, cursor
        c = [(p1[0], p1[1]), (p2[0], p1[1]), (p2[0], p2[1]), (p1[0], p2[1])]
        return list(zip(c, c[1:] + c[:1]))


class PolygonTool(Tool):
    def start(self) -> None:
        self.name = "POLYGON"
        self._sides = 0
        self._center: Point | None = None
        self.ctx.prompt(tr("POLYGON Enter number of sides <4>:"))

    def on_option(self, text: str) -> bool:
        if self._sides == 0:
            try:
                sides = int(text)
            except ValueError:
                return False
            if 3 <= sides <= 1024:
                self._sides = sides
                self.ctx.prompt(tr("Specify center of polygon:"))
                return True
            self.ctx.echo(tr("Between 3 and 1024 sides."))
            return True
        return False

    def on_enter(self) -> None:
        if self._sides == 0:
            self._sides = 4
            self.ctx.prompt(tr("Specify center of polygon:"))
        else:
            self.ctx.finish()

    def on_point(self, point: Point) -> None:
        if self._sides == 0:
            return  # still waiting for the side count
        if self._center is None:
            self._center = point
            self.last_point = point
            self.ctx.prompt(tr("Specify a vertex (inscribed):"))
        else:
            self.ctx.execute(actions.add_polygon(self._center, point, self._sides))
            self.ctx.finish()

    def preview_segments(self, cursor: Point):
        if self._center is None or self._sides == 0:
            return []
        pts = actions.polygon_points(self._center, cursor, self._sides)
        return list(zip(pts, pts[1:] + pts[:1]))


TOOL_CLASSES = {
    "LINE": LineTool,
    "CIRCLE": CircleTool,
    "ARC": ArcTool,
    "PLINE": PlineTool,
    "RECTANG": RectangTool,
    "POLYGON": PolygonTool,
}
