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
from typing import Callable, Optional

Point = tuple[float, float]


@dataclass
class ToolContext:
    execute: Callable[[object], None]          # run an undoable Command
    prompt: Callable[[str], None]              # show prompt text
    echo: Callable[[str], None]                # log a message
    finish: Callable[[], None]                 # tool is done: deactivate


@dataclass
class Tool:
    ctx: ToolContext
    name: str = "TOOL"
    last_point: Optional[Point] = None         # anchor for @rel / distances
    preview_points: list[Point] = field(default_factory=list)

    def start(self) -> None: ...

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
