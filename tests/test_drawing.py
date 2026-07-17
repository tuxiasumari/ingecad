# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Phase 4 headless tests: coordinates, osnaps, draw commands, tools."""
from __future__ import annotations

import math

import ezdxf
import pytest

from core import actions
from core.commands import History
from core.coords import CoordinateError, parse_point
from core.document import Document
from core.snap import SnapEngine
from tools.base import ToolContext
from tools.draw import ArcTool, CircleTool, LineTool, PlineTool, PolygonTool, RectangTool


# -- coordinate parsing --------------------------------------------------------

def test_parse_absolute_relative_polar_and_distance():
    assert parse_point("10,5").x == 10 and parse_point("10,5").y == 5
    p = parse_point("@10,5", last_point=(100.0, 200.0))
    assert (p.x, p.y) == (110.0, 205.0)
    p = parse_point("@10<45", last_point=(0.0, 0.0))
    assert p.x == pytest.approx(10 * math.cos(math.radians(45)))
    assert p.y == pytest.approx(10 * math.sin(math.radians(45)))
    p = parse_point("25", last_point=(0.0, 0.0), cursor_direction=0.0)
    assert (p.x, p.y) == (25.0, 0.0)
    # non-coordinates pass through as None (option keywords)
    assert parse_point("C") is None
    assert parse_point("2P") is None
    with pytest.raises(CoordinateError):
        parse_point("@1,1")             # relative without anchor
    with pytest.raises(CoordinateError):
        parse_point("25")               # distance without direction


# -- snaps ---------------------------------------------------------------------

def make_snap_doc():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0.0, 0.0), (100.0, 0.0))
    msp.add_line((50.0, -50.0), (50.0, 50.0))   # crosses the first at (50, 0)
    msp.add_line((0.0, 100.0), (20.0, 100.0))   # lone segment for MID
    msp.add_circle((200.0, 0.0), 25.0)
    msp.add_point((300.0, 300.0))
    return Document(doc)


def test_snap_priorities_and_kinds():
    engine = SnapEngine(make_snap_doc())
    hit = engine.find((99.0, 1.0), threshold=5.0)
    assert hit.kind == "END" and (hit.x, hit.y) == (100.0, 0.0)
    hit = engine.find((49.5, 0.5), threshold=5.0)
    assert hit.kind == "INT" and (hit.x, hit.y) == (50.0, 0.0)
    hit = engine.find((10.5, 101.0), threshold=5.0)
    assert hit.kind == "MID"
    assert hit.x == pytest.approx(10.0) and hit.y == pytest.approx(100.0)
    hit = engine.find((200.5, 0.5), threshold=5.0)
    assert hit.kind == "CEN" and (hit.x, hit.y) == (200.0, 0.0)
    hit = engine.find((299.0, 299.0), threshold=5.0)
    assert hit.kind == "NOD" and (hit.x, hit.y) == (300.0, 300.0)
    assert engine.find((500.0, 500.0), threshold=5.0) is None


def test_snap_nea_and_per():
    engine = SnapEngine(make_snap_doc())
    hit = engine.find((30.0, 2.0), threshold=3.0,
                      kinds=frozenset({"NEA"}))
    assert hit.kind == "NEA" and hit.y == pytest.approx(0.0)
    hit = engine.find((30.0, 2.0), threshold=5.0,
                      kinds=frozenset({"PER"}), from_point=(30.0, 40.0))
    assert hit.kind == "PER"
    assert hit.x == pytest.approx(30.0) and hit.y == pytest.approx(0.0)


def test_snap_invalidate_sees_new_entities():
    document = make_snap_doc()
    engine = SnapEngine(document)
    assert engine.find((400.0, 400.0), threshold=5.0) is None
    document.modelspace().add_point((400.0, 400.0))
    engine.invalidate()
    assert engine.find((400.0, 400.0), threshold=5.0).kind == "NOD"


# -- draw commands with undo ---------------------------------------------------

