# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""CPU-side scene data: primitive buckets packed into GPU-ready arrays.

Coordinates arrive in world units as float64 (UTM drawings live near
E=500 000 — architectural principle #3). ``pack`` subtracts the scene origin
(the drawing's center) *in float64* and only then casts to float32, so the
precision loss lands in coordinates that are small by construction. The
viewport adds the origin back when building its matrix.

Vertices are interleaved ``[x, y, r, g, b, a]`` (6 x float32). Draw ranges
per (layer, color) bucket are kept so layer visibility and highlighting can
skip ranges without re-uploading the buffer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

VERTEX_FLOATS = 6  # x, y, r, g, b, a


def parse_color(color: str) -> tuple[float, float, float, float]:
    """``#rrggbb`` or ``#rrggbbaa`` (ezdxf backend format) -> RGBA floats."""
    h = color.lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    a = int(h[6:8], 16) / 255.0 if len(h) >= 8 else 1.0
    return r, g, b, a


@dataclass
class Bucket:
    """Primitives of one (layer, color) group, world float64 coordinates."""

    layer: str
    color: str
    lines: list[float] = field(default_factory=list)      # x,y per endpoint
    triangles: list[float] = field(default_factory=list)  # x,y per corner
    points: list[float] = field(default_factory=list)     # x,y per point


@dataclass
class DrawRange:
    """A contiguous vertex run inside a packed array."""

    layer: str
    first: int  # vertex index (not float index)
    count: int


@dataclass
class Batch:
    """One primitive type packed: interleaved float32 array + its ranges."""

    data: np.ndarray  # shape (n * VERTEX_FLOATS,), float32
    ranges: list[DrawRange]

    @property
    def vertex_count(self) -> int:
        return len(self.data) // VERTEX_FLOATS


@dataclass
class Scene:
    """Everything the viewport needs to draw one document."""

    origin: tuple[float, float]                    # float64 world center
    extents: tuple[float, float, float, float]     # world min_x, min_y, max_x, max_y
    lines: Batch
    triangles: Batch
    points: Batch

    @property
    def is_empty(self) -> bool:
        return (
            self.lines.vertex_count == 0
            and self.triangles.vertex_count == 0
            and self.points.vertex_count == 0
        )


def _pack_primitive(
    buckets: list[Bucket], attr: str, origin: tuple[float, float]
) -> Batch:
    ox, oy = origin
    chunks: list[np.ndarray] = []
    ranges: list[DrawRange] = []
    first = 0
    for bucket in buckets:
        coords = getattr(bucket, attr)
        if not coords:
            continue
        xy = np.asarray(coords, dtype=np.float64).reshape(-1, 2)
        n = len(xy)
        verts = np.empty((n, VERTEX_FLOATS), dtype=np.float32)
        verts[:, 0] = xy[:, 0] - ox  # float64 subtraction, then float32 store
        verts[:, 1] = xy[:, 1] - oy
        verts[:, 2:6] = parse_color(bucket.color)
        chunks.append(verts.reshape(-1))
        ranges.append(DrawRange(bucket.layer, first, n))
        first += n
    data = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)
    return Batch(data, ranges)


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


def pack(buckets: dict[tuple[str, str], Bucket]) -> Scene:
    """Pack backend buckets into a Scene, origin at the drawing's center."""
    # Stable order: by layer then color, so ranges group per layer for the
    # future visibility toggle.
    ordered = [buckets[k] for k in sorted(buckets)]
    extents = _world_extents(ordered)
    origin = ((extents[0] + extents[2]) / 2.0, (extents[1] + extents[3]) / 2.0)
    return Scene(
        origin=origin,
        extents=extents,
        lines=_pack_primitive(ordered, "lines", origin),
        triangles=_pack_primitive(ordered, "triangles", origin),
        points=_pack_primitive(ordered, "points", origin),
    )
