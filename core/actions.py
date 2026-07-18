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


# -- blocks (Phase 6) ----------------------------------------------------------

class CreateBlockCommand(Command):
    """BLOCK: define a block from entities and convert them to a reference.

    Matches AutoCAD's default "Convert to block": the selection is packed into
    a new block definition and replaced in place by one INSERT at the base
    point. The block's base point is the picked point, so — inserted at that
    same point — the reference lands exactly over the originals.
    """

    name = "BLOCK"

    def __init__(self, block_name: str, base_point, entities) -> None:
        self.block_name = block_name
        self.base = base_point
        self.sources = list(entities)
        self.insert = None
        self._defined = False

    def do(self, document) -> None:
        doc = document.doc
        msp = document.modelspace()
        if self.block_name not in doc.blocks:
            blk = doc.blocks.new(
                name=self.block_name,
                base_point=(self.base[0], self.base[1]))
            for e in self.sources:
                blk.add_entity(e.copy())
            self._defined = True
        for e in self.sources:
            msp.unlink_entity(e)
        self.insert = msp.add_blockref(
            self.block_name, (self.base[0], self.base[1]))
        document.dirty = True

    def undo(self, document) -> None:
        doc = document.doc
        msp = document.modelspace()
        if self.insert is not None:
            msp.delete_entity(self.insert)
            self.insert = None
        for e in self.sources:
            msp.add_entity(e)
        if self._defined and self.block_name in doc.blocks:
            doc.blocks.delete_block(self.block_name, safe=False)
            self._defined = False
        document.dirty = True


def create_block(block_name, base_point, entities) -> CreateBlockCommand:
    return CreateBlockCommand(block_name, base_point, entities)


def insert_block(name, point, xscale=1.0, yscale=None,
                 rotation=0.0) -> AddEntityCommand:
    ys = xscale if yscale is None else yscale
    return AddEntityCommand(
        "INSERT",
        lambda msp: msp.add_blockref(
            name, (point[0], point[1]),
            dxfattribs={"xscale": xscale, "yscale": ys, "rotation": rotation}))


class ExplodeCommand(Command):
    """EXPLODE: replace INSERT/POLYLINE with their component entities.

    Component entities come from ``virtual_entities`` (already placed in world
    coordinates); the originals are unlinked (kept alive for exact undo).
    """

    name = "EXPLODE"

    def __init__(self, entities) -> None:
        self.sources = list(entities)
        self.pieces: list = []

    def do(self, document) -> None:
        msp = document.modelspace()
        self.pieces = []
        for e in self.sources:
            if e.dxftype() not in ("INSERT", "LWPOLYLINE", "POLYLINE"):
                continue
            parts = []
            for v in e.virtual_entities():
                msp.add_entity(v)
                parts.append(v)
            msp.unlink_entity(e)
            self.pieces.append((e, parts))
        document.dirty = True

    def undo(self, document) -> None:
        msp = document.modelspace()
        for original, parts in self.pieces:
            for v in parts:
                msp.delete_entity(v)
            msp.add_entity(original)
        self.pieces = []
        document.dirty = True


def explode_entities(entities) -> ExplodeCommand:
    return ExplodeCommand(entities)


# -- hatch (Phase 6) -----------------------------------------------------------

def _std_patterns():
    """The 172 predefined ACAD/ISO hatch patterns, loaded once."""
    global _STD_PATTERNS
    try:
        return _STD_PATTERNS
    except NameError:
        from ezdxf.tools import pattern as _pat
        _STD_PATTERNS = _pat.load()
        return _STD_PATTERNS


def hatch_pattern_names() -> list:
    """Sorted predefined pattern names (for the hatch style picker)."""
    return sorted(_std_patterns().keys())


def _add_boundary(hatch, item, flags: int) -> None:
    """Add one boundary path: an ezdxf entity (closed) or a point list."""
    if isinstance(item, (list, tuple)) and item and isinstance(item[0], (list, tuple)):
        pts = [(p[0], p[1]) for p in item]
        hatch.paths.add_polyline_path(pts, is_closed=True, flags=flags)
        return
    t = item.dxftype()
    if t == "LWPOLYLINE":
        pts = [(p[0], p[1], p[2]) for p in item.get_points("xyb")]
        hatch.paths.add_polyline_path(pts, is_closed=True, flags=flags)
    elif t == "CIRCLE":
        c = item.dxf.center
        path = hatch.paths.add_edge_path(flags=flags)
        path.add_arc((c.x, c.y), item.dxf.radius, 0, 360)
    elif t == "ELLIPSE":
        c = item.dxf.center
        maj = item.dxf.major_axis
        path = hatch.paths.add_edge_path(flags=flags)
        path.add_ellipse((c.x, c.y), (maj.x, maj.y), item.dxf.ratio, 0, 360)
    else:
        from core.hatch_boundary import boundary_polygon

        poly = boundary_polygon(item)
        if poly:
            hatch.paths.add_polyline_path(poly, is_closed=True, flags=flags)


