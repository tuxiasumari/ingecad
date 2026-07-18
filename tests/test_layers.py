# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Phase 6 headless tests: layer state, operations, current-layer drawing."""
from __future__ import annotations

import ezdxf
import pytest

from core import actions, layers as L
from core.commands import History
from core.document import Document


def make_doc():
    # setup=True loads the standard linetypes, like Document.new() does
    doc = Document(ezdxf.new("R2018", setup=True))
    return doc, History(doc)


def test_layer_list_and_current():
    doc, _ = make_doc()
    doc.doc.layers.add("WALLS", color=1)
    doc.doc.layers.add("TEXT", color=2)
    names = [i.name for i in L.layer_list(doc)]
    assert names[0] == "0"                    # layer 0 always first
    assert {"0", "WALLS", "TEXT"} <= set(names)
    assert L.current_layer_name(doc) == "0"
    L.set_current_layer(doc, "WALLS")
    assert L.current_layer_name(doc) == "WALLS"
    assert next(i for i in L.layer_list(doc) if i.name == "WALLS").is_current


def test_new_delete_rename_undo():
    doc, h = make_doc()
    h.execute(L.NewLayerCommand("EJES", color=1))
    assert "EJES" in doc.doc.layers
    h.execute(L.RenameLayerCommand("EJES", "AXES"))
    assert "AXES" in doc.doc.layers and "EJES" not in doc.doc.layers
    h.undo()
    assert "EJES" in doc.doc.layers
    h.redo()
    assert "AXES" in doc.doc.layers
    h.execute(L.DeleteLayerCommand("AXES"))
    assert "AXES" not in doc.doc.layers
    h.undo()
    assert "AXES" in doc.doc.layers            # restored with its color
    assert doc.doc.layers.get("AXES").dxf.color == 1


def test_layer_property_toggles_undo():
    doc, h = make_doc()
    doc.doc.layers.add("P", color=3)

    for prop, value, check in (
        ("on", False, lambda: not doc.doc.layers.get("P").is_on()),
        ("frozen", True, lambda: doc.doc.layers.get("P").is_frozen()),
        ("locked", True, lambda: doc.doc.layers.get("P").is_locked()),
        ("color", 5, lambda: abs(doc.doc.layers.get("P").dxf.color) == 5),
    ):
        h.execute(L.LayerPropertyCommand("P", prop, value))
        assert check()
    # undo the color, then everything unwinds cleanly
    h.undo()
    assert abs(doc.doc.layers.get("P").dxf.color) == 3


def test_new_entities_use_current_layer():
    doc, h = make_doc()
    h.execute(L.NewLayerCommand("MUROS", color=4))
    L.set_current_layer(doc, "MUROS")
    h.execute(actions.add_line((0, 0), (10, 0)))
    line = doc.modelspace().query("LINE")[0]
    assert line.dxf.layer == "MUROS"
    # after undo/redo the entity keeps landing on the current layer
    h.undo()
    L.set_current_layer(doc, "0")
    h.redo()
    assert doc.modelspace().query("LINE")[0].dxf.layer == "0"


def test_layer_linetype_and_lineweight():
    doc, h = make_doc()
    doc.doc.layers.add("EJES", color=1)
    h.execute(L.LayerPropertyCommand("EJES", "linetype", "DASHDOT"))
    assert doc.doc.layers.get("EJES").dxf.linetype == "DASHDOT"
    h.execute(L.LayerPropertyCommand("EJES", "lineweight", 50))
    assert doc.doc.layers.get("EJES").dxf.lineweight == 50
    info = next(i for i in L.layer_list(doc) if i.name == "EJES")
    assert info.linetype == "DASHDOT" and info.lineweight == 50
    h.undo()
    assert doc.doc.layers.get("EJES").dxf.lineweight == -3   # back to Default
    h.undo()
    assert doc.doc.layers.get("EJES").dxf.linetype == "Continuous"


def test_lineweight_labels():
    assert L.lineweight_label(-3) == "Default"
    assert L.lineweight_label(-1) == "ByLayer"
    assert L.lineweight_label(25) == "0.25 mm"


def test_available_linetypes():
    doc, _ = make_doc()
    lts = L.available_linetypes(doc)
    assert lts[0] == "Continuous"
    assert "DASHED" in lts and "ByLayer" not in lts


def test_unique_layer_name():
    doc, _ = make_doc()
    doc.doc.layers.add("Layer1")
    assert L.unique_layer_name(doc, "Layer") == "Layer2"


# -- entity properties (SetPropertyCommand) ------------------------------------

def test_set_entity_property_undo():
    doc, h = make_doc()
    doc.doc.layers.add("MUROS", color=4)
    line = doc.modelspace().add_line((0, 0), (10, 0))
    circle = doc.modelspace().add_circle((0, 0), 5)

    h.execute(actions.SetPropertyCommand([line, circle], "layer", "MUROS"))
    assert line.dxf.layer == "MUROS" and circle.dxf.layer == "MUROS"
    h.execute(actions.SetPropertyCommand([line], "color", 1))
    assert line.dxf.color == 1
    h.execute(actions.SetPropertyCommand([line], "lineweight", 50))
    assert line.dxf.lineweight == 50

    h.undo()   # lineweight
    assert line.dxf.get("lineweight", None) in (None, -1)
    h.undo()   # color
    assert line.dxf.get("color", 256) in (256, 7)
    h.undo()   # layer back to 0 for both
    assert line.dxf.layer == "0" and circle.dxf.layer == "0"
