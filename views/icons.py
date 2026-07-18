# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Painted line-art icons for the Draw and Modify toolbars.

Self-contained: each icon is drawn with QPainter into a pixmap, no image
files. Monochrome light strokes on transparent, sized for a 24 px toolbar.
Style echoes the classic AutoCAD toolbar glyphs (thin geometric line art).
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

_STROKE = QColor(210, 210, 210)
_ACCENT = QColor(90, 170, 255)
SIZE = 24


def _canvas() -> tuple[QPixmap, QPainter]:
    pm = QPixmap(SIZE, SIZE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(_STROKE, 1.4)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    return pm, p


def _node(p: QPainter, x: float, y: float) -> None:
    p.save()
    p.setPen(QPen(_ACCENT, 1.0))
    p.setBrush(QBrush(_ACCENT))
    p.drawRect(QRectF(x - 1.4, y - 1.4, 2.8, 2.8))
    p.restore()


# -- draw icons ----------------------------------------------------------------

def _line():
    pm, p = _canvas()
    p.drawLine(4, 19, 20, 5)
    _node(p, 4, 19)
    _node(p, 20, 5)
    p.end()
    return pm


def _circle():
    pm, p = _canvas()
    p.drawEllipse(QPointF(12, 12), 8, 8)
    _node(p, 12, 12)
    p.end()
    return pm


def _arc():
    pm, p = _canvas()
    path = QRectF(3, 6, 18, 18)
    p.drawArc(path, 20 * 16, 140 * 16)
    p.end()
    return pm


def _pline():
    pm, p = _canvas()
    poly = QPolygonF([QPointF(3, 18), QPointF(9, 8), QPointF(14, 15),
                      QPointF(21, 6)])
    p.drawPolyline(poly)
    for pt in poly:
        _node(p, pt.x(), pt.y())
    p.end()
    return pm


def _rectang():
    pm, p = _canvas()
    p.drawRect(QRectF(4, 6, 16, 12))
    p.end()
    return pm


def _polygon():
    pm, p = _canvas()
    pts = [QPointF(12 + 8 * math.cos(math.radians(90 + i * 72)),
                   12 + 8 * math.sin(math.radians(90 + i * 72)))
           for i in range(5)]
    p.drawPolygon(QPolygonF(pts))
    p.end()
    return pm


# -- modify icons --------------------------------------------------------------

def _erase():
    pm, p = _canvas()
    p.drawLine(5, 5, 19, 19)
    p.drawLine(19, 5, 5, 19)
    p.end()
    return pm


def _arrow(p: QPainter, x: float, y: float, ang: float) -> None:
    a = math.radians(ang)
    for da in (150, -150):
        b = math.radians(ang + da)
        p.drawLine(QPointF(x, y),
                   QPointF(x + 5 * math.cos(b), y + 5 * math.sin(b)))


def _move():
    pm, p = _canvas()
    p.drawLine(12, 4, 12, 20)
    p.drawLine(4, 12, 20, 12)
    _arrow(p, 12, 4, -90)
    _arrow(p, 12, 20, 90)
    _arrow(p, 4, 12, 180)
    _arrow(p, 20, 12, 0)
    p.end()
    return pm


def _copy():
    pm, p = _canvas()
    p.drawRect(QRectF(4, 8, 10, 10))
    p.drawRect(QRectF(10, 4, 10, 10))
    p.end()
    return pm


def _rotate():
    pm, p = _canvas()
    p.drawArc(QRectF(4, 4, 16, 16), 30 * 16, 260 * 16)
    _arrow(p, 19, 8, 120)
    p.end()
    return pm


def _scale():
    pm, p = _canvas()
    p.drawRect(QRectF(4, 12, 8, 8))
    p.drawRect(QRectF(9, 5, 11, 11))
    p.drawLine(6, 18, 18, 6)
    p.end()
    return pm


def _mirror():
    pm, p = _canvas()
    p.drawLine(12, 3, 12, 21)
    tri1 = QPolygonF([QPointF(10, 7), QPointF(4, 12), QPointF(10, 17)])
    tri2 = QPolygonF([QPointF(14, 7), QPointF(20, 12), QPointF(14, 17)])
    p.drawPolyline(tri1)
    p.drawPolyline(tri2)
    p.end()
    return pm


def _offset():
    pm, p = _canvas()
    p.drawLine(5, 4, 5, 20)
    p.save()
    p.setPen(QPen(_STROKE, 1.4, Qt.DashLine))
    p.drawLine(13, 4, 13, 20)
    p.restore()
    p.end()
    return pm


def _trim():
    pm, p = _canvas()
    p.drawLine(4, 15, 20, 15)
    p.save()
    p.setPen(QPen(_ACCENT, 1.4))
    p.drawLine(12, 5, 12, 22)
    p.restore()
    # scissor nick
    p.drawLine(10, 13, 14, 17)
    p.end()
    return pm


def _extend():
    pm, p = _canvas()
    p.save()
    p.setPen(QPen(_ACCENT, 1.4))
    p.drawLine(19, 5, 19, 20)
    p.restore()
    p.drawLine(4, 13, 19, 13)
    _arrow(p, 19, 13, 0)
    p.end()
    return pm


def _fillet():
    pm, p = _canvas()
    p.drawLine(5, 20, 5, 11)
    p.drawArc(QRectF(5, 5, 12, 12), 90 * 16, 90 * 16)
    p.drawLine(11, 5, 20, 5)
    p.end()
    return pm


_PAINTERS = {
    "LINE": _line, "CIRCLE": _circle, "ARC": _arc, "PLINE": _pline,
    "RECTANG": _rectang, "POLYGON": _polygon,
    "ERASE": _erase, "MOVE": _move, "COPY": _copy, "ROTATE": _rotate,
    "SCALE": _scale, "MIRROR": _mirror, "OFFSET": _offset, "TRIM": _trim,
    "EXTEND": _extend, "FILLET": _fillet,
}


def command_icon(name: str) -> QIcon:
    painter = _PAINTERS.get(name)
    return QIcon(painter()) if painter else QIcon()
