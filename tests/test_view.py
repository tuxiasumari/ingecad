# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Pure-math tests for the 2D view transform (no Qt)."""
from __future__ import annotations

from render.view import ViewTransform2D


def make_view() -> ViewTransform2D:
    return ViewTransform2D(cx=0.0, cy=0.0, scale=10.0, width=800, height=600)


def test_world_screen_roundtrip():
    v = make_view()
    for wx, wy in [(0.0, 0.0), (12.5, -7.25), (-3.0, 4.0)]:
        sx, sy = v.world_to_screen(wx, wy)
        bx, by = v.screen_to_world(sx, sy)
        assert abs(bx - wx) < 1e-9 and abs(by - wy) < 1e-9


def test_y_axis_points_up_on_screen():
    v = make_view()
    _, sy0 = v.world_to_screen(0.0, 0.0)
    _, sy1 = v.world_to_screen(0.0, 10.0)
    assert sy1 < sy0  # larger world Y is higher on screen (smaller sy)


def test_pan_moves_content_with_the_drag():
    v = make_view()
    sx0, sy0 = v.world_to_screen(5.0, 5.0)
    v.pan_pixels(30.0, -12.0)
    sx1, sy1 = v.world_to_screen(5.0, 5.0)
    assert abs((sx1 - sx0) - 30.0) < 1e-9
    assert abs((sy1 - sy0) - (-12.0)) < 1e-9


def test_zoom_at_keeps_anchor_fixed():
    v = make_view()
    anchor = (123.0, 456.0)  # screen px
    wx, wy = v.screen_to_world(*anchor)
    v.zoom_at(anchor[0], anchor[1], 1.7)
    sx, sy = v.world_to_screen(wx, wy)
    assert abs(sx - anchor[0]) < 1e-9 and abs(sy - anchor[1]) < 1e-9
    assert abs(v.scale - 17.0) < 1e-9


def test_zoom_extents_fits_rect_with_margin():
    v = make_view()
    v.zoom_extents(100.0, 200.0, 300.0, 260.0)  # 200 x 60 world rect
    assert abs(v.cx - 200.0) < 1e-9 and abs(v.cy - 230.0) < 1e-9
    # Both corners project inside the viewport.
    for wx, wy in [(100.0, 200.0), (300.0, 260.0)]:
        sx, sy = v.world_to_screen(wx, wy)
        assert 0 <= sx <= v.width and 0 <= sy <= v.height


def test_zoom_scale_is_clamped():
    v = make_view()
    v.zoom_at(400, 300, 1e-30)
    assert v.scale >= ViewTransform2D.MIN_SCALE
    v.zoom_at(400, 300, 1e40)
    assert v.scale <= ViewTransform2D.MAX_SCALE


def test_utm_scale_coordinates_survive_roundtrip():
    # Real-world UTM magnitudes (~500 km east, ~8.5M north in Peru) must not
    # lose precision in the view math — everything here is float64.
    v = ViewTransform2D(cx=512345.6789, cy=8_512_345.6789, scale=50.0,
                        width=1920, height=1080)
    wx, wy = 512345.1234, 8_512_346.9876
    sx, sy = v.world_to_screen(wx, wy)
    bx, by = v.screen_to_world(sx, sy)
    assert abs(bx - wx) < 1e-6 and abs(by - wy) < 1e-6  # sub-micrometer
