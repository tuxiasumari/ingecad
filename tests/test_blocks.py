# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Phase 6 blocks + hatch: BLOCK, INSERT, EXPLODE, HATCH (headless)."""
from __future__ import annotations

import ezdxf
import pytest

from core.commands import History
from core.document import Document
from tools.base import ToolContext
from tools.blocks import BlockTool, ExplodeTool, HatchTool, InsertTool


class Services:
    def __init__(self, document):
        self.document = document

    def block_names(self):
        return sorted(b.name for b in self.document.doc.blocks
                      if not b.name.startswith("*"))


class Harness:
    def __init__(self, text_answer="MYBLOCK", choice_answer=None):
        self.document = Document(ezdxf.new("R2018", setup=True))
        self.history = History(self.document)
        self.finished = False
        self.services = Services(self.document)
        self.ctx = ToolContext(
            execute=self.history.execute,
            prompt=lambda *_a: None,
            echo=lambda *_a: None,
            finish=lambda: setattr(self, "finished", True),
            services=self.services,
            ask_text=lambda *_a: text_answer,
            ask_choice=lambda p, items, d="": choice_answer or (items[0] if items else None),
        )

    @property
    def msp(self):
        return self.document.modelspace()


def _two_lines(h):
    a = h.msp.add_line((0, 0), (10, 0))
    b = h.msp.add_line((10, 0), (10, 10))
    return [a, b]


def test_block_create_converts_selection():
    h = Harness(text_answer="COL")
    ents = _two_lines(h)
    tool = BlockTool(h.ctx)
    tool.start()
    tool.on_selection(ents)
    tool.on_point((0, 0))            # base point
    assert "COL" in h.document.doc.blocks
    assert len(h.msp.query("INSERT")) == 1
    assert len(h.msp.query("LINE")) == 0   # originals folded into the block
    # undo brings the lines back and drops the reference + definition
    h.history.undo()
    assert len(h.msp.query("LINE")) == 2
    assert len(h.msp.query("INSERT")) == 0
    assert "COL" not in h.document.doc.blocks


def test_block_cancel_on_empty_name():
    h = Harness(text_answer="")
    ents = _two_lines(h)
    tool = BlockTool(h.ctx)
    tool.start()
    tool.on_selection(ents)
    assert h.finished
    assert len(h.msp.query("LINE")) == 2   # nothing happened


def test_insert_places_reference_with_scale_rotation():
    h = Harness()
    blk = h.document.doc.blocks.new("WIN")
    blk.add_line((0, 0), (1, 0))
    tool = InsertTool(h.ctx)
    tool.start()                     # ask_choice -> "WIN"
    assert tool.on_option("S")
    assert tool.on_option("2")       # scale 2
    assert tool.on_option("R")
    assert tool.on_option("90")      # rotation 90
    tool.on_point((5, 5))
    ins = h.msp.query("INSERT")[0]
    assert ins.dxf.name == "WIN"
    assert ins.dxf.insert.x == pytest.approx(5)
    assert ins.dxf.xscale == pytest.approx(2)
    assert ins.dxf.rotation == pytest.approx(90)


def test_explode_reference_and_undo():
    h = Harness()
    blk = h.document.doc.blocks.new("W")
    blk.add_line((0, 0), (1, 0))
    blk.add_circle((0, 0), 1)
    h.msp.add_blockref("W", (5, 5))
    ins = h.msp.query("INSERT")[0]
    tool = ExplodeTool(h.ctx)
    tool.start()
    tool.on_selection([ins])
    assert len(h.msp.query("INSERT")) == 0
    assert len(h.msp.query("LINE")) == 1
    assert len(h.msp.query("CIRCLE")) == 1
    h.history.undo()
    assert len(h.msp.query("INSERT")) == 1
    assert len(h.msp.query("LINE")) == 0


def test_hatch_solid_on_closed_polyline():
    h = Harness()
    pl = h.msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    tool = HatchTool(h.ctx)
    tool.start()
    tool.on_selection([pl])
    tool.on_enter()                  # apply with default SOLID
    hatch = h.msp.query("HATCH")[0]
    assert hatch.dxf.solid_fill == 1
    assert len(hatch.paths) == 1


def test_hatch_pattern_scale_angle():
    h = Harness()
    c = h.msp.add_circle((0, 0), 5)
    tool = HatchTool(h.ctx)
    tool.start()
    tool.on_selection([c])
    assert tool.on_option("P")
    assert tool.on_option("ANSI31")
    assert tool.on_option("S")
    assert tool.on_option("2")
    assert tool.on_option("A")
    assert tool.on_option("45")
    tool.on_enter()
    hatch = h.msp.query("HATCH")[0]
    assert hatch.dxf.solid_fill == 0
    assert hatch.dxf.pattern_name.upper().endswith("ANSI31")
    assert hatch.dxf.pattern_scale == pytest.approx(2)
    assert hatch.dxf.pattern_angle == pytest.approx(45)


def test_hatch_rejects_open_boundary():
    h = Harness()
    pl = h.msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=False)
    tool = HatchTool(h.ctx)
    tool.start()
    tool.on_selection([pl])
    assert h.finished
    assert len(h.msp.query("HATCH")) == 0
