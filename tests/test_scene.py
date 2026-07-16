# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Headless tests of the regen pipeline: ezdxf doc -> frontend -> packed scene."""
from __future__ import annotations

import math

import ezdxf
import numpy as np
import pytest

from core.document import Document, DocumentError
from render.backend import build_scene
from render.batches import VERTEX_FLOATS, parse_color


def make_document() -> Document:
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    # UTM-scale coordinates on purpose: the precision path is the point.
    msp.add_line((500_000.0, 8_500_000.0), (500_100.0, 8_500_050.0))
    msp.add_circle((500_050.0, 8_500_050.0), 25.0)
    msp.add_text("PLANO", height=5.0, dxfattribs={"insert": (500_010.0, 8_500_080.0)})
    hatch = msp.add_hatch(color=1)
    hatch.paths.add_polyline_path(
        [(500_000.0, 8_500_000.0), (500_010.0, 8_500_000.0),
         (500_010.0, 8_500_010.0), (500_000.0, 8_500_010.0)],
        is_closed=True,
    )
    msp.add_point((500_020.0, 8_500_020.0))
    return Document(doc)


def test_scene_collects_all_primitive_kinds():
    scene = build_scene(make_document())
    assert scene.lines.vertex_count > 0        # line + flattened circle
    assert scene.triangles.vertex_count > 0    # hatch fill + text glyphs
    assert scene.points.vertex_count == 1
    assert not scene.is_empty


def test_scene_origin_recenters_utm_coordinates():
    scene = build_scene(make_document())
    ox, oy = scene.origin
    assert abs(ox - 500_050.0) < 100.0
    assert abs(oy - 8_500_040.0) < 100.0
    # Stored vertices are small numbers: float32 keeps full drawing precision.
    verts = scene.lines.data.reshape(-1, VERTEX_FLOATS)
    assert np.abs(verts[:, :2]).max() < 1000.0


def test_scene_extents_match_drawing():
    scene = build_scene(make_document())
    min_x, min_y, max_x, max_y = scene.extents
    assert min_x == pytest.approx(500_000.0, abs=1.0)
    assert max_x == pytest.approx(500_100.0, abs=1.0)
    assert min_y == pytest.approx(8_500_000.0, abs=1.0)


def test_circle_flattening_is_accurate():
    scene = build_scene(make_document())
    ox, oy = scene.origin
    verts = scene.lines.data.reshape(-1, VERTEX_FLOATS)[:, :2].astype(np.float64)
    # Vertices on the circle sit 25 units from its center.
    cx, cy = 500_050.0 - ox, 8_500_050.0 - oy
    radii = np.hypot(verts[:, 0] - cx, verts[:, 1] - cy)
    on_circle = np.abs(radii - 25.0) < 0.05
    assert on_circle.sum() >= 64  # the circle produced a dense polyline


def test_thick_lineweights_become_quads():
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    msp.add_line((0.0, 0.0), (100.0, 0.0), dxfattribs={"lineweight": 50})   # 0.50 mm
    msp.add_line((0.0, 0.0), (0.0, 100.0), dxfattribs={"lineweight": 13})   # 0.13 mm
    scene = build_scene(Document(doc))

    from render.batches import THICK_FLOATS

    # The 0.50 mm line becomes one quad (6 vertices); the thin one stays GL_LINES.
    assert scene.thick.vertex_count == 6
    assert scene.lines.vertex_count == 2
    assert scene.thick.ranges[0].lineweight == pytest.approx(0.5)
    verts = scene.thick.data.reshape(-1, THICK_FLOATS)
    normals = verts[:, 2:4]
    assert np.allclose(np.hypot(normals[:, 0], normals[:, 1]), 1.0, atol=1e-5)
    # The horizontal segment's expansion direction is vertical.
    assert np.allclose(np.abs(normals[:, 1]), 1.0, atol=1e-5)


def test_draw_ranges_partition_the_buffer():
    scene = build_scene(make_document())
    for batch in (scene.lines, scene.thick, scene.triangles, scene.points):
        cursor = 0
        for rng in batch.ranges:
            assert rng.first == cursor
            assert rng.count > 0
            cursor += rng.count
        assert cursor == batch.vertex_count


def test_malformed_entity_is_skipped_not_fatal():
    # Real-world case: LibreDWG emitted a HATCH spline edge with an
    # inconsistent knot count; one bad entity must never blank the drawing.
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0.0, 0.0), (10.0, 0.0))
    hatch = msp.add_hatch(color=2)
    edge_path = hatch.paths.add_edge_path()
    edge_path.add_spline(
        control_points=[(0, 0), (5, 5), (10, 0)],
        knot_values=[0.0] * 32,  # wrong: 3 control points + degree 3 need 7
        degree=3,
    )
    scene = build_scene(Document(doc))
    assert scene.lines.vertex_count >= 2      # the LINE still renders
    assert len(scene.skipped) == 1
    assert scene.skipped[0].startswith("HATCH")


def test_parse_color_rgb_and_rgba():
    assert parse_color("#ff0000") == (1.0, 0.0, 0.0, 1.0)
    r, g, b, a = parse_color("#00ff0080")
    assert (r, g, b) == (0.0, 1.0, 0.0)
    assert math.isclose(a, 128 / 255)


def test_document_load_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.dxf"
    bad.write_text("this is not a dxf")
    with pytest.raises(DocumentError):
        Document.load(bad)


def test_document_roundtrip_load(tmp_path):
    path = tmp_path / "plan.dxf"
    make_document().doc.saveas(path)
    document = Document.load(path)
    assert document.name == "plan.dxf"
    scene = build_scene(document)
    assert not scene.is_empty
