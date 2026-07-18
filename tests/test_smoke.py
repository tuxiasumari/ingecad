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


def test_toolbar_buttons_start_commands(qapp):
    # Draw and Modify toolbars fire the same commands as typing them.
    from views.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    draw_names = [a.toolTip() for a in win._draw_toolbar.actions()]
    assert any("LINE" in t for t in draw_names)
    modify_names = [a.toolTip() for a in win._modify_toolbar.actions()]
    assert any("TRIM" in t for t in modify_names)

    win._invoke_command("LINE")
    assert win.tools.active() and win.tools.tool.name == "LINE"
    # a second toolbar command cancels the first and starts the new one
    win._invoke_command("CIRCLE")
    assert win.tools.active() and win.tools.tool.name == "CIRCLE"
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


def test_edges_stay_highlighted_during_trim(qapp):
    # Preselect circles -> TR: AutoCAD keeps the cutting edges highlighted
    # to guide the picks; the highlight clears when the command ends.
    from views.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    win.dispatcher.submit("c")
    win.tools.on_click(0, 0)
    win.tools.on_text("10")
    win.dispatcher.submit("c")
    win.tools.on_click(12, 0)
    win.tools.on_text("10")

    win.tools.on_hover(0, 10, 2.0)
    win.tools.on_click(0, 10)       # pick circle 1 (idle selection)
    win.tools.on_click(12, -10)     # pick circle 2
    assert len(win.tools.selection) == 2
    win.dispatcher.submit("tr")
    assert win.tools.active()
    assert len(win.tools.selection) == 2      # edges stay lit during TRIM
    win.tools.on_click(10.5, 0)               # trim c1's right arc
    assert len(win.tools.selection) == 2      # survivor arc replaces c1
    # the trimmed edge keeps cutting: trim c2's left arc against the arc
    win.tools.on_click(1.5, 0)
    assert len(win.document.modelspace().query("ARC")) == 2
    assert len(win.document.modelspace().query("CIRCLE")) == 0
    win.tools.on_text("")                     # Enter ends
    assert not win.tools.selection            # highlight off after command
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


def test_extend_by_rect_both_directions(qapp):
    # EXTEND targets by rectangle: quick-mode semantics, BOTH drag
    # directions act as crossing (whatever the rect touches extends).
    from views.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    for y in (0.0, 5.0):
        win.dispatcher.submit("l")
        win.tools.on_click(0, y)
        win.tools.on_click(40, y)
        win.tools.on_text("")
    win.dispatcher.submit("l")
    win.tools.on_click(100, -10)
    win.tools.on_click(100, 15)
    win.tools.on_text("")

    win.dispatcher.submit("ex")
    win.tools.on_text("")
    win.tools.on_hover(38, 2.5, 2.0)
    win.tools.start_window(30.0, -2.0)
    win.tools.on_click(45.0, 7.0)       # LEFT-to-RIGHT drag: still crossing
    ends = sorted(round(l.dxf.end.x, 1)
                  for l in win.document.modelspace().query("LINE")
                  if l.dxf.start.y == l.dxf.end.y)
    assert ends == [100.0, 100.0]
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


def test_polyline_midgrip_moves_segment(qapp):
    # AutoCAD/BricsCAD: the midpoint (triangle) grip MOVES the whole segment,
    # it never inserts a vertex — vertex count stays constant no matter how
    # many frames the live follow runs.
    from views.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    win.dispatcher.submit("pl")
    for p in ((0, 0), (10, 0), (10, 10)):
        win.tools.on_click(*p)
    win.tools.on_text("")           # Enter ends PLINE
    win.regen_in_memory()

    pl = win.document.modelspace().query("LWPOLYLINE")[0]
    win.tools.selection = {pl.dxf.handle}
    grips = win.tools.grip_points()
    # the midpoint of segment 0 (between (0,0) and (10,0)) at (5,0)
    mid = next(g for g in grips if g[2] == "mid" and abs(g[0] - 5) < 0.1)
    win.tools.begin_grip_drag(mid)
    for tgt in ((5, -4), (5, -6), (6, -8)):         # live follow, many frames
        win.tools.update_grip_drag(*tgt)
    win.tools.finish_grip_drag(6, -8)
    pts = list(win.document.modelspace().query("LWPOLYLINE")[0].get_points("xy"))
    assert len(pts) == 3                            # NO vertex inserted
    # segment 0's endpoints both moved down; the third vertex stayed
    assert round(pts[0][1], 1) == -8.0 and round(pts[1][1], 1) == -8.0
    assert (round(pts[2][0]), round(pts[2][1])) == (10, 10)
    win.close()
