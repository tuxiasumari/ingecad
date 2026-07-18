# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Entity properties palette — AutoCAD/BricsCAD PROPERTIES, context-sensitive.

The palette rebuilds itself for whatever is selected: a **General** section
(color, layer, linetype, linetype scale, lineweight, thickness) common to
every entity, plus a type section (Geometry / Text / …) whose editable rows
mirror what AutoCAD shows for that object. A filter combo at the top narrows a
mixed selection to one type, exactly like the palette's object dropdown.

Enum rows (layer/color/linetype/lineweight/style) apply across the whole
selection through :class:`SetPropertyCommand`; geometry and text edits touch the
single active entity through an in-place snapshot. Both land on the undo stack.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import actions, layers as layer_ops
from core.i18n import tr

BYLAYER_COLOR = 256      # AutoCAD sentinels
BYLAYER_LW = -1

_STYLE = """
PropertiesPanel { background: #26262a; }
PropertiesPanel QComboBox#filter { background: #1e1e22; color: #d8d8d8;
    border: 1px solid #3a3940; padding: 2px 4px; font-size: 11px; }
QTreeWidget { background: #26262a; color: #c8c8c8; border: 0;
    font-size: 11px; outline: 0; }
QTreeWidget::item { border-bottom: 1px solid #2f2f34; min-height: 19px; }
QTreeWidget QComboBox, QTreeWidget QLineEdit { background: #1e1e22;
    color: #e0e0e0; border: 1px solid #3a3940; padding: 0 3px;
    font-size: 11px; combobox-popup: 0; }
"""

_VARIES = object()   # marker: property differs across the selection


class Row:
    """One property line: how to read it and how to write it back."""

    def __init__(self, label, kind, get, apply=None, items=None):
        self.label = label
        self.kind = kind          # 'combo' | 'num' | 'str' | 'ro'
        self.get = get            # (entity) -> value  (or ignored for common)
        self.apply = apply        # (value) -> None
        self.items = items        # combo: list[(label, data)]


def _fmt(v) -> str:
    if isinstance(v, (int, float)):
        return f"{v:.6g}"
    return str(v)


class PropertiesPanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("PropertiesPanel")
        self.setStyleSheet(_STYLE)
        self._loading = False
        self._all: list = []
        self._filter: str | None = None   # None = all types

        self.filter_cb = QComboBox(self)
        self.filter_cb.setObjectName("filter")
        self.filter_cb.activated.connect(self._on_filter)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(6)
        self.tree.setUniformRowHeights(False)
        self.tree.setSelectionMode(QTreeWidget.NoSelection)
        self.tree.setFocusPolicy(Qt.NoFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.filter_cb)
        layout.addWidget(self.tree, 1)

        self.refresh()

    # -- data -----------------------------------------------------------------
    @property
    def _document(self):
        return self.window.document

    def _selection(self) -> list:
        tools = getattr(self.window, "tools", None)
        if tools is None or self._document is None:
            return []
        return [e for e in tools._selection_entities() if e.is_alive]

    def _active(self) -> list:
        if self._filter is None:
            return self._all
        return [e for e in self._all if e.dxftype() == self._filter]

    # -- top-level refresh ----------------------------------------------------
    def refresh(self) -> None:
        self._all = self._selection()
        # keep the current filter only if it still matches something
        types = {e.dxftype() for e in self._all}
        if self._filter not in types:
            self._filter = None
        self._rebuild_filter(types)
        self._rebuild_grid()

    def _rebuild_filter(self, types: set) -> None:
        self._loading = True
        self.filter_cb.clear()
        n = len(self._all)
        if n == 0:
            self.filter_cb.addItem(tr("No selection"), None)
            self.filter_cb.setEnabled(False)
            self._loading = False
            return
        self.filter_cb.setEnabled(True)
        if n > 1:
            self.filter_cb.addItem(tr("All ({n})", n=n), None)
        from collections import Counter
        counts = Counter(e.dxftype() for e in self._all)
        for t, c in sorted(counts.items()):
            label = _TYPE_LABEL.get(t, t.title())
            self.filter_cb.addItem(f"{label} ({c})" if c > 1 else label, t)
        idx = self.filter_cb.findData(self._filter)
        self.filter_cb.setCurrentIndex(max(0, idx))
        self._loading = False

    def _on_filter(self, index: int) -> None:
        if self._loading:
            return
        self._filter = self.filter_cb.itemData(index)
        self._rebuild_grid()

    # -- the grid -------------------------------------------------------------
    def _rebuild_grid(self) -> None:
        self._loading = True
        self.tree.clear()
        entities = self._active()
        if entities:
            for title, rows in self._schema(entities):
                self._add_section(title, rows, entities)
        self.tree.expandAll()
        self._loading = False

    def _add_section(self, title: str, rows: list, entities: list) -> None:
        head = QTreeWidgetItem(self.tree, [title])
        head.setFirstColumnSpanned(True)
        head.setFlags(Qt.ItemIsEnabled)
        f = head.font(0)
        f.setBold(True)
        head.setFont(0, f)
        head.setForeground(0, Qt.GlobalColor.lightGray)
        head.setBackground(0, self.palette().color(self.backgroundRole()).darker(112))
        for row in rows:
            self._add_row(head, row, entities)

    def _add_row(self, parent, row: Row, entities: list) -> None:
        item = QTreeWidgetItem(parent, [row.label, ""])
        item.setForeground(0, Qt.GlobalColor.gray)
        value = self._common(entities, row.get)
        if row.kind == "ro":
            item.setText(1, "" if value is _VARIES else _fmt(value))
            item.setForeground(1, Qt.GlobalColor.darkGray)
            return
        if row.kind == "combo":
            w = QComboBox()
            for lbl, data in row.items:
                w.addItem(lbl, data)
            w.setMaxVisibleItems(18)
            if value is _VARIES:
                w.setCurrentIndex(-1)
            else:
                j = w.findData(value)
                if j < 0 and row.items and callable(getattr(w, "setCurrentText", None)):
                    j = w.findText(str(value))
                w.setCurrentIndex(j)
            w.activated.connect(
                lambda i, r=row, cb=w: self._edit(r, cb.itemData(i)))
            self.tree.setItemWidget(item, 1, w)
        else:   # 'num' or 'str'
            w = QLineEdit("" if value is _VARIES else _fmt(value))
            w.setPlaceholderText(tr("*varies*") if value is _VARIES else "")
            w.editingFinished.connect(
                lambda r=row, le=w: self._edit_text(r, le))
            self.tree.setItemWidget(item, 1, w)

    def _common(self, entities, getter):
        if getter is None:
            return None
        try:
            vals = {getter(e) for e in entities}
        except Exception:
            return _VARIES
        return vals.pop() if len(vals) == 1 else _VARIES

    # -- edits ----------------------------------------------------------------
    def _edit(self, row: Row, value) -> None:
        if self._loading or row.apply is None:
            return
        row.apply(value)
        self._after_edit()

    def _edit_text(self, row: Row, editor: QLineEdit) -> None:
        if self._loading or row.apply is None:
            return
        text = editor.text().strip()
        if text == "":
            return
        if row.kind == "num":
            try:
                value = float(text)
            except ValueError:
                return
        else:
            value = text
        row.apply(value)
        self._after_edit()

    def _after_edit(self) -> None:
        self.window.regen_in_memory()
        self.refresh()

    def _set_prop(self, prop: str, value) -> None:
        ents = self._active()
        if ents:
            # via the tool controller: surgical hide + overlay = instant look
            self.window.tools._execute(
                actions.SetPropertyCommand(ents, prop, value))

    def _in_place(self, mutate) -> None:
        ents = self._active()
        if ents:
            actions.apply_in_place(self.window.history, ents, mutate)
            # instant look while the async regen catches up: hide the stale
            # base copies, show the mutated entities through the overlay
            tools = self.window.tools
            tools._invalidate_geometry()
            self.window.viewport.hide_handles([e.dxf.handle for e in ents])
            tools._pending_render.extend(
                e for e in ents
                if e.is_alive and e.dxf.owner is not None
                and e not in tools._pending_render)
            tools._refresh_overlay()

    def _set_comp(self, attr: str, axis: int, value: float) -> None:
        def mutate():
            for e in self._active():
                v = e.dxf.get(attr)
                comp = [v.x, v.y, v.z]
                comp[axis] = value
                e.dxf.set(attr, tuple(comp))
        self._in_place(mutate)

    # -- schema per entity type ----------------------------------------------
    def _schema(self, entities: list):
        sections = [(tr("General"), self._general_rows())]
        types = {e.dxftype() for e in entities}
        if len(types) == 1 and len(entities) == 1:
            builder = _TYPE_ROWS.get(next(iter(types)))
            if builder is not None:
                sections.append(builder(self, entities[0]))
        return sections

    def _general_rows(self):
        color_items = _color_items()
        lt_items = [(tr("ByLayer"), "ByLayer")] + [
            (lt, lt) for lt in layer_ops.available_linetypes(self._document)]
        lw_items = [(tr("ByLayer"), BYLAYER_LW)] + [
            (layer_ops.lineweight_label(lw), lw) for lw in layer_ops.LINEWEIGHTS]
        layer_items = [(i.name, i.name)
                       for i in layer_ops.layer_list(self._document)]
        return [
            Row(tr("Color"), "combo", lambda e: e.dxf.get("color", 256),
                lambda v: self._set_prop("color", v), color_items),
            Row(tr("Layer"), "combo", lambda e: e.dxf.layer,
                lambda v: self._set_prop("layer", v), layer_items),
            Row(tr("Linetype"), "combo",
                lambda e: e.dxf.get("linetype", "ByLayer"),
                lambda v: self._set_prop("linetype", v), lt_items),
            Row(tr("Linetype scale"), "num",
                lambda e: e.dxf.get("ltscale", 1.0),
                lambda v: self._set_prop("ltscale", v)),
            Row(tr("Lineweight"), "combo",
                lambda e: e.dxf.get("lineweight", -1),
                lambda v: self._set_prop("lineweight", v), lw_items),
            Row(tr("Thickness"), "num", lambda e: e.dxf.get("thickness", 0.0),
                lambda v: self._set_prop("thickness", v)),
        ]


