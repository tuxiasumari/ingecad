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

from render.batches import THICK_FLOATS, VERTEX_FLOATS, Batch, Scene
from render.view import ViewTransform2D

# OpenGL constants — kept as literals so we don't depend on PyOpenGL.
GL_FLOAT = 0x1406
GL_POINTS = 0x0000
GL_LINES = 0x0001
GL_TRIANGLES = 0x0004
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
# Lineweight display: mm of paper -> logical pixels (96 dpi reference,
# AutoCAD LWT look). 0.5 mm ~ 2 px, 1.0 mm ~ 4 px.
PX_PER_MM = 96.0 / 25.4


def _axes_vertices() -> np.ndarray:
    """X and Y world axes through the origin, interleaved pos(2) + color(4)."""
    rx, gx, bx = 0.48, 0.18, 0.18   # muted red for X
    ry, gy, by = 0.16, 0.42, 0.18   # muted green for Y
    data = [
        -AXIS_LEN, 0.0, rx, gx, bx, 1.0,
        AXIS_LEN, 0.0, rx, gx, bx, 1.0,
        0.0, -AXIS_LEN, ry, gy, by, 1.0,
        0.0, AXIS_LEN, ry, gy, by, 1.0,
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
        self._scene: Optional[Scene] = None
        self._scene_dirty = False
        # Per-primitive GPU buffers: name -> (vao, vbo, vertex_count)
        self._scene_bufs: dict[str, tuple] = {}

    # -- document hooks -------------------------------------------------------
    def set_scene(self, scene: Optional[Scene]) -> None:
        """Adopt a packed scene; the GL upload happens on the next frame."""
        self._scene = scene
        self._scene_dirty = True
        self.update()

    def scene_bounds(self) -> tuple[float, float, float, float]:
        """World bounds to fit on Zoom Extents.

        Without a document (or with an empty one) a human-scale frame around
        the origin keeps the canvas navigable.
        """
        if self._scene is not None and not self._scene.is_empty:
            min_x, min_y, max_x, max_y = self._scene.extents
            if max_x > min_x or max_y > min_y:
                return (min_x, min_y, max_x, max_y)
        return (-50.0, -50.0, 50.0, 50.0)

    def zoom_extents(self) -> None:
        self.view.zoom_extents(*self.scene_bounds())
        self.update()

    # -- GL lifecycle ---------------------------------------------------------
    def initializeGL(self) -> None:
        self._gl = QOpenGLFunctions(self.context())
        self._gl.initializeOpenGLFunctions()
        self._gl.glClearColor(*BACKGROUND, 1.0)

        self._program = self._compile_program("line.vert", "line.frag")
        self._loc_mvp = self._program.uniformLocation("u_mvp")
        self._thick_program = self._compile_program("thick.vert", "line.frag")
        self._loc_thick_mvp = self._thick_program.uniformLocation("u_mvp")
        self._loc_half_world = self._thick_program.uniformLocation("u_half_world")

        data = _axes_vertices()
        self._axes_vao, self._axes_vbo, self._axes_count = self._make_vao(data)
        # A scene set before the context existed uploads on the first frame.
        if self._scene is not None:
            self._scene_dirty = True

    def _make_vao(self, data: np.ndarray) -> tuple:
        """Upload interleaved [x, y, r, g, b, a] float32 data into a fresh VAO."""
        loc_pos = self._program.attributeLocation("a_pos")
        loc_color = self._program.attributeLocation("a_color")
        vao = QOpenGLVertexArrayObject(self)
        vao.create()
        vao.bind()
        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.create()
        vbo.bind()
        vbo.allocate(data.tobytes(), data.nbytes)
        stride = VERTEX_FLOATS * 4
        self._program.bind()
        self._program.enableAttributeArray(loc_pos)
        self._program.setAttributeBuffer(loc_pos, GL_FLOAT, 0, 2, stride)
        self._program.enableAttributeArray(loc_color)
        self._program.setAttributeBuffer(loc_color, GL_FLOAT, 2 * 4, 4, stride)
        self._program.release()
        vao.release()
        vbo.release()
        return vao, vbo, len(data) // VERTEX_FLOATS

    def _make_thick_vao(self, data: np.ndarray) -> tuple:
        """Upload interleaved [x, y, nx, ny, r, g, b, a] into a fresh VAO."""
        prog = self._thick_program
        loc_pos = prog.attributeLocation("a_pos")
        loc_normal = prog.attributeLocation("a_normal")
        loc_color = prog.attributeLocation("a_color")
        vao = QOpenGLVertexArrayObject(self)
        vao.create()
        vao.bind()
        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.create()
        vbo.bind()
        vbo.allocate(data.tobytes(), data.nbytes)
        stride = THICK_FLOATS * 4
        prog.bind()
        prog.enableAttributeArray(loc_pos)
        prog.setAttributeBuffer(loc_pos, GL_FLOAT, 0, 2, stride)
        prog.enableAttributeArray(loc_normal)
        prog.setAttributeBuffer(loc_normal, GL_FLOAT, 2 * 4, 2, stride)
        prog.enableAttributeArray(loc_color)
        prog.setAttributeBuffer(loc_color, GL_FLOAT, 4 * 4, 4, stride)
        prog.release()
        vao.release()
        vbo.release()
        return vao, vbo, len(data) // THICK_FLOATS

    def _upload_scene(self) -> None:
        """(Re)build the scene buffers. Requires a current GL context."""
        for vao, vbo, _count in self._scene_bufs.values():
            vbo.destroy()
            vao.destroy()
        self._scene_bufs.clear()
        self._scene_dirty = False
        if self._scene is None:
            return
        batches: dict[str, Batch] = {
            "triangles": self._scene.triangles,
            "lines": self._scene.lines,
            "points": self._scene.points,
        }
        for name, batch in batches.items():
            if batch.vertex_count:
                self._scene_bufs[name] = self._make_vao(batch.data)
        if self._scene.thick.vertex_count:
            self._scene_bufs["thick"] = self._make_thick_vao(self._scene.thick.data)

    def _compile_program(self, vert: str, frag: str) -> QOpenGLShaderProgram:
        prog = QOpenGLShaderProgram(self)
        prog.addShaderFromSourceFile(QOpenGLShader.Vertex, str(SHADER_DIR / vert))
        prog.addShaderFromSourceFile(QOpenGLShader.Fragment, str(SHADER_DIR / frag))
        if not prog.link():
            raise RuntimeError(f"shader link failed: {prog.log()}")
        return prog

    def resizeEvent(self, event) -> None:
        # Tracked here and not in resizeGL: resizeEvent always fires, even on
        # platforms without a GL context (CI's offscreen runner), keeping the
        # view transform testable headless.
        super().resizeEvent(event)
        self.view.width = max(self.width(), 1)
        self.view.height = max(self.height(), 1)

    def _mvp(self, ox: float = 0.0, oy: float = 0.0) -> QMatrix4x4:
        """World -> clip for vertices stored relative to origin (ox, oy).

        The subtraction (view center - vertex origin) happens here in float64;
        both operands are large (UTM), the difference is small, and only the
        small number reaches the float32 matrix.
        """
        kx, ky, cx, cy = self.view.ndc_factors()
        m = QMatrix4x4()
        m.scale(kx, ky, 1.0)
        m.translate(-(cx - ox), -(cy - oy), 0.0)
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

        if self._scene_dirty:
            self._upload_scene()

        self._program.bind()

        self._program.setUniformValue(self._loc_mvp, self._mvp())
        self._axes_vao.bind()
        gl.glDrawArrays(GL_LINES, 0, self._axes_count)
        self._axes_vao.release()

        if self._scene is not None and self._scene_bufs:
            scene_mvp = self._mvp(*self._scene.origin)
            self._program.setUniformValue(self._loc_mvp, scene_mvp)
            # Fills first, then lines and points on top of them.
            for name, mode in (("triangles", GL_TRIANGLES),
                               ("lines", GL_LINES),
                               ("points", GL_POINTS)):
                buf = self._scene_bufs.get(name)
                if buf is None:
                    continue
                vao, _vbo, count = buf
                vao.bind()
                gl.glDrawArrays(mode, 0, count)
                vao.release()
            self._program.release()
            self._draw_thick(gl, scene_mvp)
        else:
            self._program.release()

        self._paint_overlay()

    def _draw_thick(self, gl, scene_mvp: QMatrix4x4) -> None:
        """Thick lineweight quads: one draw per weight range (uniform width)."""
        buf = self._scene_bufs.get("thick")
        if buf is None:
            return
        vao, _vbo, _count = buf
        prog = self._thick_program
        prog.bind()
        prog.setUniformValue(self._loc_thick_mvp, scene_mvp)
        vao.bind()
        for rng in self._scene.thick.ranges:
            px = max(1.0, rng.lineweight * PX_PER_MM)
            half_world = (px / 2.0) / self.view.scale
            prog.setUniformValue1f(self._loc_half_world, half_world)
            gl.glDrawArrays(GL_TRIANGLES, rng.first, rng.count)
        vao.release()
        prog.release()

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
