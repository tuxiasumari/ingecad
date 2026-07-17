# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Smoke tests: the app constructs headless and the i18n engine resolves."""
from __future__ import annotations

import pytest

from core import i18n


def test_tr_falls_back_to_english_source():
    i18n.set_language("en")
    assert i18n.tr("File") == "File"
    assert i18n.tr("No such key 123") == "No such key 123"


def test_tr_spanish_catalog_loads():
    i18n.set_language("es")
    try:
        assert i18n.tr("File") == "Archivo"
        out = i18n.tr("Cannot open {name}: {error}", name="plano.dxf", error="x")
        assert out == "No se puede abrir plano.dxf: x"
    finally:
        i18n.set_language("en")


def test_main_window_constructs_offscreen(qapp):
    from views.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    assert win.viewport is win.centralWidget()
    # The view transform tracked the widget size.
    assert win.viewport.view.width > 100
    assert win.viewport.view.height > 100
    # Cursor readout wiring.
    win.viewport.cursorMoved.emit(12.3456, -7.8901)
    qapp.processEvents()
    assert "12.3456" in win._coords_label.text()
    win.close()


def test_language_switch_retranslates_menus(qapp):
    from views.main_window import MainWindow

    i18n.set_language("en")
    win = MainWindow()
    try:
        menus = [a.text() for a in win._menu_bar.actions()]
        assert "File" in menus and "Tools" in menus

        win._set_language("es")
        menus = [a.text() for a in win._menu_bar.actions()]
        assert "Archivo" in menus and "Herramientas" in menus
        assert win.windowTitle() == "IngeCAD — Sin nombre"
    finally:
        i18n.set_language("en")
        win.close()


def test_open_path_loads_async(qapp, tmp_path):
    import time
    from pathlib import Path

    import ezdxf

    from views.main_window import MainWindow

    doc = ezdxf.new("R2018")
    doc.modelspace().add_line((0, 0), (10, 10))
    path = tmp_path / "plan.dxf"
    doc.saveas(path)

    win = MainWindow()
    win.open_path(Path(path))
    deadline = time.monotonic() + 15.0
    while win.document is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert win.document is not None
    assert win.windowTitle() == "IngeCAD — plan.dxf"
    assert win.viewport._scene is not None and not win.viewport._scene.is_empty
    win.close()


def test_frontend_config_caps_hatch_density():
    from render.backend import HATCHING_TIMEOUT, frontend_config

    cfg = frontend_config(0.2)
    assert cfg.max_flattening_distance == 0.2
    assert cfg.min_hatch_line_distance == pytest.approx(0.2 / 64.0)
    assert cfg.hatching_timeout == HATCHING_TIMEOUT


def test_typed_alias_wins_over_inline_completion(qapp):
    # "l" + Enter must run LINE via the alias — the inline suggestion (a
    # trailing selection like "lAYER") must not hijack the submit.
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    from views.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    submitted = []
    win.command_line.submitted.connect(submitted.append)

    QTest.keyClicks(win.command_line.input, "l")
    qapp.processEvents()
    QTest.keyClick(win.command_line.input, Qt.Key_Return)
    assert submitted and submitted[-1].strip().lower() == "l"
    assert win.tools.active() and win.tools.tool.name == "LINE"
    win.tools.cancel()
    win.close()


def test_trim_full_flow_through_controller(qapp):
    # Regression: wants_selection was silently reset by the dataclass
    # __init__, so TRIM never entered its selection phase and Enter killed
    # the tool. This drives the REAL app flow: TR -> Enter (all edges) ->
    # click the span to remove.
    from views.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    win.dispatcher.submit("l")
    win.tools.on_click(0, 0)
    win.tools.on_click(100, 0)
    win.tools.on_text("")
    win.dispatcher.submit("l")
    win.tools.on_click(50, -20)
    win.tools.on_click(50, 20)
    win.tools.on_text("")

    win.dispatcher.submit("tr")
    assert win.tools._selecting_for is not None    # selection phase active
    win.tools.on_text("")                          # Enter: all edges
    assert win.tools.active()                      # tool survives
    win.tools.on_hover(75, 0.2, 2.0)
    win.tools.on_click(75, 0.2)
    spans = sorted(
        (round(l.dxf.start.x, 1), round(l.dxf.end.x, 1))
        for l in win.document.modelspace().query("LINE")
        if abs(l.dxf.start.y) < 0.1 and abs(l.dxf.end.y) < 0.1
    )
    assert spans == [(0.0, 50.0)]
    win.tools.cancel()
    win.close()


def test_trim_by_crossing_window(qapp):
    # TRIM targets can be captured with a window/crossing rectangle: two
    # parallel lines crossing a cutter, one crossing rect trims both spans.
    from views.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    for y in (0.0, 5.0):
        win.dispatcher.submit("l")
        win.tools.on_click(0, y)
        win.tools.on_click(100, y)
        win.tools.on_text("")
    win.dispatcher.submit("l")          # vertical cutter at x=50
    win.tools.on_click(50, -10)
    win.tools.on_click(50, 15)
    win.tools.on_text("")

    win.dispatcher.submit("tr")
    win.tools.on_text("")               # all edges
    win.tools.on_hover(75, 2.5, 2.0)
    # crossing rect (right-to-left) over the right spans of both lines
    win.tools.start_window(90.0, 7.0)
    win.tools.on_click(60.0, -2.0)      # release to the LEFT: crossing
    spans = sorted(
        (round(l.dxf.start.x, 1), round(l.dxf.end.x, 1), round(l.dxf.start.y, 1))
        for l in win.document.modelspace().query("LINE")
        if l.dxf.start.y == l.dxf.end.y
    )
    assert spans == [(0.0, 50.0, 0.0), (0.0, 50.0, 5.0)]
    win.tools.cancel()
    win.close()


def test_zoom_extents_frames_placeholder_bounds(qapp):
    from views.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    win.viewport.zoom_extents()
    v = win.viewport.view
    for wx, wy in [(-50.0, -50.0), (50.0, 50.0)]:
        sx, sy = v.world_to_screen(wx, wy)
        assert 0 <= sx <= v.width and 0 <= sy <= v.height
    win.close()