def test_draw_commands_do_and_undo():
    document = Document(ezdxf.new("R2018"))
    history = History(document)
    msp = document.modelspace()

    history.execute(actions.add_line((0, 0), (10, 0)))
    history.execute(actions.add_circle((5, 5), 2.5))
    history.execute(actions.add_rectangle((0, 0), (4, 3)))
    history.execute(actions.add_polygon((0, 0), (0, 10), 6))
    history.execute(actions.add_arc_3p((0, 0), (5, 5), (10, 0)))
    assert len(msp) == 5
    assert document.dirty

    for _ in range(5):
        history.undo()
    assert len(msp) == 0
    for _ in range(5):
        history.redo()
    assert len(msp) == 5


def test_arc_3p_passes_through_points():
    document = Document(ezdxf.new("R2018"))
    History(document).execute(actions.add_arc_3p((0, 0), (5, 5), (10, 0)))
    arc = document.modelspace().query("ARC")[0]
    assert arc.dxf.center.x == pytest.approx(5.0)
    assert arc.dxf.center.y == pytest.approx(0.0)
    assert arc.dxf.radius == pytest.approx(5.0)
    # the middle point (5,5) lies at 90 deg, inside the sweep
    a0, a1 = arc.dxf.start_angle % 360, arc.dxf.end_angle % 360
    assert ((90 - a0) % 360) <= ((a1 - a0) % 360)


# -- tools ---------------------------------------------------------------------

class Harness:
    def __init__(self):
        self.document = Document(ezdxf.new("R2018"))
        self.history = History(self.document)
        self.prompts: list[str] = []
        self.finished = False
        self.ctx = ToolContext(
            execute=self.history.execute,
            prompt=self.prompts.append,
            echo=self.prompts.append,
            finish=self._finish,
        )

    def _finish(self):
        self.finished = True

    @property
    def msp(self):
        return self.document.modelspace()


def test_line_tool_chains_and_closes():
    h = Harness()
    tool = LineTool(h.ctx)
    tool.start()
    tool.on_point((0, 0))
    tool.on_point((10, 0))
    tool.on_point((10, 10))
    assert len(h.msp.query("LINE")) == 2
    assert tool.on_option("C")            # closes back to (0,0)
    assert len(h.msp.query("LINE")) == 3
    assert h.finished


def test_circle_tool_modes():
    h = Harness()
    tool = CircleTool(h.ctx)
    tool.start()
    tool.on_point((10, 10))
    assert tool.on_option("7.5")          # typed radius
    c = h.msp.query("CIRCLE")[0]
    assert c.dxf.radius == pytest.approx(7.5)

    tool = CircleTool(h.ctx)
    tool.start()
    assert tool.on_option("2p".upper())
    tool.on_point((0, 0))
    tool.on_point((10, 0))
    c = h.msp.query("CIRCLE")[1]
    assert c.dxf.center.x == pytest.approx(5.0)
    assert c.dxf.radius == pytest.approx(5.0)

    tool = CircleTool(h.ctx)
    tool.start()
    assert tool.on_option("3P")
    for p in ((0, 0), (10, 0), (5, 5)):
        tool.on_point(p)
    assert len(h.msp.query("CIRCLE")) == 3


def test_pline_tool_enter_ends_and_close():
    h = Harness()
    tool = PlineTool(h.ctx)
    tool.start()
    for p in ((0, 0), (10, 0), (10, 10)):
        tool.on_point(p)
    tool.on_enter()
    pl = h.msp.query("LWPOLYLINE")[0]
    assert len(pl) == 3 and not pl.closed

    tool = PlineTool(h.ctx)
    tool.start()
    for p in ((0, 0), (20, 0), (20, 20)):
        tool.on_point(p)
    assert tool.on_option("C")
    assert h.msp.query("LWPOLYLINE")[1].closed


def test_rectang_and_polygon_tools():
    h = Harness()
    tool = RectangTool(h.ctx)
    tool.start()
    tool.on_point((0, 0))
    tool.on_point((30, 20))
    r = h.msp.query("LWPOLYLINE")[0]
    assert r.closed and len(r) == 4

    tool = PolygonTool(h.ctx)
    tool.start()
    assert tool.on_option("6")
    tool.on_point((0, 0))
    tool.on_point((10, 0))
    poly = h.msp.query("LWPOLYLINE")[1]
    assert poly.closed and len(poly) == 6

    tool = ArcTool(h.ctx)
    tool.start()
    for p in ((0, 0), (5, 5), (10, 0)):
        tool.on_point(p)
    assert len(h.msp.query("ARC")) == 1