# -- type-specific row builders (module-level, take panel + entity) ----------

def _pt_rows(panel, attr, label):
    return [
        Row(f"{label} X", "num", lambda e, a=attr: e.dxf.get(a).x,
            lambda v: panel._set_comp(attr, 0, v)),
        Row(f"{label} Y", "num", lambda e, a=attr: e.dxf.get(a).y,
            lambda v: panel._set_comp(attr, 1, v)),
        Row(f"{label} Z", "num", lambda e, a=attr: e.dxf.get(a).z,
            lambda v: panel._set_comp(attr, 2, v)),
    ]


def _line_rows(panel, e):
    s, w = e.dxf.start, e.dxf.end
    rows = _pt_rows(panel, "start", tr("Start"))
    rows += _pt_rows(panel, "end", tr("End"))
    rows += [
        Row(tr("Delta X"), "ro", lambda e: e.dxf.end.x - e.dxf.start.x),
        Row(tr("Delta Y"), "ro", lambda e: e.dxf.end.y - e.dxf.start.y),
        Row(tr("Length"), "ro",
            lambda e: math.dist((e.dxf.start.x, e.dxf.start.y),
                                (e.dxf.end.x, e.dxf.end.y))),
        Row(tr("Angle"), "ro",
            lambda e: math.degrees(math.atan2(e.dxf.end.y - e.dxf.start.y,
                                              e.dxf.end.x - e.dxf.start.x)) % 360),
    ]
    return (tr("Geometry"), rows)


def _circle_rows(panel, e):
    rows = _pt_rows(panel, "center", tr("Center"))
    rows += [
        Row(tr("Radius"), "num", lambda e: e.dxf.radius,
            lambda v: panel._set_prop("radius", v)),
        Row(tr("Diameter"), "num", lambda e: e.dxf.radius * 2,
            lambda v: panel._set_prop("radius", v / 2)),
        Row(tr("Circumference"), "ro", lambda e: 2 * math.pi * e.dxf.radius),
        Row(tr("Area"), "ro", lambda e: math.pi * e.dxf.radius ** 2),
    ]
    return (tr("Geometry"), rows)