def add_hatch(boundaries, pattern="SOLID", scale=1.0, angle=0.0,
              color=256, islands=None) -> AddEntityCommand:
    """SOLID or a predefined pattern (ANSI31…) inside closed boundaries.

    ``boundaries`` and ``islands`` are ezdxf entities or point lists. Islands
    are added as inner paths so the region is filled with holes ("Normal"
    island style). ``color`` is an ACI; 256 = ByLayer (AutoCAD's default for a
    new hatch).
    """
    from ezdxf.lldxf.const import (
        BOUNDARY_PATH_EXTERNAL, BOUNDARY_PATH_OUTERMOST)

    name = str(pattern).upper()
    aci = 7 if color in (256, 0) else color

    def factory(msp):
        h = msp.add_hatch(color=aci)
        h.dxf.hatch_style = 0   # Normal: nested islands alternate fill
        if name == "SOLID":
            h.set_solid_fill(color=aci)
        else:
            defn = _std_patterns().get(name)
            h.set_pattern_fill(name, color=aci, angle=angle, scale=scale,
                               definition=defn)
        for b in boundaries:
            _add_boundary(h, b, BOUNDARY_PATH_EXTERNAL)
        for isl in (islands or ()):
            _add_boundary(h, isl, BOUNDARY_PATH_OUTERMOST)
        h.dxf.color = color   # keep ByLayer sentinel when requested
        return h
    return AddEntityCommand("HATCH", factory)


# -- dimensions (create) -------------------------------------------------------

class AddDimensionCommand(Command):
    """Create a dimension: the factory builds it, ``render()`` draws the
    graphics into an anonymous *D block. Undo removes the dimension and that
    block; the current dimension style ($DIMSTYLE) is used at render time.
    """

    name = "DIMENSION"

    def __init__(self, factory) -> None:
        self._factory = factory
        self.dim = None
        self._block_name = None

    def do(self, document) -> None:
        override = self._factory(document.modelspace(), document)
        override.render()
        self.dim = override.dimension
        self._block_name = self.dim.dxf.get("geometry", None)
        current = document.doc.header.get("$CLAYER", "0")
        if current in document.doc.layers:
            self.dim.dxf.layer = current
        document.dirty = True

    def undo(self, document) -> None:
        msp = document.modelspace()
        if self.dim is not None and self.dim.is_alive:
            msp.delete_entity(self.dim)
        if self._block_name and self._block_name in document.doc.blocks:
            try:
                document.doc.blocks.delete_block(self._block_name, safe=False)
            except Exception:
                pass
        self.dim = None
        document.dirty = True


def _current_dimstyle(document) -> str:
    name = document.doc.header.get("$DIMSTYLE", "Standard")
    return name if name in document.doc.dimstyles else "Standard"


def dim_linear(p1, p2, location) -> AddDimensionCommand:
    """DIMLINEAR: horizontal or vertical, chosen by the dimension-line pick."""
    mid = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    horizontal = abs(location[1] - mid[1]) >= abs(location[0] - mid[0])
    angle = 0.0 if horizontal else 90.0

    def factory(msp, document):
        return msp.add_linear_dim(
            base=(location[0], location[1]),
            p1=(p1[0], p1[1]), p2=(p2[0], p2[1]),
            angle=angle, dimstyle=_current_dimstyle(document))
    return AddDimensionCommand(factory)


