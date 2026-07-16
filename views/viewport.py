# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""2D drafting viewport: dark model space, AutoCAD-style crosshair, pan/zoom.

Uses PySide6's bundled QOpenGL* helper classes — no external GL bindings.

Wayland requires every frame to be drawn explicitly: ``paintGL`` always calls
``glClear`` first, and re-establishes all relevant GL state each frame because
the QPainter overlay (crosshair, UCS icon) contaminates it between frames.
Both gotchas were hard-won in IngeTrazo — do not "simplify" them away.

Navigation (AutoCAD-like):
- Middle-button drag: pan
- Wheel: zoom in/out at the cursor
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QMatrix4x4, QOpenGLFunctions, QPainter, QPen
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from render.view import ViewTransform2D

# OpenGL constants — kept as literals so we don't depend on PyOpenGL.
GL_FLOAT = 0x1406
GL_LINES = 0x0001
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_TEST = 0x0B71
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303

SHADER_DIR = Path(__file__).resolve().parents[1] / "resources" / "shaders"

# Classic dark model space (near-black, slightly blue like AutoCAD's default).
BACKGROUND = (0.129, 0.149, 0.169)
AXIS_LEN = 1.0e6  # world units; clipped by GL, cheap to keep "infinite"
CROSSHAIR_COLOR = QColor(215, 215, 215, 210)
PICKBOX_PX = 8


def _axes_vertices() -> np.ndarray:
    """X and Y world axes through the origin, interleaved pos(2) + color(3)."""
    rx, gx, bx = 0.48, 0.18, 0.18   # muted red for X
    ry, gy, by = 0.16, 0.42, 0.18   # muted green for Y
    data = [
        -AXIS_LEN, 0.0, rx, gx, bx,
        AXIS_LEN, 0.0, rx, gx, bx,
        0.0, -AXIS_LEN, ry, gy, by,
        0.0, AXIS_LEN, ry, gy, by,
    ]
    return np.asarray(data, dtype=np.float32)


