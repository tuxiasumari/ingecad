# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Layer state and operations, AutoCAD/BricsCAD semantics.

The current layer is where new entities land. Layer 0 is the special base
layer (cannot be renamed or deleted). Standard states: on/off, freeze/thaw,
lock/unlock, plus color and linetype. Everything routes through ezdxf's
layer table so the round-trip stays conservative.

Layer edits go through Commands (undoable), except selecting the current
layer, which is view state, not a document mutation.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.commands import Command
from core.i18n import tr


# Standard AutoCAD lineweights, hundredths of a millimetre. -3 = Default
# (uses the drawing's default weight), -1 = ByLayer (not valid on a layer).
LINEWEIGHTS = [-3, 0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70,
               80, 90, 100, 106, 120, 140, 158, 200, 211]


def lineweight_label(value: int) -> str:
    if value == -3:
        return "Default"
    if value == -2:
        return "ByBlock"
    if value == -1:
        return "ByLayer"
    return f"{value / 100:.2f} mm"


@dataclass
class LayerInfo:
    name: str
    color: int          # ACI (AutoCAD Color Index)
    linetype: str
    lineweight: int     # hundredths of mm, or -3 Default
    is_on: bool
    is_frozen: bool
    is_locked: bool
    is_current: bool


def layer_list(document) -> list[LayerInfo]:
    """Snapshot of all layers, layer 0 first then alphabetical."""
    current = current_layer_name(document)
    infos = []
    for layer in document.doc.layers:
        infos.append(LayerInfo(
            name=layer.dxf.name,
            color=abs(layer.dxf.color),      # negative color = layer off
            linetype=layer.dxf.linetype,
            lineweight=layer.dxf.get("lineweight", -3),
            is_on=layer.is_on(),
            is_frozen=layer.is_frozen(),
            is_locked=layer.is_locked(),
            is_current=(layer.dxf.name == current),
        ))
    infos.sort(key=lambda i: (i.name != "0", i.name.lower()))
    return infos


def available_linetypes(document) -> list[str]:
    """Linetype names loaded in the document (Continuous always first)."""
    names = [lt.dxf.name for lt in document.doc.linetypes
             if lt.dxf.name not in ("ByBlock", "ByLayer")]
    names.sort(key=lambda n: (n != "Continuous", n.lower()))
    return names


def current_layer_name(document) -> str:
    return document.doc.header.get("$CLAYER", "0")


def set_current_layer(document, name: str) -> None:
    if name in document.doc.layers:
        document.doc.header["$CLAYER"] = name


def unique_layer_name(document, base: str = tr("Layer")) -> str:
    existing = {layer.dxf.name for layer in document.doc.layers}
    i = 1
    while f"{base}{i}" in existing:
        i += 1
    return f"{base}{i}"


# -- commands ------------------------------------------------------------------

class NewLayerCommand(Command):
    name = "new layer"

    def __init__(self, layer_name: str, color: int = 7) -> None:
        self.layer_name = layer_name
        self.color = color

    def do(self, document) -> None:
        if self.layer_name not in document.doc.layers:
            document.doc.layers.add(self.layer_name, color=self.color)
        document.dirty = True

    def undo(self, document) -> None:
        if self.layer_name in document.doc.layers:
            document.doc.layers.remove(self.layer_name)
        document.dirty = True


class DeleteLayerCommand(Command):
    name = "delete layer"

    def __init__(self, layer_name: str) -> None:
        self.layer_name = layer_name
        self._color = 7
        self._linetype = "Continuous"

    def do(self, document) -> None:
        layer = document.doc.layers.get(self.layer_name)
        self._color = layer.dxf.color
        self._linetype = layer.dxf.linetype
        document.doc.layers.remove(self.layer_name)
        document.dirty = True

    def undo(self, document) -> None:
        layer = document.doc.layers.add(self.layer_name)
        layer.dxf.color = self._color
        layer.dxf.linetype = self._linetype
        document.dirty = True


class RenameLayerCommand(Command):
    name = "rename layer"

    def __init__(self, old_name: str, new_name: str) -> None:
        self.old_name = old_name
        self.new_name = new_name

    def do(self, document) -> None:
        document.doc.layers.get(self.old_name).rename(self.new_name)
        if current_layer_name(document) == self.old_name:
            set_current_layer(document, self.new_name)
        document.dirty = True

    def undo(self, document) -> None:
        document.doc.layers.get(self.new_name).rename(self.old_name)
        if current_layer_name(document) == self.new_name:
            set_current_layer(document, self.old_name)
        document.dirty = True


class LayerPropertyCommand(Command):
    """Set one property (color/linetype/on/frozen/locked) with exact undo."""

    name = "layer property"

    def __init__(self, layer_name: str, prop: str, value) -> None:
        self.layer_name = layer_name
        self.prop = prop
        self.value = value
        self._old = None

    def _apply(self, document, value):
        layer = document.doc.layers.get(self.layer_name)
        if self.prop == "color":
            old = abs(layer.dxf.color)
            layer.color = value          # keeps on/off sign via ezdxf
            return old
        if self.prop == "linetype":
            old = layer.dxf.linetype
            layer.dxf.linetype = value
            return old
        if self.prop == "lineweight":
            old = layer.dxf.get("lineweight", -3)
            layer.dxf.lineweight = value
            return old
        if self.prop == "on":
            old = layer.is_on()
            layer.on() if value else layer.off()
            return old
        if self.prop == "frozen":
            old = layer.is_frozen()
            layer.freeze() if value else layer.thaw()
            return old
        if self.prop == "locked":
            old = layer.is_locked()
            layer.lock() if value else layer.unlock()
            return old
        return None

    def do(self, document) -> None:
        self._old = self._apply(document, self.value)
        document.dirty = True

    def undo(self, document) -> None:
        self._apply(document, self._old)
        document.dirty = True
