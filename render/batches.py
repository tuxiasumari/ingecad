# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""CPU-side scene data: primitive buckets packed into GPU-ready arrays.

Coordinates arrive in world units as float64 (UTM drawings live near
E=500 000 — architectural principle #3). ``pack`` subtracts the scene origin
(the drawing's center) *in float64* and only then casts to float32, so the
precision loss lands in coordinates that are small by construction. The
viewport adds the origin back when building its matrix.

Vertex formats (colors as normalized uint8 — half the memory of floats):
- standard: [x f32, y f32, rgba u8x4]                     -> 12 bytes
- thick:    [x f32, y f32, nx f32, ny f32, rgba u8x4]     -> 20 bytes

Primitives are reordered by a coarse spatial grid inside each (layer, color,
lineweight, kind) bucket, and draw ranges carry world bounds so the viewport
can cull to the visible rect and skip illegible text (a cadastre's 43 M
glyph vertices are 90 % of the scene but invisible below a few pixels).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

VERTEX_DTYPE = np.dtype([("pos", "<f4", 2), ("rgba", "u1", 4)])          # 12 B
THICK_DTYPE = np.dtype([("pos", "<f4", 2), ("normal", "<f4", 2),
                        ("rgba", "u1", 4)])                              # 20 B

# AutoCAD LWT displays weights up to 0.25 mm as one pixel; above that the
# line grows with the weight. Same split here: thin -> GL_LINES, thick ->
# screen-constant quads expanded in the shader.
THIN_MAX_MM = 0.25

# Spatial grid resolution per axis for view culling.
GRID_DIV = 16


def parse_color(color: str) -> tuple[float, float, float, float]:
    """``#rrggbb`` or ``#rrggbbaa`` (ezdxf backend format) -> RGBA floats."""
    h = color.lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    a = int(h[6:8], 16) / 255.0 if len(h) >= 8 else 1.0
    return r, g, b, a


def _color_u8(color: str) -> np.ndarray:
    h = color.lstrip("#")
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    a = int(h[6:8], 16) if len(h) >= 8 else 255
    return np.array([r, g, b, a], dtype=np.uint8)


@dataclass
class Bucket:
    """Primitives of one (layer, color, lineweight, kind) group, float64."""

    layer: str
    color: str
    lineweight: float = 0.25                              # mm, resolved
    kind: str = ""                                        # "T" = text glyphs
    lines: list[float] = field(default_factory=list)      # x,y per endpoint
    triangles: list[float] = field(default_factory=list)  # x,y per corner
    points: list[float] = field(default_factory=list)     # x,y per point
    # Owner handle per PRIMITIVE (one entry per line/triangle/point). Survives
    # the grid sort and lets pack() build a handle -> vertex-runs map so the
    # viewport can hide an edited entity instantly, without a regen (the
    # surgical-display building block).
    lines_owner: list = field(default_factory=list)
    triangles_owner: list = field(default_factory=list)
    points_owner: list = field(default_factory=list)
    text_height_sum: float = 0.0                          # glyph extents, world
    text_count: int = 0

    @property
    def avg_text_height(self) -> float:
        return self.text_height_sum / self.text_count if self.text_count else 0.0


@dataclass
class DrawRange:
    """A contiguous vertex run inside a packed array."""

    layer: str
    first: int  # vertex index (not byte index)
    count: int
    lineweight: float = 0.25  # mm; drives u_half_world for thick ranges


class Batch:
    """One primitive type packed: interleaved array + culling metadata."""

    def __init__(self, data: np.ndarray, ranges: list[DrawRange],
                 bounds: Optional[np.ndarray] = None,
                 is_text: Optional[np.ndarray] = None,
                 text_height: Optional[np.ndarray] = None) -> None:
        self.data = data                    # structured array
        self.ranges = ranges
        # Parallel arrays for vectorized culling (one row per range):
        n = len(ranges)
        self.firsts = np.fromiter((r.first for r in ranges), np.int64, n)
        self.counts = np.fromiter((r.count for r in ranges), np.int64, n)
        self.bounds = bounds                # (n, 4) world min_x,min_y,max_x,max_y
        self.is_text = is_text              # (n,) bool
        self.text_height = text_height      # (n,) avg glyph height, world units

    @property
    def vertex_count(self) -> int:
        return len(self.data)

    def positions(self) -> np.ndarray:
        """(N, 2) float32 view of the vertex positions (tests, picking)."""
        return self.data["pos"]

    def visible_runs(self, view_rect, px_per_unit: float,
                     min_text_px: float) -> list[tuple[int, int]]:
        """Merged (first, count) vertex runs to draw for this view."""
        if not len(self.ranges):
            return []
        if self.bounds is None:
            return [(0, self.vertex_count)]
        x0, y0, x1, y1 = view_rect
        vis = (
            (self.bounds[:, 0] <= x1) & (self.bounds[:, 2] >= x0)
            & (self.bounds[:, 1] <= y1) & (self.bounds[:, 3] >= y0)
        )
        if self.is_text is not None and min_text_px > 0.0:
            legible = self.text_height * px_per_unit >= min_text_px
            vis &= ~self.is_text | legible
        idx = np.nonzero(vis)[0]
        if len(idx) == 0:
            return []
        firsts = self.firsts[idx]
        counts = self.counts[idx]
        # Merge runs that are contiguous in the buffer into single draws.
        breaks = np.nonzero(firsts[1:] != firsts[:-1] + counts[:-1])[0] + 1
        starts = np.concatenate(([0], breaks))
        ends = np.concatenate((breaks, [len(idx)]))
        return [
            (int(firsts[s]), int(firsts[e - 1] + counts[e - 1] - firsts[s]))
        for s, e in zip(starts, ends)]


def _empty_batch(dtype=VERTEX_DTYPE) -> Batch:
    return Batch(np.empty(0, dtype=dtype), [])


@dataclass
class Scene:
    """Everything the viewport needs to draw one document."""

    origin: tuple[float, float]                    # float64 world center
    extents: tuple[float, float, float, float]     # world min_x, min_y, max_x, max_y
    lines: Batch                                   # thin lines
    thick: Batch                                   # lineweight quads
    triangles: Batch
    points: Batch
    # Entities the tolerant frontend could not draw ("TYPE(#handle): why").
    skipped: list[str] = field(default_factory=list)
    # Paperspace layout shown instead of an empty modelspace, if any.
    layout_name: Optional[str] = None
    # Canvas color for that layout (RGBA floats); None = viewport default.
    background: Optional[tuple[float, float, float, float]] = None
    # Flattening distance used for the build (reused by overlay regens).
    flatten: float = 0.01
    # handle -> [(batch_name, first_vertex, count)] for surgical hiding.
    handle_ranges: dict = field(default_factory=dict)
    # Handles currently hidden (edited entities awaiting the next regen).
    hidden: set = field(default_factory=set)

    @property
    def is_empty(self) -> bool:
        return (
            self.lines.vertex_count == 0
            and self.thick.vertex_count == 0
            and self.triangles.vertex_count == 0
            and self.points.vertex_count == 0
        )


def _grid_cells(prims_xy: np.ndarray, extents, verts_per_prim: int) -> np.ndarray:
    """Cell id per primitive from its first vertex (cheap, good enough)."""
    min_x, min_y, max_x, max_y = extents
    w = max(max_x - min_x, 1e-12)
    h = max(max_y - min_y, 1e-12)
    p0 = prims_xy[::verts_per_prim]
    cx = np.clip(((p0[:, 0] - min_x) / w * GRID_DIV).astype(np.int32), 0, GRID_DIV - 1)
    cy = np.clip(((p0[:, 1] - min_y) / h * GRID_DIV).astype(np.int32), 0, GRID_DIV - 1)
    return cy * GRID_DIV + cx


def _pack_standard(
    buckets: list[Bucket], attr: str, verts_per_prim: int,
    origin: tuple[float, float], extents,
    batch_name: str = "", handle_ranges: Optional[dict] = None,
) -> Batch:
    ox, oy = origin
    chunks: list[np.ndarray] = []
    ranges: list[DrawRange] = []
    bounds: list[np.ndarray] = []
    is_text: list[bool] = []
    text_h: list[float] = []
    first = 0
    for bucket in buckets:
        coords = getattr(bucket, attr)
        if not coords:
            continue
        if attr == "lines" and bucket.lineweight > THIN_MAX_MM:
            continue  # packed as quads by _pack_thick
        xy = np.asarray(coords, dtype=np.float64).reshape(-1, 2)
        n_prims = len(xy) // verts_per_prim
        # Spatial order inside the bucket, so cell ranges are contiguous.
        cells = _grid_cells(xy, extents, verts_per_prim)
        order = np.argsort(cells, kind="stable")
        xy = xy.reshape(n_prims, verts_per_prim, 2)[order]
        cells = cells[order]

        verts = np.empty(n_prims * verts_per_prim, dtype=VERTEX_DTYPE)
        flat = xy.reshape(-1, 2)
        verts["pos"][:, 0] = flat[:, 0] - ox  # float64 math, float32 store
        verts["pos"][:, 1] = flat[:, 1] - oy
        verts["rgba"] = _color_u8(bucket.color)
        chunks.append(verts)

        # Record which vertex runs belong to each entity handle (for the
        # viewport's surgical hide). Owners follow the same grid permutation.
        owners = getattr(bucket, attr + "_owner")
        if owners and handle_ranges is not None:
            owner_arr = np.asarray(owners, dtype=object)[order.tolist()]
            i = 0
            while i < n_prims:
                h = owner_arr[i]
                j = i
                while j < n_prims and owner_arr[j] == h:
                    j += 1
                if h is not None:
                    handle_ranges.setdefault(h, []).append(
                        (batch_name, first + i * verts_per_prim,
                         (j - i) * verts_per_prim))
                i = j

        # One range per occupied cell.
        cell_breaks = np.nonzero(cells[1:] != cells[:-1])[0] + 1
        starts = np.concatenate(([0], cell_breaks))
        ends = np.concatenate((cell_breaks, [n_prims]))
        for s, e in zip(starts, ends):
            block = xy[s:e].reshape(-1, 2)
            ranges.append(DrawRange(
                bucket.layer,
                first + s * verts_per_prim,
                (e - s) * verts_per_prim,
                bucket.lineweight,
            ))
            bounds.append(np.array([
                block[:, 0].min(), block[:, 1].min(),
                block[:, 0].max(), block[:, 1].max(),
            ]))
            is_text.append(bucket.kind == "T")
            text_h.append(bucket.avg_text_height)
        first += n_prims * verts_per_prim
    if not chunks:
        return _empty_batch()
    return Batch(
        np.concatenate(chunks),
        ranges,
        np.vstack(bounds),
        np.asarray(is_text, dtype=bool),
        np.asarray(text_h, dtype=np.float64),
    )


def _pack_thick(buckets: list[Bucket], origin: tuple[float, float], extents) -> Batch:
    """Thick line segments -> quads (2 triangles, 6 vertices) per segment.

    Each vertex stores the segment point plus a unit perpendicular; the
    shader expands it by the half lineweight in world units, so thickness
    stays constant in pixels at any zoom (AutoCAD LWT display).
    """
    ox, oy = origin
    chunks: list[np.ndarray] = []
    ranges: list[DrawRange] = []
    bounds: list[np.ndarray] = []
    first = 0
    for bucket in buckets:
        if not bucket.lines or bucket.lineweight <= THIN_MAX_MM:
            continue
        seg = np.asarray(bucket.lines, dtype=np.float64).reshape(-1, 2, 2)
        d = seg[:, 1] - seg[:, 0]
        length = np.hypot(d[:, 0], d[:, 1])
        ok = length > 0.0
        seg, d, length = seg[ok], d[ok], length[ok]
        if len(seg) == 0:
            continue
        cells = _grid_cells(seg.reshape(-1, 2), extents, 2)
        order = np.argsort(cells, kind="stable")
        seg, d, length, cells = seg[order], d[order], length[order], cells[order]

        normal = np.column_stack((-d[:, 1], d[:, 0])) / length[:, None]
        p0 = seg[:, 0] - (ox, oy)
        p1 = seg[:, 1] - (ox, oy)
        n_seg = len(seg)
        verts = np.empty((n_seg, 6), dtype=THICK_DTYPE)
        # Triangle pair: (p0,+n) (p0,-n) (p1,+n) / (p1,+n) (p0,-n) (p1,-n)
        corners = ((p0, 1), (p0, -1), (p1, 1), (p1, 1), (p0, -1), (p1, -1))
        for i, (p, sign) in enumerate(corners):
            verts["pos"][:, i] = p
            verts["normal"][:, i] = normal * sign
        verts["rgba"] = _color_u8(bucket.color)
        chunks.append(verts.reshape(-1))

        cell_breaks = np.nonzero(cells[1:] != cells[:-1])[0] + 1
        starts = np.concatenate(([0], cell_breaks))
        ends = np.concatenate((cell_breaks, [n_seg]))
        for s, e in zip(starts, ends):
            block = seg[s:e].reshape(-1, 2)
            ranges.append(DrawRange(
                bucket.layer, first + s * 6, (e - s) * 6, bucket.lineweight))
            bounds.append(np.array([
                block[:, 0].min(), block[:, 1].min(),
                block[:, 0].max(), block[:, 1].max(),
            ]))
        first += n_seg * 6
    if not chunks:
        return _empty_batch(THICK_DTYPE)
    return Batch(np.concatenate(chunks), ranges, np.vstack(bounds))


def _world_extents(buckets: list[Bucket]) -> tuple[float, float, float, float]:
    min_x = min_y = np.inf
    max_x = max_y = -np.inf
    for bucket in buckets:
        for coords in (bucket.lines, bucket.triangles, bucket.points):
            if not coords:
                continue
            xy = np.asarray(coords, dtype=np.float64).reshape(-1, 2)
            min_x = min(min_x, xy[:, 0].min())
            min_y = min(min_y, xy[:, 1].min())
            max_x = max(max_x, xy[:, 0].max())
            max_y = max(max_y, xy[:, 1].max())
    if min_x > max_x:  # nothing drawable
        return (0.0, 0.0, 0.0, 0.0)
    return (float(min_x), float(min_y), float(max_x), float(max_y))


def pack(buckets: dict[tuple, Bucket]) -> Scene:
    """Pack backend buckets into a Scene, origin at the drawing's center."""
    # Stable order: by layer then color, so ranges group per layer for the
    # future visibility toggle.
    ordered = [buckets[k] for k in sorted(buckets)]
    extents = _world_extents(ordered)
    origin = ((extents[0] + extents[2]) / 2.0, (extents[1] + extents[3]) / 2.0)
    hr: dict = {}
    scene = Scene(
        origin=origin,
        extents=extents,
        lines=_pack_standard(ordered, "lines", 2, origin, extents, "lines", hr),
        thick=_pack_thick(ordered, origin, extents),
        triangles=_pack_standard(ordered, "triangles", 3, origin, extents,
                                 "triangles", hr),
        points=_pack_standard(ordered, "points", 1, origin, extents, "points", hr),
    )
    scene.handle_ranges = hr
    return scene
