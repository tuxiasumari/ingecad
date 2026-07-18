# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Delete-selection and clipboard (copy/cut/paste) — headless via PasteTool."""
from __future__ import annotations

import ezdxf
import pytest

from core import actions
from core.commands import History
from core.document import Document
from tools.base import ToolContext
from tools.edit import PasteTool


class Harness:
    def __init__(self):
        self.document = Document.new()
        self.history = History(self.document)
        self.finished = False
        self._clip = None
        self.ctx = ToolContext(
            execute=self.history.execute,
            prompt=lambda *_a: None,
            echo=lambda *_a: None,
            finish=lambda: setattr(self, "finished", True),
            services=self,
        )

    @property
    def msp(self):
        return self.document.modelspace()

    def clipboard_data(self):
        return self._clip if self._clip else (None, None)


def test_paste_translates_by_base_to_target():
    h = Harness()
    a = h.msp.add_line((0, 0), (2, 0))
    b = h.msp.add_circle((1, 1), 1)
    # emulate a copy: store copies + base at the extents min (0,0)
    h._clip = ([a.copy(), b.copy()], (0, 0))
    tool = PasteTool(h.ctx)
    tool.start()
    tool.on_point((10, 5))         # insertion point
    lines = [e for e in h.msp.query("LINE")]
    circles = [e for e in h.msp.query("CIRCLE")]
    assert len(lines) == 2 and len(circles) == 2   # originals + pasted
    pasted_line = [ln for ln in lines if ln is not a][0]
    assert pasted_line.dxf.start.x == pytest.approx(10)
    assert pasted_line.dxf.start.y == pytest.approx(5)


def test_paste_undo_removes_copies():
    h = Harness()
    a = h.msp.add_line((0, 0), (2, 0))
    h._clip = ([a.copy()], (0, 0))
    tool = PasteTool(h.ctx)
    tool.start()
    tool.on_point((10, 0))
    assert len(h.msp.query("LINE")) == 2
    h.history.undo()
    assert len(h.msp.query("LINE")) == 1


def test_paste_reusable_twice():
    h = Harness()
    a = h.msp.add_line((0, 0), (1, 0))
    h._clip = ([a.copy()], (0, 0))
    for target in ((5, 0), (0, 5)):
        tool = PasteTool(h.ctx)
        tool.start()
        tool.on_point(target)
    assert len(h.msp.query("LINE")) == 3   # original + two pastes


def test_paste_empty_clipboard_finishes():
    h = Harness()
    tool = PasteTool(h.ctx)
    tool.start()
    assert h.finished
    assert len(h.msp.query("LINE")) == 0


def test_erase_command_removes_and_undo_restores():
    # Delete-selection routes through EraseCommand; verify it round-trips.
    h = Harness()
    a = h.msp.add_line((0, 0), (2, 0))
    h.history.execute(actions.EraseCommand([a]))
    assert len(h.msp.query("LINE")) == 0
    h.history.undo()
    assert len(h.msp.query("LINE")) == 1
