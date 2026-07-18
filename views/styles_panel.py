# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Palette panel — AutoCAD-style Tool Palettes for hatch / text / dimension.

A gallery of styles with preview thumbnails, one category at a time. Single
click makes a style current (safe, no drawing); double click *uses* it — hatch
starts HATCH straight into point-picking with that pattern, text starts DTEXT
with that style. Editing a style is one step back (the ✎ button or right-click
→ Edit) so it never crowds the palette. Everything routes through the style
Commands for exact undo.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
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
_TEXT, _DIM, _HATCH = "TEXT", "DIM", "HATCH"

_STYLE = """
StylesPanel { background: #26262a; }
StylesPanel QListWidget { background: #1e1e22; color: #d0d0d0; border: 0;
    font-size: 11px; }
StylesPanel QListWidget::item { padding: 2px; }
StylesPanel QListWidget::item:selected { background: #35424f; }
StylesPanel QToolButton { border: none; color: #c8c8c8; padding: 3px 6px;
    font-size: 12px; }
StylesPanel QToolButton:hover, StylesPanel QToolButton:checked {
    background: #35424f; border-radius: 3px; }
"""


class StyleEditorDialog(QDialog):
    """Small modal editor for one text or dimension style."""

    def __init__(self, parent, cat: str, name: str, props: dict,
                 text_styles: list) -> None:
        super().__init__(parent)
        self.cat = cat
        self.setWindowTitle(tr("Edit style: {n}", n=name))
        self.setMinimumWidth(280)
        self._widgets: dict = {}
        form = QFormLayout(self)

        if cat == _TEXT:
            font = QComboBox()
            font.setEditable(True)
            font.addItems(COMMON_FONTS)
            if props["font"] and props["font"] not in COMMON_FONTS:
                font.insertItem(0, props["font"])
            font.setCurrentText(props["font"])
            self._widgets["font"] = font
            form.addRow(tr("Font"), font)
            self._add_num(form, "height", tr("Height"), props["height"], 0, 1e6, 2)
            self._add_num(form, "width", tr("Width factor"), props["width"], 0.01, 100, 3)
            self._add_num(form, "oblique", tr("Oblique"), props["oblique"], -85, 85, 1)
        else:
            self._add_num(form, "dimtxt", tr("Text height"), props["dimtxt"], 0, 1e6, 2)
            self._add_num(form, "dimasz", tr("Arrow size"), props["dimasz"], 0, 1e6, 2)
            self._add_num(form, "dimscale", tr("Overall scale"), props["dimscale"], 0, 1e6, 3)
            self._add_num(form, "dimexe", tr("Ext. beyond"), props["dimexe"], 0, 1e6, 3)
            self._add_num(form, "dimexo", tr("Ext. offset"), props["dimexo"], 0, 1e6, 3)
            dec = QSpinBox()
            dec.setRange(0, 8)
            dec.setValue(int(props["dimdec"]))
            self._widgets["dimdec"] = dec
            form.addRow(tr("Decimals"), dec)
            txsty = QComboBox()
            txsty.addItems(text_styles)
            cur = props["dimtxsty"] if props["dimtxsty"] in text_styles else (
                text_styles[0] if text_styles else "Standard")
            txsty.setCurrentText(cur)
            self._widgets["dimtxsty"] = txsty
            form.addRow(tr("Text style"), txsty)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _add_num(self, form, key, label, value, lo, hi, dec) -> None:
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setDecimals(dec)
        sb.setValue(float(value))
        self._widgets[key] = sb
        form.addRow(label, sb)

    def props(self) -> dict:
        out = {}
        for key, w in self._widgets.items():
            if isinstance(w, QDoubleSpinBox):
                out[key] = w.value()
            elif isinstance(w, QSpinBox):
                out[key] = w.value()
            elif isinstance(w, QComboBox):
                out[key] = w.currentText()
        return out