class Viewport(QOpenGLWidget):
    """Model-space canvas. Owns the view transform; documents plug in at F1."""

    cursorMoved = Signal(float, float)  # world coordinates under the cursor

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.view = ViewTransform2D(width=self.width(), height=self.height())
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        # The crosshair *is* the cursor in model space, AutoCAD-style.
        self.setCursor(Qt.BlankCursor)
        self._cursor: Optional[QPointF] = None
        self._panning = False
        self._last_pos = QPointF()
        self._gl: Optional[QOpenGLFunctions] = None
        self._program: Optional[QOpenGLShaderProgram] = None

    # -- document hooks (placeholder until Phase 1) --------------------------
    def scene_bounds(self) -> tuple[float, float, float, float]:
        """World bounds to fit on Zoom Extents.

        Phase 1 replaces this with the open document's extents; the placeholder
        frames the origin at a human scale so the empty canvas is navigable.
        """
        return (-50.0, -50.0, 50.0, 50.0)

    def zoom_extents(self) -> None:
        self.view.zoom_extents(*self.scene_bounds())
        self.update()

    # -- GL lifecycle ---------------------------------------------------------
    def initializeGL(self) -> None:
        self._gl = QOpenGLFunctions(self.context())
        self._gl.initializeOpenGLFunctions()
        self._gl.glClearColor(*BACKGROUND, 1.0)

        self._program = self._compile_program()
        self._loc_mvp = self._program.uniformLocation("u_mvp")
        loc_pos = self._program.attributeLocation("a_pos")
        loc_color = self._program.attributeLocation("a_color")

        data = _axes_vertices()
        self._axes_count = len(data) // 5
        self._axes_vao = QOpenGLVertexArrayObject(self)
        self._axes_vao.create()
        self._axes_vao.bind()
        self._axes_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self._axes_vbo.create()
        self._axes_vbo.bind()
        self._axes_vbo.allocate(data.tobytes(), data.nbytes)
        stride = 5 * 4
        self._program.bind()
        self._program.enableAttributeArray(loc_pos)
        self._program.setAttributeBuffer(loc_pos, GL_FLOAT, 0, 2, stride)
        self._program.enableAttributeArray(loc_color)
        self._program.setAttributeBuffer(loc_color, GL_FLOAT, 2 * 4, 3, stride)
        self._program.release()
        self._axes_vao.release()
        self._axes_vbo.release()

    def _compile_program(self) -> QOpenGLShaderProgram:
        prog = QOpenGLShaderProgram(self)
        prog.addShaderFromSourceFile(QOpenGLShader.Vertex, str(SHADER_DIR / "line.vert"))
        prog.addShaderFromSourceFile(QOpenGLShader.Fragment, str(SHADER_DIR / "line.frag"))
        if not prog.link():
            raise RuntimeError(f"shader link failed: {prog.log()}")
        return prog

    def resizeGL(self, w: int, h: int) -> None:  # noqa: ARG002 — logical size read from widget
        self.view.width = max(self.width(), 1)
        self.view.height = max(self.height(), 1)

    def _mvp(self) -> QMatrix4x4:
        kx, ky, cx, cy = self.view.ndc_factors()
        m = QMatrix4x4()
        m.scale(kx, ky, 1.0)
        # Translation computed here in float64; cast to float32 only inside Qt.
        m.translate(-cx, -cy, 0.0)
        return m

    def paintGL(self) -> None:
        gl = self._gl
        # Re-establish state every frame: the QPainter overlay below disables
        # GL state behind our back and Wayland shows stale memory otherwise.
        gl.glDisable(GL_DEPTH_TEST)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        gl.glClearColor(*BACKGROUND, 1.0)
        gl.glClear(GL_COLOR_BUFFER_BIT)

        self._program.bind()
        self._program.setUniformValue(self._loc_mvp, self._mvp())
        self._axes_vao.bind()
        gl.glDrawArrays(GL_LINES, 0, self._axes_count)
        self._axes_vao.release()
        self._program.release()

        self._paint_overlay()

    # -- overlay (QPainter, logical pixels) -----------------------------------
    def _paint_overlay(self) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._draw_ucs_icon(p)
        if self._cursor is not None and not self._panning:
            self._draw_crosshair(p, self._cursor)
        p.end()

    def _draw_ucs_icon(self, p: QPainter) -> None:
        """Classic UCS icon: red X / green Y arrows at the world origin.

        When the origin is outside the view, the icon anchors to the lower-left
        corner (AutoCAD's off-origin behavior).
        """
        ox, oy = self.view.world_to_screen(0.0, 0.0)
        margin = 40
        if not (-margin < ox < self.width() + margin and -margin < oy < self.height() + margin):
            ox, oy = 60.0, self.height() - 60.0
        size = 48
        x_pen = QPen(QColor(205, 82, 82), 2)
        y_pen = QPen(QColor(96, 190, 96), 2)
        p.setPen(x_pen)
        p.drawLine(QPointF(ox, oy), QPointF(ox + size, oy))
        p.drawLine(QPointF(ox + size, oy), QPointF(ox + size - 8, oy - 4))
        p.drawLine(QPointF(ox + size, oy), QPointF(ox + size - 8, oy + 4))
        p.drawText(QPointF(ox + size + 6, oy + 4), "X")
        p.setPen(y_pen)
        p.drawLine(QPointF(ox, oy), QPointF(ox, oy - size))
        p.drawLine(QPointF(ox, oy - size), QPointF(ox - 4, oy - size + 8))
        p.drawLine(QPointF(ox, oy - size), QPointF(ox + 4, oy - size + 8))
        p.drawText(QPointF(ox - 4, oy - size - 6), "Y")

    def _draw_crosshair(self, p: QPainter, pos: QPointF) -> None:
        p.setPen(QPen(CROSSHAIR_COLOR, 1))
        x, y = pos.x(), pos.y()
        half = PICKBOX_PX / 2
        # Full-viewport crosshair with the pick box gap-free on top (classic).
        p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.drawRect(x - half, y - half, PICKBOX_PX, PICKBOX_PX)

    # -- input -----------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._last_pos = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            self.update()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.BlankCursor)
            self.update()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._panning:
            delta = pos - self._last_pos
            self._last_pos = pos
            self.view.pan_pixels(delta.x(), delta.y())
        else:
            self._cursor = pos
            wx, wy = self.view.screen_to_world(pos.x(), pos.y())
            self.cursorMoved.emit(wx, wy)
        self.update()

    def wheelEvent(self, event) -> None:
        notches = event.angleDelta().y() / 120.0
        if notches:
            pos = event.position()
            self.view.zoom_at(pos.x(), pos.y(), 1.2 ** notches)
            self.update()

    def leaveEvent(self, event) -> None:
        self._cursor = None
        self.update()
        super().leaveEvent(event)
