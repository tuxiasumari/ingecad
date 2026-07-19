# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Incremental snap/pick caches: additive edits must not force a full
modelspace rebuild (the per-click lag while drawing on a large file)."""
import ezdxf

from core.document import Document
from core.select import GeometryIndex
from core.snap import SnapEngine


def _doc() -> Document:
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0))
    msp.add_circle((20, 20), 5)
    return Document(doc)


def test_snap_add_entities_appends_without_rebuild():
    document = _doc()
    engine = SnapEngine(document)
    assert engine.find((10, 0), 0.5).kind == "END"  # builds the cache

    line = document.modelspace().add_line((100, 100), (110, 100))
    engine.add_entities([line])
    assert not engine._dirty            # no invalidation happened
    hit = engine.find((110, 100), 0.5)
    assert hit is not None and hit.kind == "END"
    assert (hit.x, hit.y) == (110.0, 100.0)


def test_snap_add_entities_noop_while_dirty():
    document = _doc()
    engine = SnapEngine(document)
    line = document.modelspace().add_line((100, 100), (110, 100))
    engine.add_entities([line])         # dirty: must defer to the rebuild
    assert engine._dirty
    hit = engine.find((110, 100), 0.5)  # rebuild picks the line up anyway
    assert hit is not None and hit.kind == "END"


def test_index_add_entities_appends_without_rebuild():
    document = _doc()
    index = GeometryIndex(document)
    assert index.pick((5, 0), 0.5) is not None  # builds the cache

    circle = document.modelspace().add_circle((50, 50), 3)
    index.add_entities([circle])
    assert not index._dirty
    assert index.pick((53, 50), 0.5) == circle.dxf.handle
    # window/crossing see it too
    assert circle.dxf.handle in index.window((40, 40, 60, 60))


def test_index_remove_handles_drops_pick_geometry():
    document = _doc()
    index = GeometryIndex(document)
    msp = document.modelspace()
    line = msp.query("LINE").first
    circle = msp.query("CIRCLE").first
    assert index.pick((5, 0), 0.5) == line.dxf.handle

    index.remove_handles([line.dxf.handle])
    assert index.pick((5, 0), 0.5) is None          # line gone from the index
    assert index.pick((25, 20), 0.5) == circle.dxf.handle  # circle untouched


def test_index_remove_then_add_models_a_move():
    document = _doc()
    index = GeometryIndex(document)
    msp = document.modelspace()
    line = msp.query("LINE").first
    index.pick((5, 0), 0.5)                          # build

    # simulate MOVE: mutate the entity, patch the index surgically
    line.dxf.start = (0, 100, 0)
    line.dxf.end = (10, 100, 0)
    index.remove_handles([line.dxf.handle])
    index.add_entities([line])
    assert not index._dirty
    assert index.pick((5, 0), 0.5) is None
    assert index.pick((5, 100), 0.5) == line.dxf.handle


def test_index_remove_handles_noop_while_dirty():
    document = _doc()
    index = GeometryIndex(document)
    line = document.modelspace().query("LINE").first
    index.remove_handles([line.dxf.handle])          # dirty: deferred
    assert index._dirty
    assert index.pick((5, 0), 0.5) == line.dxf.handle  # rebuild still has it


def test_index_add_entities_mixed_types():
    document = _doc()
    index = GeometryIndex(document)
    index.pick((5, 0), 0.5)
    msp = document.modelspace()
    added = [
        msp.add_line((200, 0), (210, 0)),
        msp.add_lwpolyline([(0, 200), (10, 200), (10, 210)]),
        msp.add_arc((300, 300), 5, 0, 90),
        msp.add_point((400, 400)),
        msp.add_text("hola", dxfattribs={"insert": (500, 500)}),
    ]
    index.add_entities(added)
    assert index.pick((205, 0), 0.5) == added[0].dxf.handle
    assert index.pick((5, 200), 0.5) == added[1].dxf.handle
    assert index.pick((305, 300), 0.5) == added[2].dxf.handle
    assert index.pick((400, 400), 0.5) == added[3].dxf.handle
    assert index.pick((500.5, 500.5), 2.0) == added[4].dxf.handle  # bbox path
