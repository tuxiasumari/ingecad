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


# Entity types whose fills are text glyphs; they dominate label-heavy plans
# (a cadastre: 43 M of 49 M vertices) and the viewport hides them when they
# would be smaller than a few pixels.
_TEXT_TYPES = frozenset(("TEXT", "MTEXT", "ATTRIB", "ATTDEF"))


class VertexBackend(Backend):
    """Collects frontend primitives into per-(layer, color) buckets."""

    def __init__(self, flatten_distance: float) -> None:
        super().__init__()
        self.buckets: dict[tuple, Bucket] = {}
        self._flatten = flatten_distance
        self._kind = ""
        self._handle = None
        self.background: str | None = None

    def enter_entity(self, entity, properties) -> None:
        super().enter_entity(entity, properties)
        self._kind = "T" if entity.dxftype() in _TEXT_TYPES else ""
        self._handle = getattr(entity.dxf, "handle", None)

    def exit_entity(self, entity) -> None:
        super().exit_entity(entity)
        self._kind = ""
        self._handle = None

    def _bucket(self, properties: BackendProperties) -> Bucket:
        key = (properties.layer, properties.color, properties.lineweight,
               self._kind)
        bucket = self.buckets.get(key)
        if bucket is None:
            bucket = self.buckets[key] = Bucket(
                properties.layer, properties.color, properties.lineweight,
                self._kind,
            )
        return bucket

    # -- primitives -----------------------------------------------------------
    def draw_point(self, pos: Vec2, properties: BackendProperties) -> None:
        b = self._bucket(properties)
        b.points.extend((pos.x, pos.y))
        b.points_owner.append(self._handle)

    def draw_line(self, start: Vec2, end: Vec2, properties: BackendProperties) -> None:
        b = self._bucket(properties)
        b.lines.extend((start.x, start.y, end.x, end.y))
        b.lines_owner.append(self._handle)

    def draw_solid_lines(
        self, lines: Iterable[tuple[Vec2, Vec2]], properties: BackendProperties
    ) -> None:
        b = self._bucket(properties)
        for start, end in lines:
            b.lines.extend((start.x, start.y, end.x, end.y))
            b.lines_owner.append(self._handle)

    def draw_path(self, path: BkPath2d, properties: BackendProperties) -> None:
        b = self._bucket(properties)
        prev: Vec2 | None = None
        for v in path.flattening(self._flatten):
            if prev is not None:
                b.lines.extend((prev.x, prev.y, v.x, v.y))
                b.lines_owner.append(self._handle)
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
            if self._kind == "T":
                # Legibility metric for the viewport's tiny-text culling.
                ys = [v.y for v in rings[0]]
                bucket = self._bucket(properties)
                bucket.text_height_sum += max(ys) - min(ys)
                bucket.text_count += 1
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
        bucket = self._bucket(properties)
        for a, b, c in triangles:
            bucket.triangles.extend((a.x, a.y, b.x, b.y, c.x, c.y))
            bucket.triangles_owner.append(self._handle)

    def draw_image(self, image_data, properties: BackendProperties) -> None:
        pass  # raster underlays: out of F1 scope

    # -- lifecycle --------------------------------------------------------------
    def configure(self, config: Configuration) -> None:
        pass

    def set_background(self, color: str) -> None:
        # Captured for paperspace layouts (white paper, like AutoCAD's
        # layout tabs); modelspace keeps the viewport's own dark canvas.
        self.background = color

    def clear(self) -> None:
        self.buckets.clear()

    def finalize(self) -> None:
        pass


