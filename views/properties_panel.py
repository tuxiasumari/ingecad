# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Entity properties panel — the AutoCAD Properties palette, compact.

Shows the common properties of the current selection (layer, color,
linetype, lineweight) and lets you change them. Color/linetype/lineweight
carry the ByLayer sentinel so a selection can revert to its layer's look.
Geometry read-outs appear when exactly one entity is selected.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core import actions, layers as layer_ops
from core.i18n import tr

BYLAYER_COLOR = 256      # AutoCAD sentinel
BYLAYER_LW = -1

_STYLE = """
PropertiesPanel { background: #26262a; }
PropertiesPanel QLabel { color: #c8c8c8; font-size: 11px; }
PropertiesPanel QComboBox { font-size: 11px; background: #1e1e22;
    color: #d8d8d8; border: 1px solid #3a3940; padding: 1px 3px; }
PropertiesPanel #header { font-weight: bold; padding: 3px; }
PropertiesPanel #section { color: #8ab4d8; font-weight: bold;
    padding: 4px 3px 1px; }
"""


class PropertiesPanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("PropertiesPanel")
        self.setStyleSheet(_STYLE)
        self._loading = False

        self._header = QLabel(tr("No selection"), self)
        self._header.setObjectName("header")

        self.layer_cb = QComboBox(self)
        self.color_cb = QComboBox(self)
        self.linetype_cb = QComboBox(self)
        self.lineweight_cb = QComboBox(self)
        self.layer_cb.activated.connect(self._apply_layer)
        self.color_cb.activated.connect(self._apply_color)
        self.linetype_cb.activated.connect(self._apply_linetype)
        self.lineweight_cb.activated.connect(self._apply_lineweight)

        form = QFormLayout()
        form.setContentsMargins(4, 2, 4, 2)
        form.setSpacing(3)
        form.addRow(tr("Layer"), self.layer_cb)
        form.addRow(tr("Color"), self.color_cb)
        form.addRow(tr("Linetype"), self.linetype_cb)
        form.addRow(tr("Lineweight"), self.lineweight_cb)

        self._geometry = QLabel("", self)
        self._geometry.setWordWrap(True)
        self._geometry.setObjectName("section")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._header)
        layout.addLayout(form)
        layout.addWidget(self._geometry)
        layout.addStretch()

        self.refresh()

    # -- data -----------------------------------------------------------------
    @property
    def _document(self):
        return self.window.document

    def _selection(self) -> list:
        tools = getattr(self.window, "tools", None)
        if tools is None or self._document is None:
            return []
        return tools._selection_entities()

    def refresh(self) -> None:
        self._loading = True
        entities = self._selection()
        enabled = bool(entities) and self._document is not None
        for cb in (self.layer_cb, self.color_cb, self.linetype_cb,
                   self.lineweight_cb):
            cb.setEnabled(enabled)
            cb.clear()
        if not enabled:
            self._header.setText(tr("No selection"))
            self._geometry.setText("")
            self._loading = False
            return

        n = len(entities)
        kinds = {e.dxftype() for e in entities}
        self._header.setText(
            tr("{n} objects", n=n) if n > 1
            else tr("{kind}", kind=next(iter(kinds))))

        self._fill_layer(entities)
        self._fill_color(entities)
        self._fill_linetype(entities)
        self._fill_lineweight(entities)
        self._geometry.setText(self._geometry_text(entities) if n == 1 else "")
        self._loading = False

    def _common(self, entities, getter, default):
        vals = {getter(e) for e in entities}
        return vals.pop() if len(vals) == 1 else default

    def _fill_layer(self, entities) -> None:
        names = [i.name for i in layer_ops.layer_list(self._document)]
        self.layer_cb.addItems(names)
        common = self._common(entities, lambda e: e.dxf.layer, None)
        if common in names:
            self.layer_cb.setCurrentIndex(names.index(common))
        else:
            self.layer_cb.setCurrentIndex(-1)   # varies

    def _fill_color(self, entities) -> None:
        from views.layers_panel import ACI_RGB

        self.color_cb.addItem(tr("ByLayer"), BYLAYER_COLOR)
        for aci in sorted(ACI_RGB):
            self.color_cb.addItem(tr("Color {n}", n=aci), aci)
        common = self._common(entities, lambda e: e.dxf.get("color", 256), None)
        idx = self.color_cb.findData(common)
        self.color_cb.setCurrentIndex(idx)

    def _fill_linetype(self, entities) -> None:
        self.linetype_cb.addItem(tr("ByLayer"), "ByLayer")
        for lt in layer_ops.available_linetypes(self._document):
            self.linetype_cb.addItem(lt, lt)
        common = self._common(entities, lambda e: e.dxf.get("linetype", "ByLayer"), None)
        idx = self.linetype_cb.findData(common)
        self.linetype_cb.setCurrentIndex(idx)

    def _fill_lineweight(self, entities) -> None:
        self.lineweight_cb.addItem(tr("ByLayer"), BYLAYER_LW)
        for lw in layer_ops.LINEWEIGHTS:
            self.lineweight_cb.addItem(layer_ops.lineweight_label(lw), lw)
        common = self._common(entities, lambda e: e.dxf.get("lineweight", -1), None)
        idx = self.lineweight_cb.findData(common)
        self.lineweight_cb.setCurrentIndex(idx)

    def _geometry_text(self, entities) -> str:
        e = entities[0]
        t = e.dxftype()
        if t == "LINE":
            s, w = e.dxf.start, e.dxf.end
            import math
            length = math.hypot(w.x - s.x, w.y - s.y)
            return (tr("Start") + f": {s.x:.3f}, {s.y:.3f}\n"
                    + tr("End") + f": {w.x:.3f}, {w.y:.3f}\n"
                    + tr("Length") + f": {length:.3f}")
        if t == "CIRCLE":
            c = e.dxf.center
            return (tr("Center") + f": {c.x:.3f}, {c.y:.3f}\n"
                    + tr("Radius") + f": {e.dxf.radius:.3f}")
        if t == "ARC":
            c = e.dxf.center
            return (tr("Center") + f": {c.x:.3f}, {c.y:.3f}\n"
                    + tr("Radius") + f": {e.dxf.radius:.3f}")
        if t == "LWPOLYLINE":
            return tr("Vertices") + f": {len(e)}"
        return ""

    # -- edits ----------------------------------------------------------------
    def _execute(self, prop: str, value) -> None:
        entities = self._selection()
        if not entities:
            return
        self.window.history.execute(
            actions.SetPropertyCommand(entities, prop, value))
        self.window.regen_in_memory()
        self.refresh()

    def _apply_layer(self, index: int) -> None:
        if not self._loading:
            self._execute("layer", self.layer_cb.itemText(index))

    def _apply_color(self, index: int) -> None:
        if not self._loading:
            self._execute("color", self.color_cb.itemData(index))

    def _apply_linetype(self, index: int) -> None:
        if not self._loading:
            self._execute("linetype", self.linetype_cb.itemData(index))

    def _apply_lineweight(self, index: int) -> None:
        if not self._loading:
            self._execute("lineweight", self.lineweight_cb.itemData(index))