def dim_aligned(p1, p2, location) -> AddDimensionCommand:
    """DIMALIGNED: dimension parallel to p1->p2, offset to the picked side."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length      # unit normal
    distance = (location[0] - p1[0]) * nx + (location[1] - p1[1]) * ny

    def factory(msp, document):
        return msp.add_aligned_dim(
            p1=(p1[0], p1[1]), p2=(p2[0], p2[1]),
            distance=distance, dimstyle=_current_dimstyle(document))
    return AddDimensionCommand(factory)


def dim_radius(center, radius: float, location) -> AddDimensionCommand:
    angle = math.degrees(math.atan2(location[1] - center[1],
                                    location[0] - center[0]))

    def factory(msp, document):
        return msp.add_radius_dim(
            center=(center[0], center[1]), radius=radius, angle=angle,
            dimstyle=_current_dimstyle(document))
    return AddDimensionCommand(factory)


def dim_diameter(center, radius: float, location) -> AddDimensionCommand:
    angle = math.degrees(math.atan2(location[1] - center[1],
                                    location[0] - center[0]))

    def factory(msp, document):
        return msp.add_diameter_dim(
            center=(center[0], center[1]), radius=radius, angle=angle,
            dimstyle=_current_dimstyle(document))
    return AddDimensionCommand(factory)


class PasteCommand(Command):
    """Paste clipboard entities, translated so the base point lands on the
    target. Each paste makes fresh copies, so the clipboard stays reusable."""

    name = "PASTE"

    def __init__(self, sources, dx: float, dy: float) -> None:
        self.sources = list(sources)
        self.dx = dx
        self.dy = dy
        self.copies: list = []

    def do(self, document) -> None:
        from ezdxf.math import Matrix44

        msp = document.modelspace()
        m = Matrix44.translate(self.dx, self.dy, 0.0)
        self.copies = []
        for e in self.sources:
            clone = e.copy()
            clone.transform(m)
            msp.add_entity(clone)
            self.copies.append(clone)
        document.dirty = True

    def undo(self, document) -> None:
        msp = document.modelspace()
        for clone in self.copies:
            msp.delete_entity(clone)
        self.copies = []
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


def apply_in_place(history, entities, mutate) -> None:
    """Run an in-place mutation on ``entities`` with exact snapshot undo.

    For edits that ezdxf performs through many small setters (a Vec3 component,
    text contents) a before/after tag snapshot is the simplest reversible route
    — same mechanism the grip drag uses. Records straight onto the history so
    the change joins the normal undo stack.
    """
    snap = SnapshotCommand(entities)
    mutate()
    snap.commit(history.document)
    history._undo.append(snap)
    history._redo.clear()


class SetPropertyCommand(Command):
    """Set a DXF property (layer/color/linetype/lineweight) on entities.

    Color/linetype/lineweight take AutoCAD's ByLayer sentinels: color 256 =
    ByLayer, linetype "ByLayer", lineweight -1 = ByLayer. Undo restores each
    entity's previous value individually.
    """

    name = "properties"

    def __init__(self, entities, prop: str, value) -> None:
        self.entities = list(entities)
        self.prop = prop
        self.value = value
        self._old = []

    def do(self, document) -> None:
        self._old = []
        for e in self.entities:
            self._old.append(e.dxf.get(self.prop, None))
            e.dxf.set(self.prop, self.value)
        document.dirty = True

    def undo(self, document) -> None:
        for e, old in zip(self.entities, self._old):
            if old is None:
                e.dxf.discard(self.prop)
            else:
                e.dxf.set(self.prop, old)
        document.dirty = True


def add_ellipse(center, major_axis, ratio: float) -> AddEntityCommand:
    """major_axis: vector from center to the major-axis endpoint. ratio =
    minor/major in (0, 1]."""
    return AddEntityCommand(
        "ELLIPSE",
        lambda msp: msp.add_ellipse((center[0], center[1]),
                                    major_axis=(major_axis[0], major_axis[1]),
                                    ratio=ratio))


def ellipse_from_axis(p1, p2, other_dist: float):
    """Axis endpoints p1,p2 + distance to the other axis -> (center, major, ratio)."""
    center = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    major = ((p2[0] - p1[0]) / 2.0, (p2[1] - p1[1]) / 2.0)
    major_len = math.hypot(*major)
    ratio = min(1.0, other_dist / major_len) if major_len > 1e-12 else 1.0
    return center, major, ratio


def ellipse_from_center(center, axis_end, other_dist: float):
    """Center + major-axis endpoint + distance to the other axis."""
    major = (axis_end[0] - center[0], axis_end[1] - center[1])
    major_len = math.hypot(*major)
    ratio = min(1.0, other_dist / major_len) if major_len > 1e-12 else 1.0
    return center, major, ratio


def add_point(pos) -> AddEntityCommand:
    return AddEntityCommand(
        "POINT", lambda msp: msp.add_point((pos[0], pos[1])))


def _current_text_style(msp) -> str:
    """The document's current text style ($TEXTSTYLE), AutoCAD-style."""
    name = msp.doc.header.get("$TEXTSTYLE", "Standard")
    return name if name in msp.doc.styles else "Standard"


def add_text(pos, text: str, height: float, rotation: float = 0.0) -> AddEntityCommand:
    def make(msp):
        entity = msp.add_text(
            text, height=height,
            dxfattribs={"rotation": rotation, "style": _current_text_style(msp)})
        entity.set_placement((pos[0], pos[1]))
        return entity
    return AddEntityCommand("TEXT", make)


def add_mtext(p1, p2, text: str, char_height: float) -> AddEntityCommand:
    width = abs(p2[0] - p1[0])
    top_left = (min(p1[0], p2[0]), max(p1[1], p2[1]))

    def make(msp):
        m = msp.add_mtext(text, dxfattribs={"char_height": char_height,
                                            "width": width,
                                            "style": _current_text_style(msp)})
        m.set_location(top_left)
        return m
    return AddEntityCommand("MTEXT", make)


def add_arc_sce(start, center, end) -> AddEntityCommand:
    """Arc by Start, Center, End (AutoCAD's second arc method).

    Radius from center->start; ccw from start angle to end angle (the end
    point sets the direction; its distance is ignored, AutoCAD does the same).
    """
    radius = math.hypot(start[0] - center[0], start[1] - center[1])
    a_start = math.degrees(math.atan2(start[1] - center[1], start[0] - center[0]))
    a_end = math.degrees(math.atan2(end[1] - center[1], end[0] - center[0]))
    return AddEntityCommand(
        "ARC", lambda msp: msp.add_arc((center[0], center[1]), radius,
                                       a_start, a_end))
