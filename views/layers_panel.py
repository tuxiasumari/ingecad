# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Layer manager panel (LA), classic AutoCAD-style table.

Columns: current, name, on, freeze, lock, color. Toolbar: new / delete /
set-current. Double-click a row makes it current; the name cell renames.
Every change routes through the layer Commands so undo/redo is exact.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core import layers as layer_ops
from core.i18n import tr

# ACI 1-9 to RGB (the classic AutoCAD standard colors), enough for the
# swatch + picker; higher indices fall back to a neutral grey chip.
ACI_RGB = {
    1: (255, 0, 0), 2: (255, 255, 0), 3: (0, 255, 0), 4: (0, 255, 255),
    5: (0, 0, 255), 6: (255, 0, 255), 7: (255, 255, 255), 8: (128, 128, 128),
    9: (192, 192, 192),
}


def aci_to_qcolor(index: int) -> QColor:
    r, g, b = ACI_RGB.get(index, (160, 160, 160))
    return QColor(r, g, b)


def nearest_aci(color: QColor) -> int:
    best, best_d = 7, 1e18
    for idx, (r, g, b) in ACI_RGB.items():
        d = (r - color.red()) ** 2 + (g - color.green()) ** 2 + (b - color.blue()) ** 2
        if d < best_d:
            best, best_d = idx, d
    return best


COLLAPSED_WIDTH = 22

_PANEL_STYLE = """
LayersPanel { background: #26262a; }
LayersPanel QTableWidget { font-size: 11px; background: #1e1e22;
    alternate-background-color: #232327; }
LayersPanel QHeaderView::section { background: #2d2d31; padding: 1px;
    border: none; font-size: 11px; }
LayersPanel QToolButton { border: none; color: #c8c8c8; padding: 2px 5px;
    font-size: 11px; }
LayersPanel QToolButton:hover { background: #3a3940; }
LayersPanel #sideLabel { color: #b0b0b0; font-weight: bold; }
"""


