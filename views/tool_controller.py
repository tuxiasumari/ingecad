# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Glue between prompt/viewport and the drawing tools.

Owns the interactive state AutoCAD users feel with their hands: object
snap (F3), ortho (F8), polar (F10), the rubber-band preview, and the
incremental overlay scene so drawing stays instant on any file size.
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QObject, Signal

from core import actions
from core.coords import CoordinateError, parse_point
from core.i18n import tr
from core.snap import SnapEngine, SnapHit
from render.backend import _flatten_distance, build_scene_for_entities
from tools.base import Tool, ToolContext
from tools.draw import TOOL_CLASSES

SNAP_PX = 12.0  # aperture in logical pixels


class ToolController(QObject):
    changed = Signal()  # something visual changed: repaint the viewport

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.tool: Optional[Tool] = None
        self.osnap_on = True
        self.ortho_on = False
        self.polar_on = False
        self.snap_engine: Optional[SnapEngine] = None
        self.snap_hit: Optional[SnapHit] = None
        self._cursor: Optional[tuple[float, float]] = None
        self._flatten = 0.01
        self._base_handles: set[str] = set()

    # -- document lifecycle ----------------------------------------------------
    def attach_document(self, document, flatten: Optional[float] = None) -> None:
        self.snap_engine = SnapEngine(document)
        self._flatten = flatten if flatten else _flatten_distance(
            document.modelspace())
        self._base_handles = set()
        self.window.history.document = document
        self.window.history.clear()
        self._refresh_overlay()

    def mark_scene_merged(self) -> None:
        """A full regen just happened: overlay entities now live in the base."""
        self._base_handles = {
            c.entity.dxf.handle
            for c in self._draw_commands()
            if c.entity is not None
        }
        self._refresh_overlay()

    # -- toggles ---------------------------------------------------------------
    def toggle(self, which: str) -> bool:
        value = not getattr(self, f"{which}_on")
        setattr(self, f"{which}_on", value)
        self.changed.emit()
        return value

    # -- tool lifecycle --------------------------------------------------------
    def active(self) -> bool:
        return self.tool is not None

    def start_tool(self, name: str) -> None:
        if self.window.document is None:
            self.window.new_document()
        if self.tool is not None:
            self.tool.on_cancel()
        ctx = ToolContext(
            execute=self._execute,
            prompt=self.window.command_line.echo,
            echo=self.window.command_line.echo,
            finish=self._finish,
        )
        self.tool = TOOL_CLASSES[name](ctx)
        self.tool.start()
        self.changed.emit()

    def _finish(self) -> None:
        self.tool = None
        self.snap_hit = None
        self.changed.emit()

    def cancel(self) -> None:
        if self.tool is not None:
            tool = self.tool
            self.tool = None  # avoid re-entry via ctx.finish
            tool.on_cancel()
            self._finish()

    # -- command execution and incremental render ------------------------------
    def _execute(self, command) -> None:
        self.window.history.execute(command)
        if self.snap_engine is not None:
            self.snap_engine.invalidate()
        self._refresh_overlay()

    def after_history_change(self) -> None:
        """Called by U/REDO. Rebuild the overlay; regen if base went stale."""
        if self.snap_engine is not None:
            self.snap_engine.invalidate()
        alive = {c.entity.dxf.handle for c in self._draw_commands()
                 if c.entity is not None}
        if self._base_handles - alive:
            # an entity already merged into the base scene was undone
            self.window.regen_in_memory()
        else:
            self._refresh_overlay()

    def _draw_commands(self):
        return [c for c in self.window.history._undo
                if isinstance(c, actions.AddEntityCommand)]

    def _refresh_overlay(self) -> None:
        document = self.window.document
        if document is None:
            return
        entities = [
            c.entity for c in self._draw_commands()
            if c.entity is not None
            and c.entity.dxf.handle not in self._base_handles
        ]
        scene = (build_scene_for_entities(document, entities, self._flatten)
                 if entities else None)
        self.window.viewport.set_overlay_scene(scene)
        self.changed.emit()

    # -- pointer input ---------------------------------------------------------
    def on_hover(self, wx: float, wy: float, threshold_world: float) -> None:
        self._cursor = (wx, wy)
        self.snap_hit = None
        if self.osnap_on and self.snap_engine is not None:
            self.snap_hit = self.snap_engine.find(
                (wx, wy), threshold_world,
                from_point=self.tool.last_point if self.tool else None,
            )

    def on_click(self, wx: float, wy: float) -> None:
        if self.tool is None:
            return
        self.tool.on_point(self.resolved_point(wx, wy))
        self.changed.emit()

    def resolved_point(self, wx: float, wy: float) -> tuple[float, float]:
        """Snap wins over ortho/polar, AutoCAD-style."""
        if self.snap_hit is not None:
            return (self.snap_hit.x, self.snap_hit.y)
        anchor = self.tool.last_point if self.tool else None
        if anchor is not None and (self.ortho_on or self.polar_on):
            dx, dy = wx - anchor[0], wy - anchor[1]
            if self.polar_on and not self.ortho_on:
                ang = math.atan2(dy, dx)
                step = math.radians(45.0)
                ang = round(ang / step) * step
                d = math.hypot(dx, dy)
                return (anchor[0] + d * math.cos(ang),
                        anchor[1] + d * math.sin(ang))
            if abs(dx) >= abs(dy):
                return (wx, anchor[1])
            return (anchor[0], wy)
        return (wx, wy)

    # -- prompt input ----------------------------------------------------------
    def on_text(self, text: str) -> bool:
        """Prompt input while a tool is active. True if consumed."""
        if self.tool is None:
            return False
        stripped = text.strip()
        if not stripped:
            self.tool.on_enter()
            self.changed.emit()
            return True
        if self.tool.on_option(stripped):
            self.changed.emit()
            return True
        direction = None
        anchor = self.tool.last_point
        if anchor is not None and self._cursor is not None:
            constrained = self.resolved_point(*self._cursor)
            direction = math.atan2(constrained[1] - anchor[1],
                                   constrained[0] - anchor[0])
        try:
            point = parse_point(stripped, anchor, direction)
        except CoordinateError as exc:
            self.window.command_line.echo(tr("Invalid point: {error}",
                                             error=str(exc)))
            return True
        if point is None:
            self.window.command_line.echo(tr("Invalid input."))
            return True
        self.tool.on_point((point.x, point.y))
        self.changed.emit()
        return True

    # -- viewport painting hooks ----------------------------------------------
    def preview_segments(self):
        if self.tool is None or self._cursor is None:
            return []
        return self.tool.preview_segments(self.resolved_point(*self._cursor))
