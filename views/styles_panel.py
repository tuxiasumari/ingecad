# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Styles panel — Text / Dimension / Hatch, with BricsCAD-style previews.

One category at a time (segmented tabs) keeps the list uncluttered even with
many styles. Each row carries a thumbnail of how the style looks, and a larger
preview sits above the property editor. Every change goes through the style
Commands for exact undo.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core import styles as style_ops
from core.i18n import tr
from views import style_previews as prev

COMMON_FONTS = ["txt.shx", "simplex.shx", "romans.shx", "isocp.shx",
                "arial.ttf", "arialbd.ttf", "times.ttf", "LiberationSans.ttf"]

_ICON = QSize(150, 34)

_STYLE = """
StylesPanel { background: #26262a; }
StylesPanel QListWidget { background: #1e1e22; color: #d0d0d0; border: 0;
    font-size: 11px; }
StylesPanel QListWidget::item { padding: 2px; }
StylesPanel QListWidget::item:selected { background: #35424f; }
StylesPanel QLabel { color: #c8c8c8; font-size: 11px; }
StylesPanel #preview { background: #f4f4f4; border: 1px solid #3a3940; }
StylesPanel QToolButton { border: none; color: #c8c8c8; padding: 3px 6px;
    font-size: 12px; }
StylesPanel QToolButton:hover, StylesPanel QToolButton:checked {
    background: #35424f; border-radius: 3px; }
StylesPanel QLineEdit, StylesPanel QComboBox, StylesPanel QDoubleSpinBox,
StylesPanel QSpinBox { background: #1e1e22; color: #e0e0e0;
    border: 1px solid #3a3940; padding: 0 3px; font-size: 11px; }
StylesPanel #head { color: #8ab4d8; font-weight: bold; padding: 2px; }
"""

_TEXT, _DIM, _HATCH = "TEXT", "DIM", "HATCH"


