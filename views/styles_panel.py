# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Styles panel — Text Styles and Dimension Styles in the right sidebar.

A tree groups the drawing's text and dimension styles (current one marked);
the toolbar creates / deletes / sets-current, and the editor below tweaks the
selected style's properties. Hatch has no named-style table in AutoCAD, so its
row just opens the pattern picker and remembers the session default. Every
change goes through the style Commands for exact undo.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import styles as style_ops
from core.i18n import tr

COMMON_FONTS = ["txt.shx", "simplex.shx", "romans.shx", "isocp.shx",
                "arial.ttf", "arialbd.ttf", "times.ttf", "LiberationSans.ttf"]

_STYLE = """
StylesPanel { background: #26262a; }
StylesPanel QTreeWidget { background: #1e1e22; color: #c8c8c8; border: 0;
    font-size: 11px; }
StylesPanel QLabel { color: #c8c8c8; font-size: 11px; }
StylesPanel QToolButton { border: none; color: #c8c8c8; padding: 2px 6px;
    font-size: 13px; }
StylesPanel QToolButton:hover { background: #33333a; }
StylesPanel QLineEdit, StylesPanel QComboBox, StylesPanel QDoubleSpinBox,
StylesPanel QSpinBox { background: #1e1e22; color: #e0e0e0;
    border: 1px solid #3a3940; padding: 0 3px; font-size: 11px; }
StylesPanel #head { color: #8ab4d8; font-weight: bold; padding: 3px 2px; }
"""

_TEXT_GROUP = "TEXT"
_DIM_GROUP = "DIM"
_HATCH_GROUP = "HATCH"