def _ring_extent(ring: list[Vec2]) -> float:
    xs = [v.x for v in ring]
    ys = [v.y for v in ring]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def pick_layout(document: Document):
    """The layout worth showing: modelspace, or the fullest paper layout.

    ArchiCAD-published sheets (and some AutoCAD workflows) leave modelspace
    genuinely empty and compose everything in a paperspace layout (VIEWPORT +
    INSERT). AutoCAD opens those showing the layout; a blank canvas here
    would read as a converter bug. Returns (layout, name) — name is None for
    plain modelspace.
    """
    msp = document.modelspace()
    if len(msp) > 0:
        return msp, None
    best = None
    for layout in document.doc.layouts:
        if layout.name == "Model":
            continue
        if len(layout) > 0 and (best is None or len(layout) > len(best)):
            best = layout
    if best is not None:
        return best, best.name
    return msp, None


def _layout_extents(layout):
    try:
        return bbox.extents(layout, fast=True)
    except Exception:
        # One malformed entity (e.g. a HATCH spline edge with bad knots)
        # aborts the whole-layout pass; retry entity by entity and keep
        # whatever measures cleanly.
        from ezdxf.math import BoundingBox

        total = BoundingBox()
        for entity in layout:
            try:
                one = bbox.extents([entity], fast=True)
            except Exception:
                continue
            if one.has_data:
                total.extend([one.extmin, one.extmax])
        return total


def _flatten_distance(layout) -> float:
    extents = _layout_extents(layout)
    if not extents.has_data:
        return 0.01
    dx = extents.extmax.x - extents.extmin.x
    dy = extents.extmax.y - extents.extmin.y
    diagonal = (dx * dx + dy * dy) ** 0.5
    return max(diagonal * FLATTEN_REL, MIN_FLATTEN)


class TolerantRenderContext(RenderContext):
    """Property resolution that survives malformed entities.

    resolve_all runs before draw_entity, outside the frontend's per-entity
    guard: a HATCH with pattern_scale 0 (seen after a LibreDWG roundtrip)
    raises ZeroDivisionError there and would blank the whole drawing. Fall
    back to plain defaults for that entity and keep drawing.
    """

    def resolve_all(self, entity):
        try:
            return super().resolve_all(entity)
        except Exception as exc:
            handle = getattr(entity.dxf, "handle", None) or "?"
            logger.warning(
                "default properties for %s(#%s): %s",
                entity.dxftype(), handle, exc,
            )
            from ezdxf.addons.drawing.properties import Properties

            return Properties()


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


def build_scene_for_entities(document: Document, entities, flatten: float) -> Scene:
    """Pack just ``entities`` (freshly drawn ones) into a small overlay scene.

    Drawing must feel instant on any file size: instead of a full regen per
    added entity, the viewport draws this overlay on top of the base scene
    and merges on the next real regen. ``flatten`` comes from the base scene
    build so curve quality matches.
    """
    backend = VertexBackend(flatten)
    context = TolerantRenderContext(document.doc)
    frontend = TolerantFrontend(context, backend, frontend_config(flatten))
    frontend.draw_entities(entities)
    return pack(backend.buckets)


def build_scene(document: Document) -> Scene:
    """Run the ezdxf frontend over the drawing and pack the result ("regen").

    Renders modelspace, or — when modelspace is empty — the fullest
    paperspace layout (ArchiCAD-published sheets); Scene.layout_name
    records the fallback so the UI can say so.
    """
    layout, layout_name = pick_layout(document)
    flatten = _flatten_distance(layout)
    backend = VertexBackend(flatten)
    context = TolerantRenderContext(document.doc)
    frontend = TolerantFrontend(context, backend, frontend_config(flatten))
    frontend.draw_layout(layout)
    scene = pack(backend.buckets)
    scene.skipped = list(frontend.skipped)
    scene.layout_name = layout_name
    scene.flatten = flatten
    if layout_name is not None:
        # Paper-white background like AutoCAD's layout tabs; the sheet's
        # colors were resolved by ezdxf against this background already.
        from render.batches import parse_color

        scene.background = (
            parse_color(backend.background) if backend.background
            else (1.0, 1.0, 1.0, 1.0)
        )
    return scene
