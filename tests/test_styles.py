# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Text/dimension style tables: create, edit, current, delete (undoable)."""
from __future__ import annotations

import ezdxf
import pytest

from core import styles as st
from core.commands import History
from core.document import Document


def _doc():
    doc = Document(ezdxf.new("R2018", setup=["linetypes"]))
    return doc, History(doc)


def test_new_doc_has_only_standard():
    doc, _ = _doc()
    assert st.text_style_names(doc) == ["Standard"]
    assert st.dim_style_names(doc) == ["Standard"]


def test_document_new_seeds_iso25():
    doc = Document.new()
    assert "ISO-25" in st.dim_style_names(doc)
    # $DIMSTYLE now points at a real table entry, not a phantom name
    assert st.current_dim_style(doc) in st.dim_style_names(doc)
    p = st.dim_style_props(doc, "ISO-25")
    assert p["dimtxt"] == pytest.approx(2.5)
    assert p["dimasz"] == pytest.approx(2.5)


def test_create_and_delete_text_style():
    doc, h = _doc()
    h.execute(st.NewTextStyleCommand("Titulos", {"font": "arial.ttf",
                                                 "width": 0.9}))
    assert "Titulos" in st.text_style_names(doc)
    h.undo()
    assert "Titulos" not in st.text_style_names(doc)
    h.redo()
    h.execute(st.DeleteTextStyleCommand("Titulos"))
    assert "Titulos" not in st.text_style_names(doc)
    h.undo()   # delete undone -> restored with its props
    assert "Titulos" in st.text_style_names(doc)
    assert st.text_style_props(doc, "Titulos")["width"] == pytest.approx(0.9)


def test_edit_text_style_props_and_undo():
    doc, h = _doc()
    h.execute(st.SetTextStylePropsCommand(
        "Standard", {"height": 3.0, "width": 0.8, "oblique": 15.0}))
    p = st.text_style_props(doc, "Standard")
    assert p["height"] == pytest.approx(3.0)
    assert p["width"] == pytest.approx(0.8)
    h.undo()
    assert st.text_style_props(doc, "Standard")["width"] == pytest.approx(1.0)


def test_current_text_style():
    doc, h = _doc()
    h.execute(st.NewTextStyleCommand("Titulos", {}))
    h.execute(st.SetCurrentTextStyleCommand("Titulos"))
    assert st.current_text_style(doc) == "Titulos"
    h.undo()
    assert st.current_text_style(doc) == "Standard"


def test_new_text_uses_current_style():
    from core import actions
    doc, h = _doc()
    h.execute(st.NewTextStyleCommand("Titulos", {"font": "arial.ttf"}))
    h.execute(st.SetCurrentTextStyleCommand("Titulos"))
    h.execute(actions.add_text((0, 0), "HELLO", 2.5))
    t = doc.modelspace().query("TEXT")[0]
    assert t.dxf.style == "Titulos"


def test_dim_style_create_edit_current():
    doc, h = _doc()
    initial = st.current_dim_style(doc)
    h.execute(st.NewDimStyleCommand("Acot100", dict(st.DIM_VARS)))
    assert "Acot100" in st.dim_style_names(doc)
    h.execute(st.SetDimStylePropsCommand("Acot100", {"dimtxt": 3.0,
                                                     "dimasz": 2.0}))
    p = st.dim_style_props(doc, "Acot100")
    assert p["dimtxt"] == pytest.approx(3.0)
    assert p["dimasz"] == pytest.approx(2.0)
    h.execute(st.SetCurrentDimStyleCommand("Acot100"))
    assert st.current_dim_style(doc) == "Acot100"
    h.undo()
    assert st.current_dim_style(doc) == initial