class StylesPanel(QWidget):
    changed = Signal()

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("StylesPanel")
        self.setStyleSheet(_STYLE)
        self._loading = False
        self._cat = _TEXT

        # -- category tabs + actions ------------------------------------------
        tabs = QHBoxLayout()
        tabs.setContentsMargins(2, 3, 2, 0)
        tabs.setSpacing(2)
        self._cat_btns = {}
        for cat, label in ((_TEXT, tr("Text")), (_DIM, tr("Dimension")),
                           (_HATCH, tr("Hatch"))):
            b = QToolButton(self)
            b.setText(label)
            b.setCheckable(True)
            b.setChecked(cat == self._cat)
            b.clicked.connect(lambda _=False, c=cat: self._switch(c))
            self._cat_btns[cat] = b
            tabs.addWidget(b)
        tabs.addStretch()
        for glyph, tip, slot in (("＋", tr("New"), self._new),
                                 ("🗑", tr("Delete"), self._delete),
                                 ("✓", tr("Set current"), self._set_current)):
            a = QToolButton(self)
            a.setText(glyph)
            a.setToolTip(tip)
            a.clicked.connect(slot)
            tabs.addWidget(a)

        self.list = QListWidget(self)
        self.list.setIconSize(_ICON)
        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.itemDoubleClicked.connect(lambda *_: self._set_current())

        self.preview = QLabel(self)
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedHeight(64)
        self.preview.setVisible(False)

        self._editor_host = QWidget(self)
        self._editor_layout = QVBoxLayout(self._editor_host)
        self._editor_layout.setContentsMargins(4, 2, 4, 4)
        self._editor_layout.setSpacing(3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addLayout(tabs)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.preview)
        layout.addWidget(self._editor_host)

        self.refresh()

    @property
    def _document(self):
        return self.window.document

    # -- category + list ------------------------------------------------------
    def _switch(self, cat: str) -> None:
        self._cat = cat
        for c, b in self._cat_btns.items():
            b.setChecked(c == cat)
        self.refresh()

    def refresh(self) -> None:
        self._loading = True
        self.list.clear()
        self._clear_editor()
        self.preview.setVisible(False)
        doc = self._document
        if doc is not None:
            if self._cat == _TEXT:
                self._fill_text()
            elif self._cat == _DIM:
                self._fill_dim()
            else:
                self._fill_hatch()
        self._loading = False

    def _fill_text(self) -> None:
        cur = style_ops.current_text_style(self._document)
        for name in style_ops.text_style_names(self._document):
            props = style_ops.text_style_props(self._document, name)
            icon = QIcon(prev.text_style_pixmap(props, _ICON.width(), _ICON.height()))
            self._add_item(name, icon, name == cur)

    def _fill_dim(self) -> None:
        cur = style_ops.current_dim_style(self._document)
        for name in style_ops.dim_style_names(self._document):
            props = style_ops.dim_style_props(self._document, name)
            icon = QIcon(prev.dim_style_pixmap(props, _ICON.width(), _ICON.height()))
            self._add_item(name, icon, name == cur)

    def _fill_hatch(self) -> None:
        from tools.blocks import HatchTool
        from views.hatch_dialog import _pattern_pixmap
        pat = HatchTool._last.get("pattern", "SOLID")
        item = QListWidgetItem(QIcon(_pattern_pixmap(pat)),
                               tr("Current: {p}", p=pat))
        item.setData(Qt.UserRole, pat)
        self.list.addItem(item)
        self._hatch_editor()

    def _add_item(self, name: str, icon: QIcon, current: bool) -> None:
        label = f"{name}   ({tr('current')})" if current else name
        item = QListWidgetItem(icon, label)
        item.setData(Qt.UserRole, name)
        if current:
            f = item.font()
            f.setBold(True)
            item.setFont(f)
        self.list.addItem(item)

    def _selected(self):
        items = self.list.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    # -- selection -> preview + editor ----------------------------------------
    def _on_select(self) -> None:
        if self._loading:
            return
        self._clear_editor()
        name = self._selected()
        if name is None or self._cat == _HATCH:
            return
        if self._cat == _TEXT:
            self._text_editor(name)
        else:
            self._dim_editor(name)
        self._update_preview(name)

    def _update_preview(self, name: str) -> None:
        if self._cat == _TEXT:
            props = style_ops.text_style_props(self._document, name)
            pm = prev.text_style_pixmap(props, 240, 56)
        elif self._cat == _DIM:
            props = style_ops.dim_style_props(self._document, name)
            pm = prev.dim_style_pixmap(props, 240, 60)
        else:
            return
        self.preview.setPixmap(pm)
        self.preview.setVisible(True)

    def _refresh_row_icon(self, name: str) -> None:
        """Redraw the selected row's thumbnail after an edit (no full rebuild)."""
        item = self.list.currentItem()
        if item is None:
            return
        if self._cat == _TEXT:
            props = style_ops.text_style_props(self._document, name)
            item.setIcon(QIcon(prev.text_style_pixmap(
                props, _ICON.width(), _ICON.height())))
        elif self._cat == _DIM:
            props = style_ops.dim_style_props(self._document, name)
            item.setIcon(QIcon(prev.dim_style_pixmap(
                props, _ICON.width(), _ICON.height())))

    def _clear_editor(self) -> None:
        while self._editor_layout.count():
            w = self._editor_layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()

    def _header(self, text: str) -> None:
        lbl = QLabel(text, self._editor_host)
        lbl.setObjectName("head")
        self._editor_layout.addWidget(lbl)

    def _num(self, value, lo, hi, decimals) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setDecimals(decimals)
        sb.setValue(float(value))
        sb.setKeyboardTracking(False)
        return sb

    # -- text editor ----------------------------------------------------------
    def _text_editor(self, name: str) -> None:
        props = style_ops.text_style_props(self._document, name)
        self._header(tr("Text style: {n}", n=name))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(3)
        font = QComboBox()
        font.setEditable(True)
        font.addItems(COMMON_FONTS)
        if props["font"] and props["font"] not in COMMON_FONTS:
            font.insertItem(0, props["font"])
        font.setCurrentText(props["font"])
        font.currentTextChanged.connect(
            lambda v: self._apply_text(name, {"font": v}))
        height = self._num(props["height"], 0.0, 1e6, 2)
        height.valueChanged.connect(lambda v: self._apply_text(name, {"height": v}))
        width = self._num(props["width"], 0.01, 100, 3)
        width.valueChanged.connect(lambda v: self._apply_text(name, {"width": v}))
        oblique = self._num(props["oblique"], -85, 85, 1)
        oblique.valueChanged.connect(lambda v: self._apply_text(name, {"oblique": v}))
        form.addRow(tr("Font"), font)
        form.addRow(tr("Height"), height)
        form.addRow(tr("Width factor"), width)
        form.addRow(tr("Oblique"), oblique)
        self._editor_layout.addLayout(form)

    # -- dim editor -----------------------------------------------------------
    def _dim_editor(self, name: str) -> None:
        props = style_ops.dim_style_props(self._document, name)
        self._header(tr("Dimension style: {n}", n=name))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(3)
        for var, label, dec in (("dimtxt", tr("Text height"), 2),
                                ("dimasz", tr("Arrow size"), 2),
                                ("dimscale", tr("Overall scale"), 3),
                                ("dimexe", tr("Ext. beyond"), 3),
                                ("dimexo", tr("Ext. offset"), 3)):
            sb = self._num(float(props[var]), 0.0, 1e6, dec)
            sb.valueChanged.connect(lambda v, k=var: self._apply_dim(name, {k: v}))
            form.addRow(label, sb)
        dec_sb = QSpinBox()
        dec_sb.setRange(0, 8)
        dec_sb.setValue(int(props["dimdec"]))
        dec_sb.valueChanged.connect(lambda v: self._apply_dim(name, {"dimdec": v}))
        form.addRow(tr("Decimals"), dec_sb)
        txsty = QComboBox()
        names = style_ops.text_style_names(self._document)
        txsty.addItems(names)
        cur = props["dimtxsty"] if props["dimtxsty"] in names else (
            names[0] if names else "Standard")
        txsty.setCurrentText(cur)
        txsty.currentTextChanged.connect(
            lambda v: self._apply_dim(name, {"dimtxsty": v}))
        form.addRow(tr("Text style"), txsty)
        self._editor_layout.addLayout(form)

    # -- hatch ----------------------------------------------------------------
    def _hatch_editor(self) -> None:
        note = QLabel(
            tr("AutoCAD has no named hatch styles; set the default pattern."),
            self._editor_host)
        note.setWordWrap(True)
        self._editor_layout.addWidget(note)
        btn = QPushButton(tr("Choose pattern..."), self._editor_host)
        btn.clicked.connect(self._choose_hatch)
        self._editor_layout.addWidget(btn)

    def _choose_hatch(self) -> None:
        from tools.blocks import HatchTool
        from views.hatch_dialog import HatchDialog
        dlg = HatchDialog(self.window, HatchTool._last)
        if dlg.exec():
            HatchTool._last = dlg.settings()
            self.refresh()

    # -- edits ----------------------------------------------------------------
    def _apply_text(self, name: str, props: dict) -> None:
        if self._loading:
            return
        self.window.history.execute(style_ops.SetTextStylePropsCommand(name, props))
        self.window.regen_in_memory()
        self._refresh_row_icon(name)
        self._update_preview(name)
        self.changed.emit()

    def _apply_dim(self, name: str, props: dict) -> None:
        if self._loading:
            return
        self.window.history.execute(style_ops.SetDimStylePropsCommand(name, props))
        self._refresh_row_icon(name)
        self._update_preview(name)
        self.changed.emit()

    def _new(self) -> None:
        if self._cat == _HATCH:
            self._choose_hatch()
            return
        if self._cat == _DIM:
            existing = style_ops.dim_style_names(self._document)
            name = style_ops.unique_name(existing, tr("Dim"))
            name, ok = QInputDialog.getText(self, tr("New dimension style"),
                                            tr("Name:"), text=name)
            if ok and name.strip():
                self.window.history.execute(style_ops.NewDimStyleCommand(
                    name.strip(), dict(style_ops.ISO25_DIM)))
        else:
            existing = style_ops.text_style_names(self._document)
            name = style_ops.unique_name(existing, tr("Style"))
            name, ok = QInputDialog.getText(self, tr("New text style"),
                                            tr("Name:"), text=name)
            if ok and name.strip():
                self.window.history.execute(style_ops.NewTextStyleCommand(
                    name.strip(), {"font": "txt.shx", "width": 1.0}))
        self.refresh()
        self.changed.emit()

    def _delete(self) -> None:
        name = self._selected()
        if name is None or name == "Standard" or self._cat == _HATCH:
            return
        if self._cat == _TEXT:
            if name == style_ops.current_text_style(self._document):
                return
            self.window.history.execute(style_ops.DeleteTextStyleCommand(name))
        else:
            if name == style_ops.current_dim_style(self._document):
                return
            self.window.history.execute(style_ops.DeleteDimStyleCommand(name))
        self.refresh()
        self.changed.emit()

    def _set_current(self) -> None:
        name = self._selected()
        if name is None or self._cat == _HATCH:
            return
        if self._cat == _TEXT:
            self.window.history.execute(style_ops.SetCurrentTextStyleCommand(name))
        else:
            self.window.history.execute(style_ops.SetCurrentDimStyleCommand(name))
        self.refresh()
        self.changed.emit()
