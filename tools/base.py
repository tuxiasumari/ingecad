# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Interactive tool base: a UI-agnostic state machine fed with points.

A tool receives points (from snapped mouse clicks or typed coordinates —
it does not care which) and option keywords, asks for the next input via
prompts, and executes headless Commands. The GUI supplies a ToolContext
with callbacks; tests supply fakes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, ClassVar, Optional

Point = tuple[float, float]


@dataclass
class ToolContext:
    execute: Callable[[object], None]          # run an undoable Command
    prompt: Callable[[str], None]              # show prompt text
    echo: Callable[[str], None]                # log a message
    finish: Callable[[], None]                 # tool is done: deactivate
    # Editing services (selection, entity picking, edge geometry). The GUI
    # supplies the ToolController; tests supply a fake with the same duck
    # methods: pick_entity(point), edges_geometry(handles|None).
    services: object = None


@dataclass
class Tool:
    ctx: ToolContext
    name: str = "TOOL"
    last_point: Optional[Point] = None         # anchor for @rel / distances
    preview_points: list[Point] = field(default_factory=list)
    # Editing tools consume the current selection (noun-verb) or ask for
    # one ("Select objects:") before their point prompts. ClassVar on
    # purpose: a dataclass FIELD would make the generated __init__ reset
    # the subclass override back to False on every instance.
    wants_selection: ClassVar[bool] = False
    shift: ClassVar[bool] = False              # Shift held at last click
    # Tools whose clicks pick ENTITIES (trim targets, fillet lines) get raw
    # cursor points: AutoCAD suppresses osnap during object picking.
    entity_picker: ClassVar[bool] = False
    # Tools whose target phase also accepts window/crossing rectangles
    # (TRIM/EXTEND): a drag or empty-click window feeds many targets.
    accepts_target_windows: ClassVar[bool] = False

    def on_target_entities(self, entities: list, rect) -> None:
        """Targets captured by a window/crossing during the tool's pick phase."""

    def start(self) -> None: ...

    def selection_prompt(self) -> str:
        from core.i18n import tr

        return tr("Select objects (Enter when done):")

    def on_selection(self, entities: list) -> None: ...

    def on_point(self, point: Point) -> None: ...

    def on_option(self, text: str) -> bool:
        """A non-coordinate token from the prompt. True if consumed."""
        return False

    def on_enter(self) -> None:
        """Enter on an empty prompt: finish where that is meaningful."""
        self.ctx.finish()

    def on_cancel(self) -> None:
        self.ctx.finish()

    def preview_segments(self, cursor: Point) -> list[tuple[Point, Point]]:
        """Rubber-band segments from committed points to the cursor."""
        return []
