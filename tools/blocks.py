# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Block and hatch tools: BLOCK, INSERT, EXPLODE, HATCH.

BLOCK converts a selection into a named block reference in place (AutoCAD's
"Convert to block" default). INSERT places an existing block with X scale and
rotation. EXPLODE breaks a reference (or polyline) into its parts. HATCH fills
closed boundary objects with SOLID or a named pattern at a scale/angle.
"""
from __future__ import annotations

from core import actions
from core.i18n import tr
from tools.base import Point, Tool


class BlockTool(Tool):
    """BLOCK: name the selection, pick a base point, convert to a reference."""

    wants_selection = True

    def start(self) -> None:
        self.name = "BLOCK"
        self._entities: list = []
        self._block_name: str | None = None

    def on_selection(self, entities: list) -> None:
        if not entities:
            self.ctx.finish()
            return
        self._entities = entities
        name = self.ctx.ask_text(tr("Block name:"), "")
        if not name or not name.strip():
            self.ctx.echo(tr("*Cancel*"))
            self.ctx.finish()
            return
        self._block_name = name.strip()
        self.ctx.prompt(tr("Specify insertion base point:"))

    def on_point(self, point: Point) -> None:
        if self._block_name and self._entities:
            self.ctx.execute(
                actions.create_block(self._block_name, point, self._entities))
            self.ctx.echo(tr("Block '{name}' created.", name=self._block_name))
        self.ctx.finish()


class InsertTool(Tool):
    """INSERT: choose a defined block, then place it (Scale/Rotate options)."""

    def start(self) -> None:
        self.name = "INSERT"
        self._block_name: str | None = None
        self._xscale = 1.0
        self._rotation = 0.0
        self._await: str | None = None
        names = self.ctx.services.block_names() if self.ctx.services else []
        if not names:
            self.ctx.echo(tr("No blocks defined."))
            self.ctx.finish()
            return
        chosen = self.ctx.ask_choice(tr("Insert block:"), names, names[0])
        if not chosen:
            self.ctx.finish()
            return
        self._block_name = chosen
        self.ctx.prompt(tr("Specify insertion point [Scale/Rotate]:"))

    def on_option(self, text: str) -> bool:
        t = text.strip().upper()
        if self._await is None and t in ("S", "SCALE"):
            self._await = "scale"
            self.ctx.prompt(tr("Specify scale factor <{v}>:", v=self._xscale))
            return True
        if self._await is None and t in ("R", "ROTATE"):
            self._await = "rotate"
            self.ctx.prompt(tr("Specify rotation angle <{v}>:", v=self._rotation))
            return True
        if self._await is not None:
            try:
                value = float(text)
            except ValueError:
                return False
            if self._await == "scale":
                self._xscale = value
            else:
                self._rotation = value
            self._await = None
            self.ctx.prompt(tr("Specify insertion point [Scale/Rotate]:"))
            return True
        return False

    def on_point(self, point: Point) -> None:
        if self._block_name:
            self.ctx.execute(actions.insert_block(
                self._block_name, point, self._xscale, self._xscale,
                self._rotation))
        self.ctx.finish()


class ExplodeTool(Tool):
    """EXPLODE: break block references and polylines into their components."""

    wants_selection = True
    _EXPLODABLE = ("INSERT", "LWPOLYLINE", "POLYLINE")

    def start(self) -> None:
        self.name = "EXPLODE"

    def on_selection(self, entities: list) -> None:
        targets = [e for e in entities if e.dxftype() in self._EXPLODABLE]
        if targets:
            self.ctx.execute(actions.explode_entities(targets))
            self.ctx.echo(tr("{n} exploded.", n=len(targets)))
        else:
            self.ctx.echo(tr("Nothing that can be exploded was selected."))
        self.ctx.finish()


def _is_boundary(e) -> bool:
    t = e.dxftype()
    if t == "LWPOLYLINE":
        return bool(e.closed)
    if t == "POLYLINE":
        return bool(getattr(e, "is_closed", False))
    return t in ("CIRCLE", "ELLIPSE")


class HatchTool(Tool):
    """HATCH: choose a style, then pick internal points (or select objects).

    Mirrors AutoCAD: the style/pattern dialog comes first (SOLID among the
    predefined patterns), then the ``Pick internal point or [Select objects/
    seTtings]`` prompt — click inside closed areas, Enter to apply. Islands
    (closed loops inside the picked region) become holes.
    """

    # Last-used settings persist for the session (AutoCAD remembers them).
    _last = {"pattern": "SOLID", "scale": 1.0, "angle": 0.0, "color": 256}
    # Set by the Palette to launch HATCH straight into point-picking with the
    # already-chosen pattern (skips the style dialog). One-shot.
    _skip_dialog = False

    def start(self) -> None:
        self.name = "HATCH"
        self.mode = "pick"
        self.outer: list = []      # picked outer boundaries (point lists)
        self.islands: list = []    # island loops (holes)
        self.selected: list = []   # boundary entities from Select objects
        self.settings = dict(HatchTool._last)
        if HatchTool._skip_dialog:
            HatchTool._skip_dialog = False   # already have a pattern; go draw
            self._prompt()
            return
        chosen = self.ctx.ask_hatch(self.settings)
        if chosen is None:
            self.ctx.finish()
            return
        self.settings = chosen
        HatchTool._last = dict(chosen)
        self._prompt()

    def _prompt(self) -> None:
        self.ctx.prompt(tr(
            "Pick internal point or [Select objects/seTtings], Enter to apply:"))

    def on_option(self, text: str) -> bool:
        t = text.strip().upper()
        if t in ("S", "SELECT"):
            self.mode = "select"
            self.ctx.prompt(tr("Select boundary objects, Enter to apply:"))
            return True
        if t in ("T", "SETTINGS", "K"):
            chosen = self.ctx.ask_hatch(self.settings)
            if chosen is not None:
                self.settings = chosen
                HatchTool._last = dict(chosen)
            self._prompt()
            return True
        return False

    def on_point(self, point) -> None:
        if self.mode == "select":
            e = self.ctx.services.pick_entity(point) if self.ctx.services else None
            if e is not None and _is_boundary(e):
                if e not in self.selected:
                    self.selected.append(e)
                    self.ctx.echo(tr("1 boundary added."))
            else:
                self.ctx.echo(tr("No closed boundary at that point."))
            return
        region = self.ctx.services.hatch_region_at(point) \
            if self.ctx.services else None
        if region is None:
            self.ctx.echo(tr("No closed boundary found at that point."))
            return
        outer, islands = region
        self.outer.append(outer)
        self.islands.extend(islands)
        self.ctx.echo(tr("Boundary found."))

    def on_enter(self) -> None:
        boundaries = list(self.selected) + list(self.outer)
        if not boundaries:
            self.ctx.echo(tr("No boundaries picked."))
            self.ctx.finish()
            return
        s = self.settings
        self.ctx.execute(actions.add_hatch(
            boundaries, s["pattern"], s["scale"], s["angle"],
            color=s.get("color", 256), islands=self.islands))
        self.ctx.echo(tr("Hatch created."))
        self.ctx.finish()


BLOCK_TOOL_CLASSES = {
    "BLOCK": BlockTool,
    "INSERT": InsertTool,
    "EXPLODE": ExplodeTool,
    "HATCH": HatchTool,
}