class StylesPanel(QWidget):
    changed = Signal()

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("StylesPanel")
        self.setStyleSheet(_STYLE)
        self._loading = False

        bar = QHBoxLayout()
        bar.setContentsMargins(2, 2, 2, 0)
        bar.setSpacing(0)
        for glyph, tip, slot in (
            ("＋", tr("New style"), self._new),
            ("🗑", tr("Delete style"), self._delete),
            ("✓", tr("Set current"), self._set_current)):
            b = QToolButton(self)
            b.setText(glyph)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch()

        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(10)
        self.tree.itemSelectionChanged.connect(self._build_editor)
        self.tree.itemDoubleClicked.connect(lambda *_: self._set_current())

        self._editor_host = QWidget(self)
        self._editor_layout = QVBoxLayout(self._editor_host)
        self._editor_layout.setContentsMargins(4, 2, 4, 4)
        self._editor_layout.setSpacing(3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(bar)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self._editor_host)

        self.refresh()

    @property
    def _document(self):
        return self.window.document

    # -- tree -----------------------------------------------------------------
    def refresh(self) -> None:
        self._loading = True
        self.tree.clear()
        doc = self._document
        if doc is not None:
            cur_txt = style_ops.current_text_style(doc)
            txt = self._group(tr("Text Styles"), _TEXT_GROUP)
            for name in style_ops.text_style_names(doc):
                self._leaf(txt, name, _TEXT_GROUP, name == cur_txt)
            cur_dim = style_ops.current_dim_style(doc)
            dim = self._group(tr("Dimension Styles"), _DIM_GROUP)
            for name in style_ops.dim_style_names(doc):
                self._leaf(dim, name, _DIM_GROUP, name == cur_dim)
            hatch = self._group(tr("Hatch"), _HATCH_GROUP)
            pat = self._current_hatch_pattern()
            self._leaf(hatch, tr("Current pattern: {p}", p=pat),
                       _HATCH_GROUP, False)
        self.tree.expandAll()
        self._loading = False
        self._clear_editor()

    def _group(self, label: str, kind: str) -> QTreeWidgetItem:
        it = QTreeWidgetItem(self.tree, [label])
        it.setData(0, Qt.UserRole, (kind, None))
        it.setForeground(0, Qt.GlobalColor.gray)
        it.setFlags(Qt.ItemIsEnabled)
        return it

    def _leaf(self, parent, name: str, kind: str, current: bool) -> None:
        label = f"{name}  ({tr('current')})" if current else name
        it = QTreeWidgetItem(parent, [label])
        it.setData(0, Qt.UserRole, (kind, name if kind != _HATCH_GROUP else None))
        if current:
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)

    def _selected(self):
        items = self.tree.selectedItems()
        if not items:
            return None, None
        kind, name = items[0].data(0, Qt.UserRole)
        return kind, name

    # -- editor ---------------------------------------------------------------
    def _clear_editor(self) -> None:
        while self._editor_layout.count():
            item = self._editor_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build_editor(self) -> None:
        if self._loading:
            return
        self._clear_editor()
        kind, name = self._selected()
        if kind == _TEXT_GROUP and name is not None:
            self._text_editor(name)
        elif kind == _DIM_GROUP and name is not None:
            self._dim_editor(name)
        elif kind == _HATCH_GROUP:
            self._hatch_editor()

    def _header(self, text: str) -> None:
        lbl = QLabel(text, self._editor_host)
        lbl.setObjectName("head")
        self._editor_layout.addWidget(lbl)

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
        height.valueChanged.connect(
            lambda v: self._apply_text(name, {"height": v}))
        width = self._num(props["width"], 0.01, 100, 3)
        width.valueChanged.connect(
            lambda v: self._apply_text(name, {"width": v}))
        oblique = self._num(props["oblique"], -85, 85, 1)
        oblique.valueChanged.connect(
            lambda v: self._apply_text(name, {"oblique": v}))

        form.addRow(tr("Font"), font)
        form.addRow(tr("Height"), height)
        form.addRow(tr("Width factor"), width)
        form.addRow(tr("Oblique"), oblique)
        self._editor_layout.addLayout(form)

    def _dim_editor(self, name: str) -> None:
        props = style_ops.dim_style_props(self._document, name)
        self._header(tr("Dimension style: {n}", n=name))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(3)
        rows = [
            ("dimtxt", tr("Text height"), 2),
            ("dimasz", tr("Arrow size"), 2),
            ("dimscale", tr("Overall scale"), 3),
            ("dimexe", tr("Ext. beyond"), 3),
            ("dimexo", tr("Ext. offset"), 3),
        ]
        for var, label, dec in rows:
            sb = self._num(float(props[var]), 0.0, 1e6, dec)
            sb.valueChanged.connect(
                lambda v, k=var: self._apply_dim(name, {k: v}))
            form.addRow(label, sb)
        dec_sb = QSpinBox()
        dec_sb.setRange(0, 8)
        dec_sb.setValue(int(props["dimdec"]))
        dec_sb.valueChanged.connect(
            lambda v: self._apply_dim(name, {"dimdec": v}))
        form.addRow(tr("Decimals"), dec_sb)

        txsty = QComboBox()
        names = style_ops.text_style_names(self._document)
        txsty.addItems(names)
        cur = props["dimtxsty"] if props["dimtxsty"] in names else \
            (names[0] if names else "Standard")
        txsty.setCurrentText(cur)
        txsty.currentTextChanged.connect(
            lambda v: self._apply_dim(name, {"dimtxsty": v}))
        form.addRow(tr("Text style"), txsty)
        self._editor_layout.addLayout(form)

    def _hatch_editor(self) -> None:
        self._header(tr("Hatch default"))
        note = QLabel(
            tr("AutoCAD has no named hatch styles; set the default pattern."),
            self._editor_host)
        note.setWordWrap(True)
        self._editor_layout.addWidget(note)
        btn = QPushButton(tr("Choose pattern..."), self._editor_host)
        btn.clicked.connect(self._choose_hatch)
        self._editor_layout.addWidget(btn)

    def _num(self, value, lo, hi, decimals) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setDecimals(decimals)
        sb.setValue(float(value))
        sb.setKeyboardTracking(False)
        return sb

    # -- hatch ----------------------------------------------------------------
    def _current_hatch_pattern(self) -> str:
        from tools.blocks import HatchTool
        return HatchTool._last.get("pattern", "SOLID")

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
        self.window.history.execute(
            style_ops.SetTextStylePropsCommand(name, props))
        self.window.regen_in_memory()
        self.changed.emit()

    def _apply_dim(self, name: str, props: dict) -> None:
        if self._loading:
            return
        self.window.history.execute(
            style_ops.SetDimStylePropsCommand(name, props))
        self.changed.emit()

    def _new(self) -> None:
        kind, _ = self._selected()
        if kind == _DIM_GROUP:
            base, existing = tr("Dim"), style_ops.dim_style_names(self._document)
            name = style_ops.unique_name(existing, base)
            name, ok = QInputDialog.getText(self, tr("New dimension style"),
                                            tr("Name:"), text=name)
            if ok and name.strip():
                self.window.history.execute(
                    style_ops.NewDimStyleCommand(name.strip(),
                                                 dict(style_ops.DIM_VARS)))
        else:   # default to a text style
            base = tr("Style")
            existing = style_ops.text_style_names(self._document)
            name = style_ops.unique_name(existing, base)
            name, ok = QInputDialog.getText(self, tr("New text style"),
                                            tr("Name:"), text=name)
            if ok and name.strip():
                self.window.history.execute(style_ops.NewTextStyleCommand(
                    name.strip(), {"font": "txt.shx", "width": 1.0}))
        self.refresh()
        self.changed.emit()

    def _delete(self) -> None:
        kind, name = self._selected()
        if name is None or name == "Standard":
            return
        if kind == _TEXT_GROUP:
            if name == style_ops.current_text_style(self._document):
                return
            self.window.history.execute(style_ops.DeleteTextStyleCommand(name))
        elif kind == _DIM_GROUP:
            if name == style_ops.current_dim_style(self._document):
                return
            self.window.history.execute(style_ops.DeleteDimStyleCommand(name))
        else:
            return
        self.refresh()
        self.changed.emit()

    def _set_current(self) -> None:
        kind, name = self._selected()
        if name is None:
            return
        if kind == _TEXT_GROUP:
            self.window.history.execute(
                style_ops.SetCurrentTextStyleCommand(name))
        elif kind == _DIM_GROUP:
            self.window.history.execute(
                style_ops.SetCurrentDimStyleCommand(name))
        else:
            return
        self.refresh()
        self.changed.emit()