class LayersPanel(QWidget):
    changed = Signal()   # a layer edit landed: repaint the viewport

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("LayersPanel")
        self.setStyleSheet(_PANEL_STYLE)

        # Columns: Cur, Name, On, Freeze, Lock, Color, Linetype, Lineweight
        self.table = QTableWidget(0, 8, self)
        self.table.setHorizontalHeaderItem(0, self._header_item("✓", tr("Current")))
        self.table.setHorizontalHeaderItem(1, self._header_item(tr("Name"), tr("Name")))
        self.table.setHorizontalHeaderItem(2, self._header_item("◍", tr("On/Off")))
        self.table.setHorizontalHeaderItem(3, self._header_item("❄", tr("Freeze")))
        self.table.setHorizontalHeaderItem(4, self._header_item("🔒", tr("Lock")))
        self.table.setHorizontalHeaderItem(5, self._header_item("■", tr("Color")))
        self.table.setHorizontalHeaderItem(6, self._header_item(tr("Linetype"), tr("Linetype")))
        self.table.setHorizontalHeaderItem(7, self._header_item(tr("Lineweight"), tr("Lineweight")))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(20)  # compact rows

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)    # Name takes room
        for col in (0, 2, 3, 4, 5, 6, 7):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        for col, w in ((0, 22), (2, 26), (3, 26), (4, 26), (5, 40),
                       (6, 84), (7, 68)):
            self.table.setColumnWidth(col, w)

        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.cellChanged.connect(self._on_cell_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)

        # Compact icon-buttons instead of wide text buttons.
        new_btn = self._tool_button("＋", tr("New layer"), self._new_layer)
        del_btn = self._tool_button("🗑", tr("Delete layer"), self._delete_layer)
        cur_btn = self._tool_button("✓", tr("Set current"),
                                    self._set_current_selected)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(2, 2, 2, 0)
        buttons.setSpacing(1)
        for b in (new_btn, del_btn, cur_btn):
            buttons.addWidget(b)
        buttons.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.addLayout(buttons)
        layout.addWidget(self.table)

        self._loading = False
        self.refresh()

    def _tool_button(self, text, tip, slot) -> QToolButton:
        btn = QToolButton(self)
        btn.setText(text)
        btn.setToolTip(tip)
        btn.clicked.connect(slot)
        return btn

    @staticmethod
    def _header_item(text: str, tooltip: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setToolTip(tooltip)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    # -- data -----------------------------------------------------------------
    @property
    def document(self):
        return self.window.document

    def refresh(self) -> None:
        if self.document is None:
            self.table.setRowCount(0)
            return
        self._loading = True
        infos = layer_ops.layer_list(self.document)
        self.table.setRowCount(len(infos))
        for row, info in enumerate(infos):
            self._fill_row(row, info)
        self._loading = False

    def _fill_row(self, row: int, info: layer_ops.LayerInfo) -> None:
        cur = QTableWidgetItem("✓" if info.is_current else "")
        cur.setTextAlignment(Qt.AlignCenter)
        cur.setFlags(Qt.ItemIsEnabled)
        self.table.setItem(row, 0, cur)

        name = QTableWidgetItem(info.name)
        if info.name == "0":
            name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)  # 0 locked-name
        self.table.setItem(row, 1, name)

        for col, on in ((2, info.is_on), (3, not info.is_frozen), (4, not info.is_locked)):
            item = QTableWidgetItem(self._state_glyph(col, on))
            item.setTextAlignment(Qt.AlignCenter)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, col, item)

        swatch = QTableWidgetItem(str(info.color))
        swatch.setTextAlignment(Qt.AlignCenter)
        pm = QPixmap(12, 12)
        pm.fill(aci_to_qcolor(info.color))
        swatch.setIcon(QIcon(pm))
        swatch.setToolTip(tr("ACI color {n}", n=info.color))
        swatch.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(row, 5, swatch)

        lt = QTableWidgetItem(info.linetype)
        lt.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(row, 6, lt)

        lw = QTableWidgetItem(layer_ops.lineweight_label(info.lineweight))
        lw.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(row, 7, lw)

    @staticmethod
    def _state_glyph(col: int, active: bool) -> str:
        if col == 2:   # on
            return "💡" if active else "🌑"
        if col == 3:   # thawed (not frozen)
            return "☀" if active else "❄"
        return "🔓" if active else "🔒"   # unlocked

    def _row_layer(self, row: int) -> str:
        item = self.table.item(row, 1)
        return item.text() if item else ""

    # -- edits ----------------------------------------------------------------
    def _execute(self, command) -> None:
        self.window.history.execute(command)
        self.window.regen_in_memory()
        self.refresh()
        self.changed.emit()

    def _new_layer(self) -> None:
        if self.document is None:
            self.window.new_document()
        name = layer_ops.unique_layer_name(self.document)
        self._execute(layer_ops.NewLayerCommand(name))

    def _delete_layer(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        name = self._row_layer(row)
        if name == "0":
            self.window.command_line.echo(tr("Layer 0 cannot be deleted."))
            return
        if name == layer_ops.current_layer_name(self.document):
            self.window.command_line.echo(tr("Cannot delete the current layer."))
            return
        # Refuse if entities use it (AutoCAD refuses too).
        if any(e.dxf.layer == name for e in self.document.modelspace()):
            self.window.command_line.echo(tr("Layer {name} is in use.", name=name))
            return
        self._execute(layer_ops.DeleteLayerCommand(name))

    def _set_current_selected(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self._make_current(self._row_layer(row))

    def _make_current(self, name: str) -> None:
        layer_ops.set_current_layer(self.document, name)
        self.refresh()
        self.changed.emit()

    def _on_double_click(self, row: int, col: int) -> None:
        if col != 1:
            self._make_current(self._row_layer(row))

    def _on_cell_clicked(self, row: int, col: int) -> None:
        if self._loading or self.document is None:
            return
        name = self._row_layer(row)
        if col in (2, 3, 4):
            prop = {2: "on", 3: "frozen", 4: "locked"}[col]
            glyph = self.table.item(row, col).text()
            active = glyph in ("💡", "☀", "🔓")
            # toggling: on->off, thawed->frozen, unlocked->locked
            if prop == "on":
                self._execute(layer_ops.LayerPropertyCommand(name, "on", not active))
            elif prop == "frozen":
                self._execute(layer_ops.LayerPropertyCommand(name, "frozen", active))
            else:
                self._execute(layer_ops.LayerPropertyCommand(name, "locked", active))
        elif col == 5:
            info = next((i for i in layer_ops.layer_list(self.document)
                         if i.name == name), None)
            start = aci_to_qcolor(info.color) if info else QColor("white")
            chosen = QColorDialog.getColor(start, self, tr("Layer color"))
            if chosen.isValid():
                self._execute(layer_ops.LayerPropertyCommand(
                    name, "color", nearest_aci(chosen)))
        elif col == 6:
            self._pick_linetype(name)
        elif col == 7:
            self._pick_lineweight(name)

    def _pick_linetype(self, name: str) -> None:
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        for lt in layer_ops.available_linetypes(self.document):
            menu.addAction(lt, lambda lt=lt: self._execute(
                layer_ops.LayerPropertyCommand(name, "linetype", lt)))
        menu.exec(QCursor.pos())

    def _pick_lineweight(self, name: str) -> None:
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        for lw in layer_ops.LINEWEIGHTS:
            menu.addAction(layer_ops.lineweight_label(lw), lambda lw=lw: self._execute(
                layer_ops.LayerPropertyCommand(name, "lineweight", lw)))
        menu.exec(QCursor.pos())

    def _on_cell_changed(self, row: int, col: int) -> None:
        if self._loading or col != 1 or self.document is None:
            return
        new_name = self.table.item(row, 1).text().strip()
        infos = layer_ops.layer_list(self.document)
        if row >= len(infos):
            return
        old_name = infos[row].name if False else None
        # find the old name: the row order matches layer_list order
        names = [i.name for i in infos]
        if row < len(names):
            old_name = names[row]
        if not new_name or new_name == old_name or old_name is None:
            self.refresh()
            return
        if new_name in names:
            self.window.command_line.echo(
                tr("Layer {name} already exists.", name=new_name))
            self.refresh()
            return
        self._execute(layer_ops.RenameLayerCommand(old_name, new_name))
