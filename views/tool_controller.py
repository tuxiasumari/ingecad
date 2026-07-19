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

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from core import actions
from core.coords import CoordinateError, parse_point
from core.i18n import tr
from core.select import GeometryIndex, apply_grip_edit, entity_grips
from core.snap import SnapEngine, SnapHit
from render.backend import _flatten_distance, build_scene_for_entities
from tools.base import Tool, ToolContext
from tools.blocks import BLOCK_TOOL_CLASSES
from tools.dimension import DIM_TOOL_CLASSES
from tools.draw import TOOL_CLASSES
from tools.edit import EDIT_TOOL_CLASSES

SNAP_PX = 12.0   # aperture in logical pixels
PICK_PX = 8.0    # pick box half-size in logical pixels
# Overlay entities beyond this schedule an idle merge into the base scene
# (the overlay is re-tessellated per edit, so it must not grow unbounded).
MERGE_THRESHOLD = 50

ALL_TOOL_CLASSES = {**TOOL_CLASSES, **EDIT_TOOL_CLASSES, **BLOCK_TOOL_CLASSES,
                    **DIM_TOOL_CLASSES}


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
        self._clipboard = None   # (list[entity copies], base point) for paste
        self._ghost_on = False   # a drag ghost (MOVE/COPY/PASTE) is showing
        # Selection state (idle noun set, or the set a tool is acquiring).
        self.index: Optional[GeometryIndex] = None
        self.selection: set[str] = set()
        self._selecting_for: Optional[Tool] = None
        self._window_anchor: Optional[tuple[float, float]] = None
        self._pick_tolerance = 1.0  # world units, refreshed on hover
        # Edits render instantly via the overlay; the expensive base-scene
        # regen is coalesced so a burst of trims pays it once.
        self._pending_render: list = []
        # Grip drag state: (handle, grip_index, role, SnapshotCommand).
        self._grip_drag = None
        self._regen_timer = QTimer(self)
        self._regen_timer.setSingleShot(True)
        self._regen_timer.setInterval(400)
        self._regen_timer.timeout.connect(self._run_deferred_regen)
        # Additive edits (draw/paste/copy) never NEED a regen — they ride the
        # overlay — but the overlay is re-tessellated per edit, so merge it
        # into the base scene after a longer quiet pause to bound its growth.
        self._merge_timer = QTimer(self)
        self._merge_timer.setSingleShot(True)
        self._merge_timer.setInterval(2500)
        self._merge_timer.timeout.connect(self._run_deferred_regen)

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
        self._pending_render = []
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
        if getattr(self.window, "_active_layout", "Model") != "Model":
            # Paper space is view/plot-only in v0.1 (honest, like the plan).
            self.window.command_line.echo(
                tr("Editing in paper space arrives in v0.2 — switch to the "
                   "Model tab to draw."))
            return
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
            ask_text=self._ask_text,
            ask_choice=self._ask_choice,
            ask_hatch=self._ask_hatch,
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

    def edges_geometry(self, handles=None, exclude=None, near=None):
        """(segments, circles) for TRIM/EXTEND edge math.

        ``near`` is a world bbox: cutters that cannot touch it are filtered
        out vectorized — a trim pick pays for LOCAL edges, not the whole
        drawing (TRIM cutters must intersect the target by definition).
        """
        if self.index is None:
            return [], []
        if self.index._dirty:
            self.index._build()
        seg_arr = self.index._segs
        circ_arr = self.index._circles
        smask = np.ones(len(seg_arr), dtype=bool)
        cmask = np.ones(len(circ_arr), dtype=bool)
        if handles is not None:
            wanted = set(handles)
            wanted.discard(exclude)
            smask &= np.fromiter((h in wanted for h in self.index._seg_owner),
                                 bool, len(seg_arr))
            cmask &= np.fromiter((h in wanted for h in self.index._circle_owner),
                                 bool, len(circ_arr))
        elif exclude is not None:
            smask &= np.fromiter((h != exclude for h in self.index._seg_owner),
                                 bool, len(seg_arr))
            cmask &= np.fromiter((h != exclude for h in self.index._circle_owner),
                                 bool, len(circ_arr))
        if near is not None and len(seg_arr):
            x0, y0, x1, y1 = near
            smask &= ((np.minimum(seg_arr[:, 0], seg_arr[:, 2]) <= x1)
                      & (np.maximum(seg_arr[:, 0], seg_arr[:, 2]) >= x0)
                      & (np.minimum(seg_arr[:, 1], seg_arr[:, 3]) <= y1)
                      & (np.maximum(seg_arr[:, 1], seg_arr[:, 3]) >= y0))
        if near is not None and len(circ_arr):
            x0, y0, x1, y1 = near
            cmask &= ((circ_arr[:, 0] - circ_arr[:, 2] <= x1)
                      & (circ_arr[:, 0] + circ_arr[:, 2] >= x0)
                      & (circ_arr[:, 1] - circ_arr[:, 2] <= y1)
                      & (circ_arr[:, 1] + circ_arr[:, 2] >= y0))
        segs = [tuple(s) for s in seg_arr[smask]]
        # (center, r, a0, a1): arcs cut/bound only along their real sweep
        circles = [((c[0], c[1]), c[2], c[4], c[5]) for c in circ_arr[cmask]]
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

    # -- Delete / clipboard (noun-verb: act on the current selection) ----------
    def delete_selection(self) -> bool:
        """Supr/Delete: erase the selected objects (only when idle)."""
        if self.tool is not None:
            return False
        entities = self._selection_entities()
        if not entities:
            return False
        self._execute(actions.EraseCommand(entities))
        self.clear_selection()
        return True

    def copy_selection(self, cut: bool = False) -> bool:
        """Ctrl+C / Ctrl+X: stash copies of the selection with a base point."""
        entities = self._selection_entities()
        if not entities:
            return False
        try:
            from ezdxf import bbox
            ext = bbox.extents(entities)
            base = (ext.extmin.x, ext.extmin.y)
        except Exception:
            base = (0.0, 0.0)
        self._clipboard = ([e.copy() for e in entities], base)
        if cut:
            self._execute(actions.EraseCommand(entities))
            self.clear_selection()
        return True

    def clipboard_data(self):
        return self._clipboard if self._clipboard else (None, None)

    def paste(self) -> None:
        """Ctrl+V: place the clipboard entities from a picked point."""
        if not self._clipboard:
            self.window.command_line.echo(tr("Clipboard is empty."))
            return
        self.start_tool("PASTECLIP")

    # -- in-place text typing (DTEXT) -----------------------------------------
    def text_capturing(self) -> bool:
        return self.tool is not None and getattr(self.tool, "typing", False)

    def text_char(self, ch: str) -> None:
        if self.text_capturing():
            self.tool.on_char(ch)
            self.changed.emit()

    def text_backspace(self) -> None:
        if self.text_capturing():
            self.tool.on_backspace()
            self.changed.emit()

    def text_newline(self) -> None:
        if self.text_capturing():
            self.tool.on_enter()
            self.changed.emit()

    def text_finish(self) -> None:
        if self.text_capturing():
            self.tool.finish_typing()
            self.changed.emit()

    def live_text(self):
        tool = self.tool
        return tool.live_text() if tool is not None and hasattr(tool, "live_text") \
            else None

    def _ask_text(self, prompt: str, default: str = "") -> Optional[str]:
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getMultiLineText(
            self.window, prompt, prompt, default)
        return text if ok else None

    def _ask_choice(self, prompt: str, items: list, default: str = "") -> Optional[str]:
        from PySide6.QtWidgets import QInputDialog

        start = items.index(default) if default in items else 0
        text, ok = QInputDialog.getItem(
            self.window, prompt, prompt, list(items), start, editable=False)
        return text if ok else None

    def _ask_hatch(self, settings: dict) -> Optional[dict]:
        from views.hatch_dialog import HatchDialog

        dlg = HatchDialog(self.window, settings)
        if dlg.exec():
            return dlg.settings()
        return None

    def hatch_region_at(self, point):
        """(outer_polygon, [island_polygons]) under a Pick-internal-point."""
        from core.hatch_boundary import region_at_point

        if self.window.document is None:
            return None
        return region_at_point(
            list(self.window.document.modelspace()), point)

    def block_names(self) -> list:
        """User block definitions (not *Model_Space/*Paper_Space/anonymous)."""
        if self.window.document is None:
            return []
        return sorted(
            b.name for b in self.window.document.doc.blocks
            if not b.name.startswith("*"))

    def _finish(self) -> None:
        self.tool = None
        self.snap_hit = None
        self._selecting_for = None
        self._window_anchor = None
        self.selection = set()  # command done: highlight goes off
        if self._ghost_on:
            self._ghost_on = False
            self.window.viewport.set_ghost_scene(None)
        self.changed.emit()

    def cancel(self) -> None:
        if self._grip_drag is not None:
            # Esc mid-grip: revert the entity to its pre-drag snapshot. Its
            # base-scene copy was hidden at grab time, so ride the overlay
            # for instant feedback while the async regen rebuilds the base.
            handle, _i, _r, snap = self._grip_drag
            self._grip_drag = None
            snap.undo(self.window.document)
            self._invalidate_geometry()
            self._pending_render.extend(
                e for e in snap.entities
                if e.is_alive and e.dxf.owner is not None
                and e not in self._pending_render)
            self._refresh_overlay()
            self.window.regen_in_memory()
            return
        if self.tool is not None:
            tool = self.tool
            self.tool = None  # avoid re-entry via ctx.finish
            tool.on_cancel()
            self._finish()
        elif self.selection or self._window_anchor:
            self.clear_selection()

    # -- command execution and incremental render ------------------------------
    @staticmethod
    def _added_entities(command):
        """New entities of a purely-additive command, else None.

        Additive commands (draw, paste, copy, mirror-keep-source) touch
        nothing that already exists, so the snap/pick caches can grow
        incrementally and the base scene needs no urgent regen.
        """
        if isinstance(command, actions.AddEntityCommand):
            return [command.entity] if command.entity is not None else []
        if isinstance(command, (actions.PasteCommand,
                                actions.CopyEntitiesCommand)):
            return list(command.copies)
        return None

    # Commands whose touched entities are fully known, so the pick index can
    # be patched (remove + re-add) instead of rebuilt from scratch.
    _KNOWN_MODIFY = (actions.EraseCommand, actions.TransformCommand,
                     actions.SetPropertyCommand, actions.ReplaceEntitiesCommand,
                     actions.CreateBlockCommand, actions.ExplodeCommand)

    def _execute(self, command) -> None:
        self.window.history.execute(command)
        added = self._added_entities(command)
        if added is not None:
            # Appending beats invalidating: a full cache rebuild walks the
            # whole modelspace in Python on the NEXT mouse move — that walk
            # was the per-click lag while drawing on a large file.
            if self.snap_engine is not None:
                self.snap_engine.add_entities(added)
            if self.index is not None:
                self.index.add_entities(added)
        elif isinstance(command, self._KNOWN_MODIFY):
            # snap rebuild is a cheap attribute walk; the pick index is NOT
            # (ezdxf bbox per exotic entity) — it gets patched surgically in
            # the display branch below, where the touched sets are known.
            if self.snap_engine is not None:
                self.snap_engine.invalidate()
        else:
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
        if isinstance(command, actions.AddDimensionCommand):
            # A dimension renders into an anonymous block; the overlay can't
            # show that cheaply, so regen now (creating one dim is not hot).
            self.window.regen_in_memory()
        elif added is not None:
            # Additive: show through the overlay, no hide, no urgent regen —
            # paste used to schedule a full regen whose GIL-heavy rebuild
            # made the NEXT paste stutter on big files.
            if not isinstance(command, actions.AddEntityCommand):
                # drawn entities reach the overlay via _draw_commands()
                self._pending_render.extend(added)
            self._refresh_overlay()
            if any(e.dxftype() == "DIMENSION" for e in added):
                # pasted dimension: only a regen renders its block right
                self._regen_timer.start()
            elif (len(self._draw_commands()) + len(self._pending_render)
                    > MERGE_THRESHOLD):
                # overlay got heavy (re-tessellated per edit): fold it into
                # the base scene after a quiet pause. Below the threshold no
                # regen is ever scheduled — a couple of pastes on a big file
                # must not queue a multi-second background rebuild.
                self._merge_timer.start()
        else:
            # hide the OLD geometry instantly (surgical, no regen) and show
            # the results NOW through the overlay; the full regen is deferred
            old_handles = []
            if isinstance(command, (actions.EraseCommand,
                                    actions.TransformCommand,
                                    actions.SetPropertyCommand)):
                # property edits too: hide the stale-look base copy and show
                # the restyled entity via the overlay (async regen catches up)
                old_handles = [e.dxf.handle for e in command.entities]
            elif isinstance(command, actions.ReplaceEntitiesCommand):
                old_handles = [e.dxf.handle for e in command.old_entities]
            elif isinstance(command, (actions.CreateBlockCommand,
                                      actions.ExplodeCommand)):
                old_handles = [e.dxf.handle for e in command.sources]
            if old_handles:
                self.window.viewport.hide_handles(old_handles)
            new_ents = []
            if isinstance(command, actions.CreateBlockCommand) and command.insert:
                new_ents = [command.insert]
            elif isinstance(command, actions.ExplodeCommand):
                for _orig, parts in command.pieces:
                    new_ents.extend(parts)
            elif isinstance(command, actions.EraseCommand):
                pass   # nothing new to show — the hide above IS the result
            else:
                for attr in ("new_entities", "copies", "entities"):
                    extra = getattr(command, attr, None)
                    if extra:
                        new_ents = list(extra)
                        break
            self._pending_render.extend(new_ents)
            if self.index is not None:
                # patch the pick index: O(touched) instead of a full rebuild
                # (both calls no-op if the index was invalidated above)
                self.index.remove_handles(old_handles)
                self.index.add_entities(
                    [e for e in new_ents if e.is_alive])
            self._refresh_overlay()
            self._regen_timer.start()

    def _run_deferred_regen(self) -> None:
        self.window.regen_in_memory()

    def _invalidate_geometry(self) -> None:
        if self.snap_engine is not None:
            self.snap_engine.invalidate()
        if self.index is not None:
            self.index.invalidate()

    def after_history_change(self, command=None) -> None:
        """Called by U/REDO with the command that crossed the boundary.

        Instant feedback, no deferred-regen blank: stale base-scene copies of
        everything the command touched are hidden surgically and the restored
        or current entities ride the overlay; the full regen stays coalesced.
        """
        self._invalidate_geometry()
        if command is None or isinstance(command, actions.AddDimensionCommand):
            # unknown scope / dimension block graphics: only a regen is right;
            # hide what the undo just destroyed so it vanishes NOW (the regen
            # runs in the background and lands later)
            removed = getattr(command, "removed_handles", None)
            if removed:
                self.window.viewport.hide_handles(removed)
            self.window.regen_in_memory()
            return
        touched = []
        for attr in ("entities", "old_entities", "new_entities", "copies",
                     "sources"):
            touched.extend(getattr(command, attr, None) or [])
        if getattr(command, "insert", None) is not None:
            touched.append(command.insert)
        if getattr(command, "entity", None) is not None:
            touched.append(command.entity)
        for _orig, parts in (getattr(command, "pieces", None) or []):
            touched.extend(parts)
        # hide stale base copies: entities the undo/redo just destroyed
        # (recorded handles) plus every touched survivor's base-scene copy
        hide = list(getattr(command, "removed_handles", None) or [])
        hide += [e.dxf.handle for e in touched if e.is_alive]
        if hide:
            self.window.viewport.hide_handles(hide)
        for e in touched:
            if (e.is_alive and e.dxf.owner is not None
                    and e not in self._pending_render):
                self._pending_render.append(e)
        self._refresh_overlay()
        self._regen_timer.start()

    def _draw_commands(self):
        return [c for c in self.window.history._undo
                if isinstance(c, actions.AddEntityCommand)]

    def _refresh_overlay(self) -> None:
        document = self.window.document
        if document is None:
            return
        # owner=None means the entity is unlinked from modelspace (erased,
        # kept alive only for undo) — never draw those in the overlay.
        entities = [
            c.entity for c in self._draw_commands()
            if c.entity is not None and c.entity.dxf.owner is not None
            and c.entity.dxf.handle not in self._base_handles
        ]
        entities += [e for e in self._pending_render
                     if e.is_alive and e.dxf.owner is not None
                     and e.dxf.handle not in self._base_handles]
        entities += self.grip_overlay_entities()
        scene = (build_scene_for_entities(document, entities, self._flatten)
                 if entities else None)
        self.window.viewport.set_overlay_scene(scene)
        self.changed.emit()

    # -- pointer input ---------------------------------------------------------
    def on_hover(self, wx: float, wy: float, threshold_world: float) -> None:
        self._cursor = (wx, wy)
        self._pick_tolerance = threshold_world * (PICK_PX / SNAP_PX)
        self.snap_hit = None
        grip_hot = self._grip_drag is not None
        needs_snap = grip_hot or (
            self.tool is not None and self._selecting_for is None
            and not self.tool.entity_picker)
        if needs_snap and self.osnap_on and self.snap_engine is not None:
            self.snap_hit = self.snap_engine.find(
                (wx, wy), threshold_world,
                from_point=self.tool.last_point if self.tool else None,
            )
        self._sync_ghost(wx, wy)

    def _sync_ghost(self, wx: float, wy: float) -> None:
        """MOVE/COPY/PASTE drag preview: the tool exposes ghost_entities +
        ghost_base; the scene builds ONCE and each hover only updates the
        translation uniform — the drag stays fluid on any selection size."""
        tool = self.tool
        ents = getattr(tool, "ghost_entities", None) if tool is not None else None
        base = getattr(tool, "ghost_base", None) if tool is not None else None
        if not ents or base is None:
            if self._ghost_on:
                self._ghost_on = False
                self.window.viewport.set_ghost_scene(None)
            return
        if not self._ghost_on:
            scene = build_scene_for_entities(
                self.window.document, ents, self._flatten)
            self.window.viewport.set_ghost_scene(scene)
            self._ghost_on = True
        rx, ry = self.resolved_point(wx, wy)
        self.window.viewport.set_ghost_offset(rx - base[0], ry - base[1])

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
                # complete a target rectangle (drag or click-click). AutoCAD
                # quick-mode TRIM/EXTEND treats BOTH directions as crossing:
                # whatever the rect touches is a target.
                ax, ay = self._window_anchor
                self._window_anchor = None
                rect = (min(ax, wx), min(ay, wy), max(ax, wx), max(ay, wy))
                handles = self.index.crossing(rect)
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

    def preview_dimension(self):
        """Rich dimension preview (frame + measurement) for the dim tools."""
        if self.tool is None or self._cursor is None:
            return None
        fn = getattr(self.tool, "preview_dimension", None)
        if fn is None:
            return None
        return fn(self.resolved_point(*self._cursor))

    # -- grips (selected-entity editing points) --------------------------------
    def grip_points(self):
        """[(x, y, role, handle, index)] for the current idle selection."""
        # While a grip is hot, hide the rest (AutoCAD/BricsCAD show only the
        # moving grip); this also avoids flashing the new grips of a just-
        # inserted vertex before the user drops it.
        if self._grip_drag is not None:
            return []
        if self.tool is not None or not self.selection or self.index is None:
            return []
        out = []
        for h in list(self.selection)[:200]:   # cap: grips get noisy past that
            e = self.index.entity(h)
            if e is None or not e.is_alive:
                continue
            for i, (x, y, role) in enumerate(entity_grips(e)):
                out.append((x, y, role, h, i))
        return out

    def grip_at(self, wx: float, wy: float, tol: float):
        for x, y, role, h, i in self.grip_points():
            if abs(x - wx) <= tol and abs(y - wy) <= tol:
                return (x, y, role, h, i)
        return None

    def begin_grip_drag(self, grip) -> None:
        from core.actions import SnapshotCommand
        from core.select import entity_grips

        gx, gy, role, handle, index = grip
        entity = self.index.entity(handle)
        if entity is None:
            return
        snap = SnapshotCommand([entity])   # captures the pre-grab state
        self._grip_drag = (handle, index, role, snap)
        # Hide the base-scene copy ONCE (a full-scene re-upload); from here
        # the live entity rides the cheap 1-entity overlay each frame.
        self.window.viewport.hide_handles([handle])
        self._refresh_overlay()

    def grip_target(self, wx: float, wy: float) -> tuple[float, float]:
        """Where the hot grip should sit: snap wins, then ortho/polar."""
        if self.snap_hit is not None:
            return (self.snap_hit.x, self.snap_hit.y)
        return (wx, wy)

    def update_grip_drag(self, wx: float, wy: float) -> None:
        if self._grip_drag is None:
            return
        handle, index, role, _snap = self._grip_drag
        entity = self.index.entity(handle)
        if entity is not None:
            apply_grip_edit(entity, index, role, (wx, wy))
            # per frame: rebuild only the dragged entity's overlay — no
            # index rebuild, no whole-scene re-upload
            self._refresh_overlay()

    def finish_grip_drag(self, wx: float, wy: float) -> None:
        if self._grip_drag is None:
            return
        handle, index, role, snap = self._grip_drag
        self._grip_drag = None
        entity = self.index.entity(handle)
        if entity is not None:
            apply_grip_edit(entity, index, role, (wx, wy))
            snap.commit(self.window.document)
            self.window.history._undo.append(snap)
            self.window.history._redo.clear()
            # grips/snap see the new shape; the pick index is patched, not
            # rebuilt (a full rebuild froze the next pick on big files)
            if self.snap_engine is not None:
                self.snap_engine.invalidate()
            if self.index is not None:
                self.index.remove_handles([handle])
                self.index.add_entities([entity])
            self._refresh_overlay()
            self._regen_timer.start()

    def grip_overlay_entities(self):
        if self._grip_drag is None:
            return []
        entity = self.index.entity(self._grip_drag[0])
        return [entity] if entity is not None and entity.is_alive else []

    def highlight_geometry(self):
        """(segments, circles, boxes) of the current selection, world coords."""
        if not self.selection or self.index is None:
            import numpy as np

            empty = np.empty((0, 4))
            return empty, empty, empty
        return (self.index.segments_of(self.selection),
                self.index.circles_of(self.selection),
                self.index.boxes_of(self.selection))