class StylesPanel(QWidget):
    changed = Signal()

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("StylesPanel")
        self.setStyleSheet(_STYLE)
        self._loading = False
        self._cat = _TEXT

        bar = QHBoxLayout()
        bar.setContentsMargins(2, 3, 2, 0)
        bar.setSpacing(2)
        self._cat_btns = {}
        for cat, label in ((_TEXT, tr("Text")), (_DIM, tr("Dimension")),
                           (_HATCH, tr("Hatch"))):
            b = QToolButton(self)
            b.setText(label)
            b.setCheckable(True)
            b.setChecked(cat == self._cat)
            b.clicked.connect(lambda _=False, c=cat: self._switch(c))
            self._cat_btns[cat] = b
            bar.addWidget(b)
        bar.addStretch()
        for glyph, tip, slot in (("＋", tr("New"), self._new),
                                 ("✎", tr("Edit"), self._edit),
                                 ("🗑", tr("Delete"), self._delete)):
            a = QToolButton(self)
            a.setText(glyph)
            a.setToolTip(tip)
            a.clicked.connect(slot)
            bar.addWidget(a)

        self.list = QListWidget(self)
        self.list.setIconSize(_ICON)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.itemClicked.connect(lambda it: self._set_current(it.data(Qt.UserRole)))
        self.list.itemDoubleClicked.connect(lambda it: self._activate(it.data(Qt.UserRole)))
        self.list.customContextMenuRequested.connect(self._menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addLayout(bar)
        layout.addWidget(self.list, 1)

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
        if self._document is not None or self._cat == _HATCH:
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
        from views.hatch_dialog import HatchDialog, _pattern_pixmap
        cur = HatchTool._last.get("pattern", "SOLID")
        for name in HatchDialog.COMMON:
            self._add_item(name, QIcon(_pattern_pixmap(name)), name == cur)

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

    # -- context menu ---------------------------------------------------------
    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        name = item.data(Qt.UserRole)
        menu = QMenu(self)
        use = menu.addAction(tr("Use (draw)"))
        setcur = menu.addAction(tr("Set current"))
        menu.addSeparator()
        edit = menu.addAction(tr("Edit..."))
        newa = menu.addAction(tr("New..."))
        dele = menu.addAction(tr("Delete"))
        dele.setEnabled(self._cat != _HATCH and name != "Standard")
        chosen = menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen is use:
            self._activate(name)
        elif chosen is setcur:
            self._set_current(name)
        elif chosen is edit:
            self._edit(name)
        elif chosen is newa:
            self._new()
        elif chosen is dele:
            self._delete()

    # -- single click: set current -------------------------------------------
    def _set_current(self, name) -> None:
        if self._loading or name is None:
            return
        if self._cat == _HATCH:
            from tools.blocks import HatchTool
            HatchTool._last["pattern"] = name
            self._rebold(name)
            self.changed.emit()
            return
        if self._document is None:
            return
        self._make_current(name)
        self._rebold(name)
        self.changed.emit()

    def _make_current(self, name: str) -> None:
        """Set the style current, but skip a no-op (avoids a dead undo step
        when a single click precedes a double click)."""
        if self._cat == _TEXT:
            if name != style_ops.current_text_style(self._document):
                self.window.history.execute(
                    style_ops.SetCurrentTextStyleCommand(name))
        else:
            if name != style_ops.current_dim_style(self._document):
                self.window.history.execute(
                    style_ops.SetCurrentDimStyleCommand(name))

    def _rebold(self, name: str) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            is_cur = it.data(Qt.UserRole) == name
            f = it.font()
            f.setBold(is_cur)
            it.setFont(f)
            base = it.data(Qt.UserRole)
            it.setText(f"{base}   ({tr('current')})" if is_cur else base)

    # -- double click: use it (draw) ------------------------------------------
    def _activate(self, name) -> None:
        if name is None:
            return
        if self._cat == _HATCH:
            from tools.blocks import HatchTool
            HatchTool._last["pattern"] = name
            HatchTool._skip_dialog = True         # go straight to point pick
            self._start_tool("HATCH")
            self._rebold(name)
            return
        if self._document is None:
            return
        if self._cat == _TEXT:
            self._make_current(name)
            self._rebold(name)
            self._start_tool("TEXT")
        else:   # dimension drawing is v0.2; set current for now
            self._make_current(name)
            self._rebold(name)
            self.window.command_line.echo(
                tr("Dimension style '{n}' is current "
                   "(creating dimensions arrives in v0.2).", n=name))
        self.changed.emit()

    def _start_tool(self, command: str) -> None:
        self.window.tools.start_tool(command)
        self.window.viewport.setFocus()

    # -- edit / new / delete --------------------------------------------------
    def _edit(self, name=None) -> None:
        if name is None or name is False:
            name = self._selected()
        if name is None:
            return
        if self._cat == _HATCH:
            from tools.blocks import HatchTool
            from views.hatch_dialog import HatchDialog
            dlg = HatchDialog(self.window, HatchTool._last)
            if dlg.exec():
                HatchTool._last = dlg.settings()
                self.refresh()
            return
        if self._document is None:
            return
        if self._cat == _TEXT:
            props = style_ops.text_style_props(self._document, name)
        else:
            props = style_ops.dim_style_props(self._document, name)
        dlg = StyleEditorDialog(self.window, self._cat, name, props,
                                style_ops.text_style_names(self._document))
        if not dlg.exec():
            return
        new = dlg.props()
        if self._cat == _TEXT:
            self.window.history.execute(style_ops.SetTextStylePropsCommand(name, new))
            self.window.regen_in_memory()
        else:
            self.window.history.execute(style_ops.SetDimStylePropsCommand(name, new))
        self.refresh()
        self.changed.emit()

    def _new(self) -> None:
        if self._cat == _HATCH:
            self._edit(self._selected() or "SOLID")   # opens the pattern dialog
            return
        if self._document is None:
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
        if self._document is None:
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
