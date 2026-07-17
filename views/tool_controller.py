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
from core.select import GeometryIndex
from core.snap import SnapEngine, SnapHit
from render.backend import _flatten_distance, build_scene_for_entities
from tools.base import Tool, ToolContext
from tools.draw import TOOL_CLASSES
from tools.edit import EDIT_TOOL_CLASSES

SNAP_PX = 12.0   # aperture in logical pixels
PICK_PX = 8.0    # pick box half-size in logical pixels

ALL_TOOL_CLASSES = {**TOOL_CLASSES, **EDIT_TOOL_CLASSES}


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
        # Selection state (idle noun set, or the set a tool is acquiring).
        self.index: Optional[GeometryIndex] = None
        self.selection: set[str] = set()
        self._selecting_for: Optional[Tool] = None
        self._window_anchor: Optional[tuple[float, float]] = None
        self._pick_tolerance = 1.0  # world units, refreshed on hover

    # -- document lifecycle ----------------------------------------------------
    def attach_document(self, document, flatten: Optional[float] = None) -> None:
        self.snap_engine = SnapEngine(document)
        self.index = GeometryIndex(document)
        self.selection = set()
        self._selecting_for = None
        self._window_anchor = None
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
            services=self,
        )
        self.tool = ALL_TOOL_CLASSES[name](ctx)
        self.tool.start()
        if self.tool is not None and self.tool.wants_selection:
            if self.selection:
                # noun-verb: the preselected set feeds the command directly.
                # The highlight STAYS while the command runs (AutoCAD keeps
                # the cutting edges lit during TRIM to guide the picks);
                # _finish clears it.
                entities = self._selection_entities()
                self.tool.on_selection(entities)
            else:
                self._selecting_for = self.tool
                self.window.command_line.echo(self.tool.selection_prompt())
        self.changed.emit()

    # -- services for editing tools (ToolContext.services) ---------------------
    def pick_entity(self, point):
        if self.index is None:
            return None
        handle = self.index.pick(point, self._pick_tolerance)
        return self.index.entity(handle) if handle else None

    def edges_geometry(self, handles=None, exclude=None):
        """(segments, circles) for TRIM/EXTEND edge math."""
        if self.index is None:
            return [], []
        if handles is None:
            handles = [e.dxf.handle for e in self.window.document.modelspace()]
        wanted = [h for h in handles if h != exclude]
        segs = [tuple(s) for s in self.index.segments_of(wanted)]
        # (center, r, a0, a1): arcs cut/bound only along their real sweep
        circles = [((c[0], c[1]), c[2], c[4], c[5])
                   for c in self.index.circles_of(wanted)]
        return segs, circles

    def _selection_entities(self) -> list:
        out = []
        for h in self.selection:
            e = self.index.entity(h) if self.index else None
            if e is not None and e.is_alive:
                out.append(e)
        return out

    def clear_selection(self) -> None:
        self.selection = set()
        self._window_anchor = None
        self.changed.emit()

    def _finish(self) -> None:
        self.tool = None
        self.snap_hit = None
        self._selecting_for = None
        self._window_anchor = None
        self.selection = set()  # command done: highlight goes off
        self.changed.emit()

    def cancel(self) -> None:
        if self.tool is not None:
            tool = self.tool
            self.tool = None  # avoid re-entry via ctx.finish
            tool.on_cancel()
            self._finish()
        elif self.selection or self._window_anchor:
            self.clear_selection()

    # -- command execution and incremental render ------------------------------
    def _execute(self, command) -> None:
        self.window.history.execute(command)
        self._invalidate_geometry()
        if (isinstance(command, actions.ReplaceEntitiesCommand)
                and self.selection):
            # a trimmed edge keeps its highlight through its survivors
            olds = {e.dxf.handle for e in command.old_entities}
            if olds & self.selection:
                self.selection = (self.selection - olds) | {
                    e.dxf.handle for e in command.new_entities}
        if self.selection and self.index is not None:
            # prune handles whose entities were erased or replaced
            self.selection = {
                h for h in self.selection
                if (e := self.index.entity(h)) is not None and e.is_alive
            }
        if isinstance(command, actions.AddEntityCommand):
            self._refresh_overlay()
        else:
            # edits touch existing (base-scene) entities: regen in memory
            self.window.regen_in_memory()

    def _invalidate_geometry(self) -> None:
        if self.snap_engine is not None:
            self.snap_engine.invalidate()
        if self.index is not None:
            self.index.invalidate()

    def after_history_change(self) -> None:
        """Called by U/REDO. Rebuild the overlay; regen if base went stale."""
        self._invalidate_geometry()
        tops = [(self.window.history._undo or [None])[-1],
                (self.window.history._redo or [None])[-1]]
        if any(t is not None and not isinstance(t, actions.AddEntityCommand)
               for t in tops):
            # an edit command crossed the undo boundary: base scene is stale
            self.window.regen_in_memory()
            return
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
        self._pick_tolerance = threshold_world * (PICK_PX / SNAP_PX)
        self.snap_hit = None
        needs_snap = (self.tool is not None and self._selecting_for is None
                      and not self.tool.entity_picker)
        if needs_snap and self.osnap_on and self.snap_engine is not None:
            self.snap_hit = self.snap_engine.find(
                (wx, wy), threshold_world,
                from_point=self.tool.last_point if self.tool else None,
            )

    def in_selection_mode(self) -> bool:
        return self.tool is None or self._selecting_for is not None

    def wants_drag_rect(self) -> bool:
        """Left press should defer to release (drag = window rectangle)."""
        return (self.in_selection_mode()
                or (self.tool is not None and self.tool.accepts_target_windows))

    def start_window(self, wx: float, wy: float) -> None:
        """Anchor a selection window (drag start). Idempotent during a drag."""
        if self._window_anchor is None:
            self._window_anchor = (wx, wy)
            self.changed.emit()

    def on_click(self, wx: float, wy: float, shift: bool = False) -> None:
        if self.in_selection_mode():
            self._selection_click(wx, wy, shift)
            self.changed.emit()
            return
        if self.tool is None:
            return
        self.tool.shift = shift
        if self.tool.accepts_target_windows and self.index is not None:
            if self._window_anchor is not None:
                # complete a target window/crossing (drag or click-click)
                ax, ay = self._window_anchor
                self._window_anchor = None
                rect = (min(ax, wx), min(ay, wy), max(ax, wx), max(ay, wy))
                handles = (self.index.window(rect) if wx >= ax
                           else self.index.crossing(rect))
                entities = [e for h in handles
                            if (e := self.index.entity(h)) is not None
                            and e.is_alive]
                self.tool.on_target_entities(entities, rect)
                self.changed.emit()
                return
            if self.index.pick((wx, wy), self._pick_tolerance) is None:
                # empty click: anchor a target window instead of "nothing"
                self._window_anchor = (wx, wy)
                self.changed.emit()
                return
        self.tool.on_point(self.resolved_point(wx, wy))
        self.changed.emit()

    def _selection_click(self, wx: float, wy: float, shift: bool) -> None:
        if self.index is None:
            if self.window.document is None:
                return
            self.index = GeometryIndex(self.window.document)
        if self._window_anchor is not None:
            # second corner: apply window (L->R, fully inside) or crossing
            ax, ay = self._window_anchor
            self._window_anchor = None
            rect = (min(ax, wx), min(ay, wy), max(ax, wx), max(ay, wy))
            hits = (self.index.window(rect) if wx >= ax
                    else self.index.crossing(rect))
            if shift:
                self.selection -= set(hits)
            else:
                self.selection |= set(hits)
            self._echo_count()
            return
        handle = self.index.pick((wx, wy), self._pick_tolerance)
        if handle is None:
            self._window_anchor = (wx, wy)
            return
        if shift:
            self.selection.discard(handle)
        else:
            self.selection.add(handle)
        self._echo_count()

    def _echo_count(self) -> None:
        if self.selection:
            self.window.command_line.echo(
                tr("{count} selected.", count=len(self.selection)))

    def selection_rect(self):
        """(rect, crossing?) while a window pick is in progress."""
        if self._window_anchor is None or self._cursor is None:
            return None
        ax, ay = self._window_anchor
        wx, wy = self._cursor
        rect = (min(ax, wx), min(ay, wy), max(ax, wx), max(ay, wy))
        return rect, wx < ax

    def finish_selection(self) -> None:
        """Enter during a tool's 'Select objects' phase."""
        tool = self._selecting_for
        if tool is None:
            return
        self._selecting_for = None
        entities = self._selection_entities()
        self._window_anchor = None
        # keep the highlight while the command runs (AutoCAD behavior)
        tool.on_selection(entities)
        self.changed.emit()

    def resolved_point(self, wx: float, wy: float) -> tuple[float, float]:
        """Snap wins over ortho/polar, AutoCAD-style."""
        if self.tool is not None and self.tool.entity_picker:
            return (wx, wy)  # object picking: raw cursor, no snap/ortho
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
        if self._selecting_for is not None:
            if not text.strip():
                self.finish_selection()
                return True
            self.window.command_line.echo(self._selecting_for.selection_prompt())
            return True
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

    def highlight_geometry(self):
        """(segments, circles, boxes) of the current selection, world coords."""
        if not self.selection or self.index is None:
            import numpy as np

            empty = np.empty((0, 4))
            return empty, empty, empty
        return (self.index.segments_of(self.selection),
                self.index.circles_of(self.selection),
                self.index.boxes_of(self.selection))
