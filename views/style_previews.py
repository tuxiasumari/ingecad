# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Preview graphics for the Styles panel — BricsCAD-style thumbnails.

A text style is drawn as a sample string in its font, width factor and oblique
angle; a dimension style is drawn as a small dimension (extension lines, arrows
sized by DIMASZ, text sized by DIMTXT) so the settings read at a glance.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)

_BG = QColor(244, 244, 244)
_FG = QColor(25, 25, 25)
_SAMPLE = "AaBbCc 0123"

# Rough .shx -> family hints; TTF/OTF use the file's base name as the family.
_SHX_FAMILY = {
    "txt": "monospace", "monotxt": "monospace",
    "romans": "serif", "romant": "serif", "italic": "serif",
    "isocp": "sans-serif", "isocpeur": "sans-serif", "simplex": "sans-serif",
}


def font_family(font_name: str) -> str:
    name = (font_name or "txt").strip()
    low = name.lower()
    if low.endswith((".ttf", ".otf", ".ttc")):
        return name.rsplit(".", 1)[0]
    stem = low.rsplit(".", 1)[0]
    return _SHX_FAMILY.get(stem, "sans-serif")


def _tile(w: int, h: int) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(w, h)
    pm.fill(_BG)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    return pm, p


def text_style_pixmap(props: dict, w: int = 150, h: int = 34,
                      sample: str = _SAMPLE) -> QPixmap:
    pm, p = _tile(w, h)
    f = QFont(font_family(props.get("font", "txt")))
    f.setPixelSize(max(8, int(h * 0.5)))
    p.setFont(f)
    p.setPen(QPen(_FG))
    width = props.get("width", 1.0) or 1.0
    oblique = props.get("oblique", 0.0) or 0.0
    t = QTransform()
    t.translate(6, h * 0.72)
    t.scale(width, 1.0)
    if oblique:
        t.shear(-math.tan(math.radians(oblique)), 0.0)
    p.setTransform(t)
    p.drawText(QPointF(0, 0), sample)
    p.end()
    return pm


def _arrow(p: QPainter, tip: QPointF, direction: int, size: float) -> None:
    """Filled dimension arrowhead pointing outward from the dimension line."""
    dx = direction * size
    base_y = size * 0.35
    poly = QPolygonF([
        tip,
        QPointF(tip.x() + dx, tip.y() - base_y),
        QPointF(tip.x() + dx, tip.y() + base_y),
    ])
    p.setBrush(QBrush(_FG))
    p.setPen(Qt.NoPen)
    p.drawPolygon(poly)


def dim_style_pixmap(props: dict, w: int = 150, h: int = 60) -> QPixmap:
    pm, p = _tile(w, h)
    pen = QPen(_FG)
    pen.setWidthF(1.0)
    p.setPen(pen)

    txt = float(props.get("dimtxt", 2.5) or 2.5)
    asz = float(props.get("dimasz", 2.5) or 2.5)
    exe = float(props.get("dimexe", 1.25) or 1.25)
    exo = float(props.get("dimexo", 0.625) or 0.625)
    dec = int(props.get("dimdec", 2) or 0)

    # Map model units to pixels so DIMTXT/DIMASZ differences show. The measured
    # span is a fixed 25 units drawn across most of the tile width.
    span = 25.0
    left, right = 16.0, w - 16.0
    scale = (right - left) / span
    dim_y = h * 0.62
    top = h * 0.16

    # extension lines (offset from the "geometry", overshoot past the dim line)
    for x in (left, right):
        p.drawLine(QPointF(x, top + exo * scale),
                   QPointF(x, dim_y + exe * scale))
    # dimension line
    p.drawLine(QPointF(left, dim_y), QPointF(right, dim_y))
    # arrowheads
    a = max(3.0, asz * scale)
    _arrow(p, QPointF(left, dim_y), +1, a)
    _arrow(p, QPointF(right, dim_y), -1, a)
    # measurement text, centered above the dimension line
    f = QFont(font_family(props.get("dimtxsty", "Standard")))
    f.setPixelSize(max(7, int(txt * scale)))
    p.setFont(f)
    p.setPen(QPen(_FG))
    label = f"{span:.{dec}f}"
    fm = p.fontMetrics()
    tx = (left + right) / 2 - fm.horizontalAdvance(label) / 2
    p.drawText(QPointF(tx, dim_y - a - 2), label)
    p.end()
    return pm
