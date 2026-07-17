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
        # New entities land on the current layer (AutoCAD/BricsCAD), with
        # ByLayer color/linetype so they inherit the layer's appearance.
        current = document.doc.header.get("$CLAYER", "0")
        if current in document.doc.layers:
            self.entity.dxf.layer = current
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


# -- editing actions (headless, undoable) --------------------------------------

class TransformCommand(Command):
    """Apply a Matrix44 to entities in place; undo applies the inverse."""

    def __init__(self, name: str, entities, matrix) -> None:
        self.name = name
        self.entities = list(entities)
        self.matrix = matrix

    def do(self, document) -> None:
        for e in self.entities:
            e.transform(self.matrix)
        document.dirty = True

    def undo(self, document) -> None:
        import numpy as np
        from ezdxf.math import Matrix44

        inverse = Matrix44(self.matrix)
        inverse.inverse()
        for e in self.entities:
            e.transform(inverse)
        document.dirty = True


class EraseCommand(Command):
    """Unlink entities from modelspace (keeps them alive for exact undo)."""

    name = "ERASE"

    def __init__(self, entities) -> None:
        self.entities = list(entities)

    def do(self, document) -> None:
        msp = document.modelspace()
        for e in self.entities:
            msp.unlink_entity(e)
        document.dirty = True

    def undo(self, document) -> None:
        msp = document.modelspace()
        for e in self.entities:
            msp.add_entity(e)
        document.dirty = True


class CopyEntitiesCommand(Command):
    """Copy entities transformed by a Matrix44; undo removes the copies."""

    name = "COPY"

    def __init__(self, entities, matrix) -> None:
        self.sources = list(entities)
        self.matrix = matrix
        self.copies = []

    def do(self, document) -> None:
        msp = document.modelspace()
        self.copies = []
        for e in self.sources:
            clone = e.copy()
            clone.transform(self.matrix)
            msp.add_entity(clone)
            self.copies.append(clone)
        document.dirty = True

    def undo(self, document) -> None:
        msp = document.modelspace()
        for clone in self.copies:
            msp.delete_entity(clone)
        self.copies = []
        document.dirty = True


class ReplaceEntitiesCommand(Command):
    """Swap old entities for new ones (TRIM/EXTEND/FILLET results).

    Old entities are unlinked (kept alive), new ones created by factories.
    """

    def __init__(self, name: str, old_entities, factories) -> None:
        self.name = name
        self.old_entities = list(old_entities)
        self._factories = list(factories)
        self.new_entities = []

    def do(self, document) -> None:
        msp = document.modelspace()
        for e in self.old_entities:
            msp.unlink_entity(e)
        self.new_entities = [factory(msp) for factory in self._factories]
        document.dirty = True

    def undo(self, document) -> None:
        msp = document.modelspace()
        for e in self.new_entities:
            msp.delete_entity(e)
        self.new_entities = []
        for e in self.old_entities:
            msp.add_entity(e)
        document.dirty = True


def move_entities(entities, dx: float, dy: float) -> TransformCommand:
    from ezdxf.math import Matrix44

    return TransformCommand("MOVE", entities, Matrix44.translate(dx, dy, 0.0))


def rotate_entities(entities, base, angle_deg: float) -> TransformCommand:
    import math as _math

    from ezdxf.math import Matrix44

    m = (Matrix44.translate(-base[0], -base[1], 0.0)
         @ Matrix44.z_rotate(_math.radians(angle_deg))
         @ Matrix44.translate(base[0], base[1], 0.0))
    return TransformCommand("ROTATE", entities, m)


def scale_entities(entities, base, factor: float) -> TransformCommand:
    from ezdxf.math import Matrix44

    m = (Matrix44.translate(-base[0], -base[1], 0.0)
         @ Matrix44.scale(factor, factor, factor)
         @ Matrix44.translate(base[0], base[1], 0.0))
    return TransformCommand("SCALE", entities, m)


def _mirror_matrix(p1, p2):
    import math as _math

    from ezdxf.math import Matrix44

    ang = _math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    return (Matrix44.translate(-p1[0], -p1[1], 0.0)
            @ Matrix44.z_rotate(-ang)
            @ Matrix44.scale(1.0, -1.0, 1.0)
            @ Matrix44.z_rotate(ang)
            @ Matrix44.translate(p1[0], p1[1], 0.0))


def mirror_entities(entities, p1, p2, keep_source: bool = True) -> Command:
    m = _mirror_matrix(p1, p2)
    if keep_source:
        cmd = CopyEntitiesCommand(entities, m)
        cmd.name = "MIRROR"
        return cmd
    return TransformCommand("MIRROR", entities, m)


def copy_entities(entities, dx: float, dy: float) -> CopyEntitiesCommand:
    from ezdxf.math import Matrix44

    return CopyEntitiesCommand(entities, Matrix44.translate(dx, dy, 0.0))


class SnapshotCommand(Command):
    """Undo via full DXF-tag snapshots of the edited entities.

    Grip edits mutate entities in place through many small setters; capturing
    a before/after tag copy is the simplest exact-undo route (the entity keeps
    its handle, so the round-trip stays conservative).
    """

    name = "GRIP"

    def __init__(self, entities) -> None:
        self.entities = list(entities)
        self._before = [e.copy() for e in entities]
        self._after = None

    def commit(self, document) -> None:
        """Call after the in-place edit; captures the 'after' state."""
        self._after = [e.copy() for e in self.entities]
        document.dirty = True

    def do(self, document) -> None:
        if self._after is None:
            return  # first application already happened in place
        for e, snap in zip(self.entities, self._after):
            _restore_entity(e, snap)
        document.dirty = True

    def undo(self, document) -> None:
        for e, snap in zip(self.entities, self._before):
            _restore_entity(e, snap)
        document.dirty = True


def _restore_entity(entity, snapshot) -> None:
    """Copy snapshot's DXF attributes back onto entity (keeps its handle)."""
    for key, value in snapshot.dxf.all_existing_dxf_attribs().items():
        if key == "handle":
            continue
        entity.dxf.set(key, value)
    if entity.dxftype() == "LWPOLYLINE":
        entity.set_points(snapshot.get_points("xyseb"), format="xyseb")
