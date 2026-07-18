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


class HatchTool(Tool):
    """HATCH: fill closed boundary objects (SOLID or a pattern/scale/angle)."""

    wants_selection = True

    def start(self) -> None:
        self.name = "HATCH"
        self._bounds: list = []
        self._pattern = "SOLID"
        self._scale = 1.0
        self._angle = 0.0
        self._await: str | None = None

    def _is_boundary(self, e) -> bool:
        t = e.dxftype()
        if t == "LWPOLYLINE":
            return bool(e.closed)
        return t in ("CIRCLE", "ELLIPSE")

    def on_selection(self, entities: list) -> None:
        bounds = [e for e in entities if self._is_boundary(e)]
        if not bounds:
            self.ctx.echo(tr("Select closed boundary objects (polyline/circle)."))
            self.ctx.finish()
            return
        self._bounds = bounds
        self.ctx.prompt(
            tr("HATCH [Pattern/Scale/Angle] <{p}, Enter to apply>:",
               p=self._pattern))

    def on_option(self, text: str) -> bool:
        t = text.strip().upper()
        if self._await is None and t in ("P", "PATTERN"):
            self._await = "pattern"
            self.ctx.prompt(tr("Enter pattern name (SOLID/ANSI31/ANSI37/…):"))
            return True
        if self._await is None and t in ("S", "SCALE"):
            self._await = "scale"
            self.ctx.prompt(tr("Enter pattern scale <{v}>:", v=self._scale))
            return True
        if self._await is None and t in ("A", "ANGLE"):
            self._await = "angle"
            self.ctx.prompt(tr("Enter pattern angle <{v}>:", v=self._angle))
            return True
        if self._await == "pattern":
            self._pattern = text.strip().upper() or "SOLID"
            self._await = None
            self._reprompt()
            return True
        if self._await in ("scale", "angle"):
            try:
                value = float(text)
            except ValueError:
                return False
            setattr(self, "_scale" if self._await == "scale" else "_angle", value)
            self._await = None
            self._reprompt()
            return True
        return False

    def _reprompt(self) -> None:
        self.ctx.prompt(
            tr("HATCH [Pattern/Scale/Angle] <{p}, Enter to apply>:",
               p=self._pattern))

    def on_enter(self) -> None:
        if self._bounds:
            self.ctx.execute(actions.add_hatch(
                self._bounds, self._pattern, self._scale, self._angle))
            self.ctx.echo(tr("Hatch created."))
        self.ctx.finish()


BLOCK_TOOL_CLASSES = {
    "BLOCK": BlockTool,
    "INSERT": InsertTool,
    "EXPLODE": ExplodeTool,
    "HATCH": HatchTool,
}
