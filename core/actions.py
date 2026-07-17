# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Headless command dispatch — every prompt keystroke ends up here.

The dispatcher owns the AutoCAD prompt semantics that the UI must not
reimplement: alias resolution, Enter-on-empty repeats the last command,
multi-step prompts (``Z`` then ``E``), Esc cancels. Handlers are plain
callables registered by the application, so the whole flow is testable
without a GUI (the AI-native invariant: every command is a headless
action first).

A handler may return a :class:`Prompt` to ask for more input; the next
submitted line goes to its callback instead of starting a new command.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import math

from core import aliases as aliases_mod
from core.commands import Command
from core.i18n import tr


@dataclass
class Prompt:
    """A continuation: show ``text`` and send the next input line to ``on_input``."""

    text: str
    on_input: Callable[[str], Optional["Prompt"]]


@dataclass
class _Entry:
    handler: Callable[..., Optional[Prompt]]
    phase: int = 0  # 0 = implemented; else "arrives in Phase N"


@dataclass
class Dispatcher:
    """Parses prompt input and routes it to registered command handlers."""

    aliases: dict[str, str] = field(default_factory=aliases_mod.load_aliases)
    echo: Callable[[str], None] = lambda text: None

    def __post_init__(self) -> None:
        self._commands: dict[str, _Entry] = {}
        self._pending: Optional[Prompt] = None
        self.last_command: str = ""

    # -- registration ---------------------------------------------------------
    def register(self, name: str, handler: Callable[..., Optional[Prompt]]) -> None:
        self._commands[name.upper()] = _Entry(handler)

    def register_future(self, name: str, phase: int) -> None:
        """A command in scope but not implemented yet: answer honestly."""
        self._commands[name.upper()] = _Entry(handler=None, phase=phase)

    def known_names(self) -> list[str]:
        """Commands + aliases, for prompt autocompletion."""
        names = set(self._commands)
        names.update(a for a, cmd in self.aliases.items() if cmd in self._commands)
        return sorted(names)

    # -- prompt state ---------------------------------------------------------
    @property
    def pending_prompt(self) -> Optional[str]:
        return self._pending.text if self._pending else None

    def cancel(self) -> None:
        """Esc: abandon any pending multi-step prompt."""
        if self._pending is not None:
            self._pending = None
            self.echo(tr("*Cancel*"))

    # -- input ----------------------------------------------------------------
    def submit(self, raw: str) -> None:
        """One line from the prompt (Enter or Space already stripped)."""
        text = raw.strip()

        if self._pending is not None:
            prompt = self._pending
            self._pending = None
            self._continue(prompt.on_input(text))
            return

        if not text:
            # AutoCAD: Enter on an empty prompt repeats the last command.
            if self.last_command:
                self._run(self.last_command, [])
            return

        tokens = text.split()
        name = aliases_mod.resolve(tokens[0], self.aliases)
        self._run(name, tokens[1:])

    def _run(self, name: str, args: list[str]) -> None:
        entry = self._commands.get(name)
        if entry is None:
            self.echo(tr('Unknown command "{name}".', name=name))
            return
        self.last_command = name
        if entry.handler is None:
            self.echo(tr("{name}: not available yet (arrives in Phase {phase}).",
                         name=name, phase=entry.phase))
            return
        self._continue(entry.handler(*args) if args else entry.handler())

    def _continue(self, result: Optional[Prompt]) -> None:
        if isinstance(result, Prompt):
            self._pending = result
            self.echo(result.text)


# -- drawing actions (headless, undoable) --------------------------------------
#
# Every mutation is a Command: do() creates the entity in modelspace, undo()
# deletes it. The ezdxf document IS the model — no shadow data structures.

class AddEntityCommand(Command):
    """Create one entity via a factory(msp) -> entity; undo deletes it."""

    def __init__(self, name: str, factory) -> None:
        self.name = name
        self._factory = factory
        self.entity = None

    def do(self, document) -> None:
        self.entity = self._factory(document.modelspace())
        document.dirty = True

    def undo(self, document) -> None:
        if self.entity is not None:
            document.modelspace().delete_entity(self.entity)
            self.entity = None
        document.dirty = True


def add_line(p1, p2) -> AddEntityCommand:
    return AddEntityCommand(
        "LINE", lambda msp: msp.add_line((p1[0], p1[1]), (p2[0], p2[1])))


def add_circle(center, radius: float) -> AddEntityCommand:
    return AddEntityCommand(
        "CIRCLE", lambda msp: msp.add_circle((center[0], center[1]), radius))


def circle_from_2p(p1, p2) -> tuple[tuple[float, float], float]:
    center = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    return center, math.dist(p1, p2) / 2.0


def circle_from_3p(p1, p2, p3) -> tuple[tuple[float, float], float]:
    """Circumcenter/radius; raises ValueError for collinear points."""
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        raise ValueError("collinear points")
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


def add_arc_3p(p1, p2, p3) -> AddEntityCommand:
    """Arc from start, a point on the arc, and end (AutoCAD ARC 3P)."""
    center, radius = circle_from_3p(p1, p2, p3)
    a1 = math.degrees(math.atan2(p1[1] - center[1], p1[0] - center[0]))
    a2 = math.degrees(math.atan2(p2[1] - center[1], p2[0] - center[0]))
    a3 = math.degrees(math.atan2(p3[1] - center[1], p3[0] - center[0]))
    # DXF arcs run counterclockwise from start to end; flip when the middle
    # point is not on the ccw sweep.
    if ((a2 - a1) % 360.0) > ((a3 - a1) % 360.0):
        a1, a3 = a3, a1
    return AddEntityCommand(
        "ARC",
        lambda msp: msp.add_arc((center[0], center[1]), radius, a1, a3))


def add_polyline(points, closed: bool = False) -> AddEntityCommand:
    pts = [(p[0], p[1]) for p in points]
    return AddEntityCommand(
        "PLINE", lambda msp: msp.add_lwpolyline(pts, close=closed))


def add_rectangle(p1, p2) -> AddEntityCommand:
    pts = [(p1[0], p1[1]), (p2[0], p1[1]), (p2[0], p2[1]), (p1[0], p2[1])]
    return AddEntityCommand(
        "RECTANG", lambda msp: msp.add_lwpolyline(pts, close=True))


def polygon_points(center, vertex, sides: int) -> list[tuple[float, float]]:
    """Regular polygon inscribed, one vertex at ``vertex``."""
    r = math.dist(center, vertex)
    a0 = math.atan2(vertex[1] - center[1], vertex[0] - center[0])
    return [
        (center[0] + r * math.cos(a0 + i * math.tau / sides),
         center[1] + r * math.sin(a0 + i * math.tau / sides))
        for i in range(sides)
    ]


def add_polygon(center, vertex, sides: int) -> AddEntityCommand:
    pts = polygon_points(center, vertex, sides)
    return AddEntityCommand(
        "POLYGON", lambda msp: msp.add_lwpolyline(pts, close=True))
