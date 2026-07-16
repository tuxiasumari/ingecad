# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""GL vertex backend for ``ezdxf.addons.drawing`` — the "regen" engine.

The ezdxf frontend resolves the hard CAD semantics (block references, MTEXT
layout, linetype dashing, hatch patterns, dimension graphics, OCS) and hands
this backend nothing but resolved 2D primitives with final colors. We collect
them into (layer, color) buckets and pack them into GPU-ready arrays.

Curves are flattened at a fixed world-space tolerance derived from the
drawing size — a "regen", AutoCAD-style. Deep zoom-in past that tolerance
shows facets until a future re-regen at view scale (known trade-off, F1).
"""
from __future__ import annotations

from typing import Iterable

from ezdxf import bbox
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.backend import Backend, BkPath2d, BkPoints2d
from ezdxf.addons.drawing.config import Configuration
from ezdxf.addons.drawing.properties import BackendProperties
from ezdxf.math import Vec2
from ezdxf.math.triangulation import mapbox_earcut_2d

import logging

from core.document import Document
from render.batches import Bucket, Scene, pack

logger = logging.getLogger(__name__)

# Curve flattening: max sagitta as a fraction of the drawing diagonal.
# 1/20000 keeps a full-drawing circle visually smooth and stays sane on
# kilometre-scale UTM drawings.
FLATTEN_REL = 1.0 / 20000.0
MIN_FLATTEN = 1e-6

# Hatch density cap, AutoCAD MaxHatch style: pattern lines closer than this
# fraction of the flattening distance fall back to ezdxf's solid fill. Keep
# it generous — stipple patterns (AR-CONC, sand) on detail sheets are much
# finer than the sheet-wide flatten distance and must render as patterns
# (pavement-plan lesson: 1/4 turned them all into solid blobs). The timeout
# below, not this cap, is what contains pathological hatches.
HATCH_DENSITY_REL = 1.0 / 64.0
# Backstop for hatches that explode combinatorially: ezdxf aborts the pattern
# after this many seconds and falls back to a solid fill. This is what turned
# a frozen 287 s open (30 s x ~40 hatches) into ~3 s.
HATCHING_TIMEOUT = 5.0


class VertexBackend(Backend):
    """Collects frontend primitives into per-(layer, color) buckets."""

    def __init__(self, flatten_distance: float) -> None:
        super().__init__()
        self.buckets: dict[tuple[str, str], Bucket] = {}
        self._flatten = flatten_distance

    def _bucket(self, properties: BackendProperties) -> Bucket:
        key = (properties.layer, properties.color, properties.lineweight)
        bucket = self.buckets.get(key)
        if bucket is None:
            bucket = self.buckets[key] = Bucket(
                properties.layer, properties.color, properties.lineweight
            )
        return bucket

    # -- primitives -----------------------------------------------------------
    def draw_point(self, pos: Vec2, properties: BackendProperties) -> None:
        self._bucket(properties).points.extend((pos.x, pos.y))

    def draw_line(self, start: Vec2, end: Vec2, properties: BackendProperties) -> None:
        self._bucket(properties).lines.extend((start.x, start.y, end.x, end.y))

    def draw_solid_lines(
        self, lines: Iterable[tuple[Vec2, Vec2]], properties: BackendProperties
    ) -> None:
        out = self._bucket(properties).lines
        for start, end in lines:
            out.extend((start.x, start.y, end.x, end.y))

    def draw_path(self, path: BkPath2d, properties: BackendProperties) -> None:
        out = self._bucket(properties).lines
        prev: Vec2 | None = None
        for v in path.flattening(self._flatten):
            if prev is not None:
                out.extend((prev.x, prev.y, v.x, v.y))
            prev = v

    def draw_filled_polygon(
        self, points: BkPoints2d, properties: BackendProperties
    ) -> None:
        self._fill(points.vertices(), [], properties)

    def draw_filled_paths(
        self, paths: Iterable[BkPath2d], properties: BackendProperties
    ) -> None:
        # Each path may carry holes as sub-paths (glyphs like "O", hatch
        # islands). The largest ring is the exterior — the frontend guarantees
        # holes lie inside their path's exterior.
        for path in paths:
            rings = [list(sub.flattening(self._flatten)) for sub in path.sub_paths()]
            rings = [r for r in rings if len(r) >= 3]
            if not rings:
                continue
            rings.sort(key=_ring_extent, reverse=True)
            self._fill(rings[0], rings[1:], properties)

    def _fill(
        self,
        exterior: list[Vec2],
        holes: list[list[Vec2]],
        properties: BackendProperties,
    ) -> None:
        if len(exterior) < 3:
            return
        try:
            triangles = mapbox_earcut_2d(exterior, holes or None)
        except (ValueError, ZeroDivisionError):
            return  # degenerate ring: drop the fill, keep going
        out = self._bucket(properties).triangles
        for a, b, c in triangles:
            out.extend((a.x, a.y, b.x, b.y, c.x, c.y))

    def draw_image(self, image_data, properties: BackendProperties) -> None:
        pass  # raster underlays: out of F1 scope

    # -- lifecycle --------------------------------------------------------------
    def configure(self, config: Configuration) -> None:
        pass

    def set_background(self, color: str) -> None:
        pass  # viewport keeps its own model-space background

    def clear(self) -> None:
        self.buckets.clear()

    def finalize(self) -> None:
        pass


def _ring_extent(ring: list[Vec2]) -> float:
    xs = [v.x for v in ring]
    ys = [v.y for v in ring]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def _modelspace_extents(document: Document):
    msp = document.modelspace()
    try:
        return bbox.extents(msp, fast=True)
    except Exception:
        # One malformed entity (e.g. a HATCH spline edge with bad knots)
        # aborts the whole-layout pass; retry entity by entity and keep
        # whatever measures cleanly.
        from ezdxf.math import BoundingBox

        total = BoundingBox()
        for entity in msp:
            try:
                one = bbox.extents([entity], fast=True)
            except Exception:
                continue
            if one.has_data:
                total.extend([one.extmin, one.extmax])
        return total


def _flatten_distance(document: Document) -> float:
    extents = _modelspace_extents(document)
    if not extents.has_data:
        return 0.01
    dx = extents.extmax.x - extents.extmin.x
    dy = extents.extmax.y - extents.extmin.y
    diagonal = (dx * dx + dy * dy) ** 0.5
    return max(diagonal * FLATTEN_REL, MIN_FLATTEN)


class TolerantFrontend(Frontend):
    """Frontend that survives malformed entities.

    Real-world files (and satellite conversions) carry broken geometry —
    e.g. LibreDWG emitting HATCH spline edges with inconsistent knot counts.
    AutoCAD still opens those plans; one bad entity must never blank the
    whole drawing. Failures are skipped, logged, and counted for the UI.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.skipped: list[str] = []

    def draw_entity(self, entity, properties) -> None:
        try:
            super().draw_entity(entity, properties)
        except Exception as exc:
            handle = getattr(entity.dxf, "handle", None) or "?"
            note = f"{entity.dxftype()}(#{handle}): {exc}"
            self.skipped.append(note)
            logger.warning("skipped unrenderable entity %s", note)


def frontend_config(flatten: float) -> Configuration:
    return Configuration(
        max_flattening_distance=flatten,
        min_hatch_line_distance=flatten * HATCH_DENSITY_REL,
        hatching_timeout=HATCHING_TIMEOUT,
    )


def build_scene(document: Document) -> Scene:
    """Run the ezdxf frontend over modelspace and pack the result ("regen")."""
    flatten = _flatten_distance(document)
    backend = VertexBackend(flatten)
    context = RenderContext(document.doc)
    frontend = TolerantFrontend(context, backend, frontend_config(flatten))
    frontend.draw_layout(document.modelspace())
    scene = pack(backend.buckets)
    scene.skipped = list(frontend.skipped)
    return scene
