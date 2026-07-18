# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Text and dimension styles — the AutoCAD STYLE and DIMSTYLE tables.

Every operation is an undoable Command so the style tables round-trip like
everything else. Text styles carry font / height / width factor / oblique;
dimension styles expose the handful of variables a plan actually needs (text
height, arrow size, overall scale, decimals, text style). The ezdxf document
IS the model — these edit ``doc.styles`` and ``doc.dimstyles`` directly.
"""
from __future__ import annotations

from core.commands import Command

# The dimension variables surfaced in the panel (name -> default).
DIM_VARS = {
    "dimtxt": 2.5,     # text height
    "dimasz": 2.5,     # arrow size
    "dimscale": 1.0,   # overall scale
    "dimexe": 1.25,    # extension beyond dim line
    "dimexo": 0.625,   # extension line offset
    "dimdec": 2,       # decimal places
    "dimtxsty": "Standard",   # text style
}


# AutoCAD's metric ISO-25 dimension style — the acadiso default. Seeded into
# every new drawing so the panel shows an established style (and the $DIMSTYLE
# header, which ezdxf defaults to "ISO-25", points at a real table entry).
ISO25_DIM = {
    "dimtxt": 2.5, "dimasz": 2.5, "dimexe": 1.25, "dimexo": 0.625,
    "dimgap": 0.625, "dimdec": 2, "dimscale": 1.0, "dimtad": 1,
    "dimtxsty": "Standard", "dimlfac": 1.0,
}


def install_default_styles(document) -> None:
    """Seed the standard styles a fresh AutoCAD drawing carries (idempotent)."""
    doc = document.doc
    if "ISO-25" not in doc.dimstyles:
        doc.dimstyles.new("ISO-25", dxfattribs=dict(ISO25_DIM))
    if doc.header.get("$DIMSTYLE", "Standard") not in doc.dimstyles:
        doc.header["$DIMSTYLE"] = "ISO-25"


# -- queries ------------------------------------------------------------------

def text_style_names(document) -> list[str]:
    return [s.dxf.name for s in document.doc.styles]


def current_text_style(document) -> str:
    return document.doc.header.get("$TEXTSTYLE", "Standard")


def text_style_props(document, name: str) -> dict:
    s = document.doc.styles.get(name)
    return {
        "font": s.dxf.get("font", ""),
        "height": s.dxf.get("height", 0.0),
        "width": s.dxf.get("width", 1.0),
        "oblique": s.dxf.get("oblique", 0.0),
    }


def dim_style_names(document) -> list[str]:
    return [d.dxf.name for d in document.doc.dimstyles]


def current_dim_style(document) -> str:
    return document.doc.header.get("$DIMSTYLE", "Standard")


def dim_style_props(document, name: str) -> dict:
    d = document.doc.dimstyles.get(name)
    return {k: d.dxf.get(k, default) for k, default in DIM_VARS.items()}


def unique_name(existing, base: str) -> str:
    if base not in existing:
        return base
    i = 1
    while f"{base}{i}" in existing:
        i += 1
    return f"{base}{i}"


# -- text style commands ------------------------------------------------------

class NewTextStyleCommand(Command):
    name = "STYLE"

    def __init__(self, style_name: str, attribs: dict | None = None) -> None:
        self.style_name = style_name
        self.attribs = dict(attribs or {})

    def do(self, document) -> None:
        document.doc.styles.new(self.style_name, dxfattribs=self.attribs)
        document.dirty = True

    def undo(self, document) -> None:
        document.doc.styles.remove(self.style_name)
        document.dirty = True


class DeleteTextStyleCommand(Command):
    name = "STYLE"

    def __init__(self, style_name: str) -> None:
        self.style_name = style_name
        self._attribs: dict = {}

    def do(self, document) -> None:
        s = document.doc.styles.get(self.style_name)
        self._attribs = dict(s.dxf.all_existing_dxf_attribs())
        self._attribs.pop("handle", None)
        self._attribs.pop("owner", None)
        document.doc.styles.remove(self.style_name)
        document.dirty = True

    def undo(self, document) -> None:
        attribs = {k: v for k, v in self._attribs.items() if k != "name"}
        document.doc.styles.new(self.style_name, dxfattribs=attribs)
        document.dirty = True


class SetTextStylePropsCommand(Command):
    name = "STYLE"

    def __init__(self, style_name: str, props: dict) -> None:
        self.style_name = style_name
        self.props = props
        self._old: dict = {}

    def do(self, document) -> None:
        s = document.doc.styles.get(self.style_name)
        self._old = {k: s.dxf.get(k, None) for k in self.props}
        for k, v in self.props.items():
            s.dxf.set(k, v)
        document.dirty = True

    def undo(self, document) -> None:
        s = document.doc.styles.get(self.style_name)
        for k, v in self._old.items():
            if v is None:
                s.dxf.discard(k)
            else:
                s.dxf.set(k, v)
        document.dirty = True


class SetCurrentTextStyleCommand(Command):
    name = "STYLE"

    def __init__(self, style_name: str) -> None:
        self.style_name = style_name
        self._old: str | None = None

    def do(self, document) -> None:
        self._old = document.doc.header.get("$TEXTSTYLE", "Standard")
        document.doc.header["$TEXTSTYLE"] = self.style_name
        document.dirty = True

    def undo(self, document) -> None:
        document.doc.header["$TEXTSTYLE"] = self._old
        document.dirty = True


# -- dimension style commands -------------------------------------------------

class NewDimStyleCommand(Command):
    name = "DIMSTYLE"

    def __init__(self, style_name: str, attribs: dict | None = None) -> None:
        self.style_name = style_name
        self.attribs = dict(attribs or {})

    def do(self, document) -> None:
        document.doc.dimstyles.new(self.style_name, dxfattribs=self.attribs)
        document.dirty = True

    def undo(self, document) -> None:
        document.doc.dimstyles.remove(self.style_name)
        document.dirty = True


class DeleteDimStyleCommand(Command):
    name = "DIMSTYLE"

    def __init__(self, style_name: str) -> None:
        self.style_name = style_name
        self._attribs: dict = {}

    def do(self, document) -> None:
        d = document.doc.dimstyles.get(self.style_name)
        self._attribs = dict(d.dxf.all_existing_dxf_attribs())
        self._attribs.pop("handle", None)
        self._attribs.pop("owner", None)
        document.doc.dimstyles.remove(self.style_name)
        document.dirty = True

    def undo(self, document) -> None:
        attribs = {k: v for k, v in self._attribs.items() if k != "name"}
        document.doc.dimstyles.new(self.style_name, dxfattribs=attribs)
        document.dirty = True


class SetDimStylePropsCommand(Command):
    name = "DIMSTYLE"

    def __init__(self, style_name: str, props: dict) -> None:
        self.style_name = style_name
        self.props = props
        self._old: dict = {}

    def do(self, document) -> None:
        d = document.doc.dimstyles.get(self.style_name)
        self._old = {k: d.dxf.get(k, None) for k in self.props}
        for k, v in self.props.items():
            d.dxf.set(k, v)
        document.dirty = True

    def undo(self, document) -> None:
        d = document.doc.dimstyles.get(self.style_name)
        for k, v in self._old.items():
            if v is None:
                d.dxf.discard(k)
            else:
                d.dxf.set(k, v)
        document.dirty = True


class SetCurrentDimStyleCommand(Command):
    name = "DIMSTYLE"

    def __init__(self, style_name: str) -> None:
        self.style_name = style_name
        self._old: str | None = None

    def do(self, document) -> None:
        self._old = document.doc.header.get("$DIMSTYLE", "Standard")
        document.doc.header["$DIMSTYLE"] = self.style_name
        document.dirty = True

    def undo(self, document) -> None:
        document.doc.header["$DIMSTYLE"] = self._old
        document.dirty = True