def _arc_rows(panel, e):
    rows = _pt_rows(panel, "center", tr("Center"))
    rows += [
        Row(tr("Radius"), "num", lambda e: e.dxf.radius,
            lambda v: panel._set_prop("radius", v)),
        Row(tr("Start angle"), "num", lambda e: e.dxf.start_angle,
            lambda v: panel._set_prop("start_angle", v)),
        Row(tr("End angle"), "num", lambda e: e.dxf.end_angle,
            lambda v: panel._set_prop("end_angle", v)),
        Row(tr("Total angle"), "ro",
            lambda e: (e.dxf.end_angle - e.dxf.start_angle) % 360),
        Row(tr("Length"), "ro",
            lambda e: e.dxf.radius * math.radians(
                (e.dxf.end_angle - e.dxf.start_angle) % 360)),
    ]
    return (tr("Geometry"), rows)


def _ellipse_rows(panel, e):
    rows = _pt_rows(panel, "center", tr("Center"))
    rows += [
        Row(tr("Ratio"), "num", lambda e: e.dxf.ratio,
            lambda v: panel._set_prop("ratio", v)),
        Row(tr("Start angle"), "num", lambda e: math.degrees(e.dxf.start_param),
            lambda v: panel._set_prop("start_param", math.radians(v))),
        Row(tr("End angle"), "num", lambda e: math.degrees(e.dxf.end_param),
            lambda v: panel._set_prop("end_param", math.radians(v))),
    ]
    return (tr("Geometry"), rows)


def _point_rows(panel, e):
    return (tr("Geometry"), _pt_rows(panel, "location", tr("Position")))


def _lwpolyline_rows(panel, e):
    def set_closed(v):
        def mutate():
            for ent in panel._active():
                ent.close(bool(v))
        panel._in_place(mutate)
    rows = [
        Row(tr("Closed"), "combo", lambda e: 1 if e.closed else 0,
            set_closed, [(tr("No"), 0), (tr("Yes"), 1)]),
        Row(tr("Global width"), "num", lambda e: e.dxf.get("const_width", 0.0),
            lambda v: panel._set_prop("const_width", v)),
        Row(tr("Elevation"), "num", lambda e: e.dxf.get("elevation", 0.0),
            lambda v: panel._set_prop("elevation", v)),
        Row(tr("Vertices"), "ro", lambda e: len(e)),
        Row(tr("Length"), "ro", lambda e: _polyline_length(e)),
    ]
    if e.closed:
        rows.append(Row(tr("Area"), "ro", lambda e: _polyline_area(e)))
    return (tr("Geometry"), rows)


def _text_rows(panel, e):
    def set_text(v):
        def mutate():
            for ent in panel._active():
                ent.dxf.text = v
        panel._in_place(mutate)
    rows = [
        Row(tr("Contents"), "str", lambda e: e.dxf.text, set_text),
        Row(tr("Style"), "combo", lambda e: e.dxf.get("style", "Standard"),
            lambda v: panel._set_prop("style", v), _style_items(panel)),
        Row(tr("Height"), "num", lambda e: e.dxf.height,
            lambda v: panel._set_prop("height", v)),
        Row(tr("Rotation"), "num", lambda e: e.dxf.get("rotation", 0.0),
            lambda v: panel._set_prop("rotation", v)),
        Row(tr("Width factor"), "num", lambda e: e.dxf.get("width", 1.0),
            lambda v: panel._set_prop("width", v)),
        Row(tr("Obliquing"), "num", lambda e: e.dxf.get("oblique", 0.0),
            lambda v: panel._set_prop("oblique", v)),
    ]
    rows += _pt_rows(panel, "insert", tr("Position"))
    return (tr("Text"), rows)


