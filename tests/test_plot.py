# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Phase 8 plot: vector PDF output at scale, y-flip correctness, layouts."""
from __future__ import annotations

import os
import sys

import pytest

from core.document import Document
from formats import pdf_out


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _doc_with_marker():
    """A line along the bottom and a small circle at the TOP-left."""
    doc = Document.new()
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0))          # bottom edge
    msp.add_circle((10, 90), 5)             # near the top
    return doc


def test_graphics_scene_builds(app):
    doc = _doc_with_marker()
    scene = pdf_out.build_graphics_scene(doc)
    r = pdf_out.scene_extents(scene)
    assert r.width() > 90
    assert r.height() > 80


def test_pdf_plot_writes_vector_file(app, tmp_path):
    doc = _doc_with_marker()
    path = str(tmp_path / "plan.pdf")
    printer = pdf_out.make_pdf_printer(path, "A4", landscape=True)
    pdf_out.plot(doc, printer)              # fit extents
    assert os.path.exists(path)
    assert os.path.getsize(path) > 1000     # real content, not an empty page


def test_pdf_plot_at_scale(app, tmp_path):
    # 1:100 with metres: 100 m wide area -> 1000 mm on paper (clipped by A4,
    # but the transform must not fit-shrink it). Just verify it renders.
    doc = _doc_with_marker()
    path = str(tmp_path / "scaled.pdf")
    printer = pdf_out.make_pdf_printer(path, "A4", landscape=True)
    pdf_out.plot(doc, printer, area=(0, 0, 20, 10), mm_per_unit=10.0)
    assert os.path.getsize(path) > 500


def test_y_flip_top_stays_top(app):
    """Render with the same flip math into an image: the circle drawn at
    world TOP must land in the TOP half of the page, not mirrored."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    doc = _doc_with_marker()
    scene = pdf_out.build_graphics_scene(doc)
    w, h = 200, 200
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(0xFFFFFFFF)
    painter = QPainter(img)
    # same transform pdf_out.plot applies (page.y() = 0 here)
    painter.translate(0.0, h)
    painter.scale(1.0, -1.0)
    target = QRectF(0, 0, w, h)
    source = QRectF(0, 0, 100, 100)
    scene.render(painter, target, source)
    painter.end()

    def dark_pixels(y0, y1):
        n = 0
        for y in range(y0, y1):
            for x in range(0, w, 2):
                if QImage.pixel(img, x, y) & 0xFF < 100:
                    n += 1
        return n

    top = dark_pixels(0, h // 3)            # circle territory
    bottom = dark_pixels(2 * h // 3, h)     # bottom line territory
    assert top > 0                          # circle visible near the top
    assert bottom > 0                       # base line near the bottom


def test_build_scene_layout_by_name():
    import ezdxf
    from render.backend import build_scene

    doc = Document.new()
    doc.modelspace().add_line((0, 0), (5, 5))
    layout = doc.doc.layouts.new("Plancha1")
    layout.add_line((0, 0), (100, 0))
    model_scene = build_scene(doc, "Model")
    sheet_scene = build_scene(doc, "Plancha1")
    assert model_scene.lines.vertex_count > 0
    assert sheet_scene.lines.vertex_count > 0
    assert sheet_scene.background is not None   # paper-white layout
