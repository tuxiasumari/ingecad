# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Plot to PDF or a system printer, at a real scale (Phase 8).

The ezdxf drawing frontend replays the layout into a QGraphicsScene through
PyQtBackend (true vector graphics — lines stay lines in the PDF), and
QGraphicsScene.render maps a world-area rectangle onto the printable page at
the requested scale. 1:N metric scaling: one paper mm equals N drawing mm, so
a drawing in metres plots 1:100 with ``mm_per_unit = 1000 / 100``.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter

# Paper sizes in mm (portrait), the ones a civil plan actually uses.
PAPER_SIZES_MM = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A2": (420.0, 594.0),
    "A1": (594.0, 841.0),
    "A0": (841.0, 1189.0),
    "Letter": (215.9, 279.4),
}

# Common metric plot scales (denominators of 1:N).
COMMON_SCALES = (10, 20, 25, 50, 75, 100, 125, 200, 250, 500, 1000, 2000)


def build_graphics_scene(document, layout_name: str | None = None):
    """Replay a layout into a QGraphicsScene (vector items, world coords)."""
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.pyqt import PyQtBackend
    from PySide6.QtWidgets import QGraphicsScene

    from render.backend import pick_layout

    if layout_name and layout_name != "Model" \
            and layout_name in document.doc.layouts:
        layout = document.doc.layouts.get(layout_name)
    else:
        layout, _name = pick_layout(document)
    scene = QGraphicsScene()
    backend = PyQtBackend(scene)
    context = RenderContext(document.doc)
    Frontend(context, backend).draw_layout(layout, finalize=True)
    return scene


def scene_extents(scene) -> QRectF:
    """World-coordinate bounding rect of everything in the graphics scene."""
    return scene.itemsBoundingRect()


def plot(document, printer, layout_name: str | None = None,
         area: tuple[float, float, float, float] | None = None,
         mm_per_unit: float | None = None) -> None:
    """Render onto ``printer`` (PDF file or a physical printer).

    ``area`` is the world rect (x0, y0, x1, y1) to plot; None plots the
    extents. ``mm_per_unit`` fixes the scale (paper mm per drawing unit);
    None fits the area to the page. The plot is centred on the page.
    """
    scene = build_graphics_scene(document, layout_name)
    if area is None:
        r = scene_extents(scene)
        area = (r.left(), r.top(), r.right(), r.bottom())
    x0, y0, x1, y1 = area
    aw, ah = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)

    painter = QPainter(printer)
    try:
        page = printer.pageRect(printer.Unit.DevicePixel)
        px_per_mm = printer.resolution() / 25.4
        if mm_per_unit is None:
            px_per_unit = min(page.width() / aw, page.height() / ah)
        else:
            px_per_unit = mm_per_unit * px_per_mm
        tw, th = aw * px_per_unit, ah * px_per_unit
        tx = page.x() + (page.width() - tw) / 2.0
        ty = page.y() + (page.height() - th) / 2.0

        # DXF is y-up, the page is y-down: flip the painter and hand render()
        # a target rect expressed in the flipped coordinate system.
        painter.translate(0.0, page.y() * 2 + page.height())
        painter.scale(1.0, -1.0)
        target = QRectF(tx, page.y() * 2 + page.height() - (ty + th), tw, th)
        source = QRectF(x0, y0, aw, ah)
        scene.render(painter, target, source)
    finally:
        painter.end()


def make_pdf_printer(path: str, paper: str = "A4", landscape: bool = True):
    """A QPrinter configured for vector PDF output."""
    from PySide6.QtGui import QPageLayout, QPageSize
    from PySide6.QtPrintSupport import QPrinter

    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(path)
    size_id = getattr(QPageSize, paper, QPageSize.A4)
    printer.setPageSize(QPageSize(size_id))
    printer.setPageOrientation(
        QPageLayout.Landscape if landscape else QPageLayout.Portrait)
    return printer