def _mtext_rows(panel, e):
    rows = [
        Row(tr("Contents"), "ro",
            lambda e: e.text.replace("\n", " ")[:40]),
        Row(tr("Style"), "combo", lambda e: e.dxf.get("style", "Standard"),
            lambda v: panel._set_prop("style", v), _style_items(panel)),
        Row(tr("Text height"), "num", lambda e: e.dxf.char_height,
            lambda v: panel._set_prop("char_height", v)),
        Row(tr("Rotation"), "num", lambda e: e.dxf.get("rotation", 0.0),
            lambda v: panel._set_prop("rotation", v)),
        Row(tr("Width"), "num", lambda e: e.dxf.get("width", 0.0),
            lambda v: panel._set_prop("width", v)),
    ]
    rows += _pt_rows(panel, "insert", tr("Position"))
    return (tr("Text"), rows)


def _insert_rows(panel, e):
    rows = [Row(tr("Name"), "ro", lambda e: e.dxf.name)]
    rows += _pt_rows(panel, "insert", tr("Position"))
    rows += [
        Row(tr("Scale X"), "num", lambda e: e.dxf.get("xscale", 1.0),
            lambda v: panel._set_prop("xscale", v)),
        Row(tr("Scale Y"), "num", lambda e: e.dxf.get("yscale", 1.0),
            lambda v: panel._set_prop("yscale", v)),
        Row(tr("Scale Z"), "num", lambda e: e.dxf.get("zscale", 1.0),
            lambda v: panel._set_prop("zscale", v)),
        Row(tr("Rotation"), "num", lambda e: e.dxf.get("rotation", 0.0),
            lambda v: panel._set_prop("rotation", v)),
    ]
    return (tr("Geometry"), rows)


def _hatch_rows(panel, e):
    rows = [
        Row(tr("Pattern name"), "ro", lambda e: e.dxf.get("pattern_name", "")),
        Row(tr("Solid fill"), "ro",
            lambda e: tr("Yes") if e.dxf.solid_fill else tr("No")),
        Row(tr("Pattern angle"), "num", lambda e: e.dxf.get("pattern_angle", 0.0),
            lambda v: panel._set_prop("pattern_angle", v)),
        Row(tr("Pattern scale"), "num", lambda e: e.dxf.get("pattern_scale", 1.0),
            lambda v: panel._set_prop("pattern_scale", v)),
    ]
    return (tr("Pattern"), rows)


def _polyline_length(e) -> float:
    pts = [(p[0], p[1]) for p in e.get_points("xy")]
    if e.closed and pts:
        pts = pts + [pts[0]]
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def _polyline_area(e) -> float:
    pts = [(p[0], p[1]) for p in e.get_points("xy")]
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2


def _color_items():
    """(label, aci) pairs — mirror the layers panel's swatch combo data."""
    from views.layers_panel import ACI_NAMES
    items = [(tr("ByLayer"), 256), (tr("ByBlock"), 0)]
    for aci in (1, 2, 3, 4, 5, 6, 7, 8, 9):
        items.append((ACI_NAMES.get(aci, f"Color {aci}"), aci))
    return items


def _style_items(panel):
    doc = panel._document.doc
    return [(s.dxf.name, s.dxf.name) for s in doc.styles]


_TYPE_LABEL = {
    "LINE": "Line", "CIRCLE": "Circle", "ARC": "Arc", "ELLIPSE": "Ellipse",
    "LWPOLYLINE": "Polyline", "POLYLINE": "Polyline", "POINT": "Point",
    "TEXT": "Text", "MTEXT": "MText", "INSERT": "Block Reference",
    "HATCH": "Hatch", "DIMENSION": "Dimension",
}

_TYPE_ROWS = {
    "LINE": _line_rows,
    "CIRCLE": _circle_rows,
    "ARC": _arc_rows,
    "ELLIPSE": _ellipse_rows,
    "POINT": _point_rows,
    "LWPOLYLINE": _lwpolyline_rows,
    "TEXT": _text_rows,
    "MTEXT": _mtext_rows,
    "INSERT": _insert_rows,
    "HATCH": _hatch_rows,
}
