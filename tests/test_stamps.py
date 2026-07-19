# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Big MOVE/COPY/PASTE commits reuse the ghost tessellation as stamps —
no overlay re-tessellation — plus the ezdxf hatch-transform patch and the
translated-row cache updates that back them."""
import math

import ezdxf
import pytest
from ezdxf.math import Matrix44

from core.document import Document
from core.select import GeometryIndex
from core.snap import SnapEngine


# -- ezdxf patch: pattern must survive transforms ------------------------------

def _hatch_doc():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    h = msp.add_hatch()
    h.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)])
    h.set_pattern_fill("ANSI31", scale=1.0)
    return Document(doc), h


def test_hatch_translation_keeps_pattern():
    _doc, h = _hatch_doc()
    angle0 = h.pattern.lines[0].angle
    base0 = h.pattern.lines[0].base_point
    h.transform(Matrix44.translate(500, 500, 0))
    h.transform(Matrix44.translate(-100, 30, 0))
    assert h.pattern.lines[0].angle == pytest.approx(angle0)
    assert h.pattern.lines[0].base_point.x == pytest.approx(base0.x)


def test_hatch_rotation_applies_delta_once():
    _doc, h = _hatch_doc()
    angle0 = h.pattern.lines[0].angle
    h.transform(Matrix44.z_rotate(math.radians(30)))
    assert h.pattern.lines[0].angle == pytest.approx(angle0 + 30.0)
    assert h.dxf.pattern_angle == pytest.approx(30.0)


# -- translated-row cache updates ----------------------------------------------

def _doc_with_line_circle():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0))
    msp.add_circle((20, 20), 5)
    return Document(doc)


def test_index_translate_handles_shifts_rows():
    document = _doc_with_line_circle()
    index = GeometryIndex(document)
    msp = document.modelspace()
    line = msp.query("LINE").first
    index.pick((5, 0), 0.5)
    index.translate_handles([line.dxf.handle], 100, 50)
    assert index.pick((5, 0), 0.5) is None
    assert index.pick((105, 50), 0.5) == line.dxf.handle


def test_snap_translate_handles_shifts_rows():
    document = _doc_with_line_circle()
    engine = SnapEngine(document)
    circle = document.modelspace().query("CIRCLE").first
    engine.find((0, 0), 1.0)
    engine.translate_handles([circle.dxf.handle], 80, 80)
    hit = engine.find((100.1, 100.1), 0.5)
    assert hit is not None and hit.kind == "CEN"
    assert engine.find((20.1, 20.1), 0.5) is None   # old center gone


def test_index_add_translated_copies_source_rows():
    document = _doc_with_line_circle()
    index = GeometryIndex(document)
    msp = document.modelspace()
    line = msp.query("LINE").first
    index.pick((5, 0), 0.5)

    copy = msp.add_line((100, 100), (110, 100))   # pretend paste result
    missing = index.add_translated(
        [(line.dxf.handle, copy.dxf.handle)], 100, 100)
    assert missing == set()
    assert index.pick((105, 100), 0.5) == copy.dxf.handle
    assert index.pick((5, 0), 0.5) == line.dxf.handle   # source intact


def test_index_add_translated_reports_missing_sources():
    document = _doc_with_line_circle()
    index = GeometryIndex(document)
    index.pick((5, 0), 0.5)
    missing = index.add_translated([("DEAD", "BEEF")], 1, 1)
    assert missing == {"DEAD"}


# -- stamp lifecycle through the real controller --------------------------------

def _wait_ghost(qapp, tools, timeout_s: float = 5.0):
    import time
    t0 = time.monotonic()
    while tools._ghost_cache is None and time.monotonic() - t0 < timeout_s:
        qapp.processEvents()
    assert tools._ghost_cache is not None, "ghost tessellation never landed"


def _window_with_grid(qapp, n=30):
    from views.main_window import MainWindow

    win = MainWindow()
    win.show()
    win.new_document()
    t = win.tools
    msp = win.document.modelspace()
    for i in range(n):
        msp.add_line((i, 0), (i, 5))
    t.snap_engine.find((0, 0), 1.0)
    t.index.pick((0, 0), 1.0)
    return win, t


def test_big_paste_is_stamped_not_retessellated(qapp):
    win, t = _window_with_grid(qapp)
    t.selection = set(t.index.crossing((-1, -1, 40, 6)))
    assert len(t.selection) >= 25
    assert t.copy_selection()
    t.paste()
    t.on_hover(50, 50, 1.0)                  # hover kicks the ghost worker
    _wait_ghost(qapp, t)
    t.on_hover(100, 100, 1.0)
    t.on_click(100, 100)                     # drop
    assert len(t._stamp_records) == 1
    rec = next(iter(t._stamp_records.values()))
    assert rec["scene"] is not None          # reused the ghost tessellation
    assert win.viewport._stamps and win.viewport._stamps[0]["offsets"]
    assert not t._pending_render             # copies did NOT hit the overlay
    # caches see the copies at the pasted location
    assert t.index.pick((100.0, 102.0), 0.5) is not None
    assert not t.index._dirty and not t.snap_engine._dirty

    # undo removes the stamp and the cache rows; redo restores both
    win.history.undo()
    cmd = win.history._redo[-1]
    t.after_history_change(cmd)
    assert not win.viewport._stamps
    assert t.index.pick((100.0, 102.0), 0.5) is None
    win.history.redo()
    t.after_history_change(cmd)
    assert win.viewport._stamps
    assert t.index.pick((100.0, 102.0), 0.5) is not None
    win.close()


def test_big_move_is_stamped_and_undo_unhides(qapp):
    win, t = _window_with_grid(qapp)
    t.selection = set(t.index.crossing((-1, -1, 40, 6)))
    handles = sorted(t.selection)
    t.start_tool("MOVE")
    t.on_click(0, 0)                          # base point (ghost starts)
    t.on_hover(50, 0, 1.0)                    # hover kicks the ghost worker
    _wait_ghost(qapp, t)
    t.on_click(200, 0)                        # second point: commit
    assert len(t._stamp_records) == 1
    rec = next(iter(t._stamp_records.values()))
    assert rec["hidden"]                      # base copies hidden
    assert t.index.pick((201.0, 2.0), 0.5) in handles   # rows translated
    assert t.index.pick((1.0, 2.0), 0.5) is None

    win.history.undo()
    cmd = win.history._redo[-1]
    t.after_history_change(cmd)
    assert not win.viewport._stamps
    assert t.index.pick((1.0, 2.0), 0.5) in handles     # rows back
    win.close()


def test_editing_stamped_entity_retires_the_stamp(qapp):
    from core import actions

    win, t = _window_with_grid(qapp)
    t.selection = set(t.index.crossing((-1, -1, 40, 6)))
    assert t.copy_selection()
    t.paste()
    t.on_hover(50, 50, 1.0)                  # hover kicks the ghost worker
    _wait_ghost(qapp, t)
    t.on_hover(100, 100, 1.0)
    t.on_click(100, 100)
    assert len(t._stamp_records) == 1
    # erase one pasted copy: the stamp must retire, survivors ride the overlay
    victim = t.index.pick((100.0, 102.0), 0.5)
    assert victim is not None
    t._execute(actions.EraseCommand([t.index.entity(victim)]))
    assert not t._stamp_records
    assert not win.viewport._stamps
    assert t._pending_render                  # surviving copies re-shown
    win.close()
