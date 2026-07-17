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
from PySide6.QtWidgets import QRubberBand

from render.batches import THICK_DTYPE, VERTEX_DTYPE, Batch, Scene
from render.view import ViewTransform2D

# OpenGL constants — kept as literals so we don't depend on PyOpenGL.
GL_FLOAT = 0x1406
GL_UNSIGNED_BYTE = 0x1401
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
CROSSHAIR_COLOR = QColor(215, 215, 215, 210)        # over the dark canvas
CROSSHAIR_COLOR_LIGHT = QColor(40, 40, 40, 210)     # over paper-white layouts
PICKBOX_PX = 8
# Lineweight display: mm of paper -> logical pixels (96 dpi reference,
# AutoCAD LWT look). 0.5 mm ~ 2 px, 1.0 mm ~ 4 px.
PX_PER_MM = 96.0 / 25.4
# Text glyphs smaller than this on screen are illegible: skip their ranges
# (they reappear instantly on zoom-in — the data stays on the GPU).
MIN_TEXT_PX = 2.0


def _axes_vertices() -> np.ndarray:
    """X and Y world axes through the origin in the standard vertex format."""
    data = np.zeros(4, dtype=VERTEX_DTYPE)
    data["pos"] = [(-AXIS_LEN, 0.0), (AXIS_LEN, 0.0),
                   (0.0, -AXIS_LEN), (0.0, AXIS_LEN)]
    data["rgba"][0] = data["rgba"][1] = (122, 46, 46, 255)   # muted red X
    data["rgba"][2] = data["rgba"][3] = (41, 107, 46, 255)   # muted green Y
    return data


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
        self._view_stack: list[tuple[float, float, float]] = []
        self._zoom_window = False
        self._rubber: Optional[QRubberBand] = None
        self._rubber_origin = QPointF()
        self._overlay_scene: Optional[Scene] = None
        self._overlay_dirty = False
        self._overlay_bufs: dict[str, tuple] = {}
        # Interactive tool hook (ToolController): hover/click/preview/markers.
        self.tool_delegate = None

    # -- document hooks -------------------------------------------------------
    def set_scene(self, scene: Optional[Scene]) -> None:
        """Adopt a packed scene; the GL upload happens on the next frame."""
        self._scene = scene
        self._scene_dirty = True
        self.update()

    def set_overlay_scene(self, scene: Optional[Scene]) -> None:
        """Freshly drawn entities, rendered on top of the base scene."""
        self._overlay_scene = scene
        self._overlay_dirty = True
        self.update()

    # -- view stack (ZOOM Previous) -------------------------------------------
    def push_view(self) -> None:
        self._view_stack.append((self.view.cx, self.view.cy, self.view.scale))
        del self._view_stack[:-32]  # bounded, AutoCAD-style

    def zoom_previous(self) -> bool:
        if not self._view_stack:
            return False
        self.view.cx, self.view.cy, self.view.scale = self._view_stack.pop()
        self.update()
        return True

    def start_zoom_window(self) -> None:
        """Next left-drag on the canvas picks the zoom window."""
        self._zoom_window = True
        self.setCursor(Qt.CrossCursor)

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
        self.push_view()
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
        """Upload standard-format vertices (12 B: pos f32x2 + rgba u8x4)."""
        loc_pos = self._program.attributeLocation("a_pos")
        loc_color = self._program.attributeLocation("a_color")
        vao = QOpenGLVertexArrayObject(self)
        vao.create()
        vao.bind()
        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.create()
        vbo.bind()
        raw = data.tobytes()
        vbo.allocate(raw, len(raw))
        stride = VERTEX_DTYPE.itemsize
        self._program.bind()
        self._program.enableAttributeArray(loc_pos)
        self._program.setAttributeBuffer(loc_pos, GL_FLOAT, 0, 2, stride)
        self._program.enableAttributeArray(loc_color)
        # Qt normalizes integer attribute types: u8 255 -> 1.0 in the vec4.
        self._program.setAttributeBuffer(loc_color, GL_UNSIGNED_BYTE, 8, 4, stride)
        self._program.release()
        vao.release()
        vbo.release()
        return vao, vbo, len(data)

    def _make_thick_vao(self, data: np.ndarray) -> tuple:
        """Upload thick-format vertices (20 B: pos + normal f32x2 + rgba u8x4)."""
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
        raw = data.tobytes()
        vbo.allocate(raw, len(raw))
        stride = THICK_DTYPE.itemsize
        prog.bind()
        prog.enableAttributeArray(loc_pos)
        prog.setAttributeBuffer(loc_pos, GL_FLOAT, 0, 2, stride)
        prog.enableAttributeArray(loc_normal)
        prog.setAttributeBuffer(loc_normal, GL_FLOAT, 8, 2, stride)
        prog.enableAttributeArray(loc_color)
        prog.setAttributeBuffer(loc_color, GL_UNSIGNED_BYTE, 16, 4, stride)
        prog.release()
        vao.release()
        vbo.release()
        return vao, vbo, len(data)

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
        if self._scene is not None and self._scene.background is not None:
            gl.glClearColor(*self._scene.background)
        else:
            gl.glClearColor(*BACKGROUND, 1.0)
        gl.glClear(GL_COLOR_BUFFER_BIT)

        if self._scene_dirty:
            self._upload_scene()
        if self._overlay_dirty:
            self._upload_overlay()

        self._program.bind()

        self._program.setUniformValue(self._loc_mvp, self._mvp())
        self._axes_vao.bind()
        gl.glDrawArrays(GL_LINES, 0, self._axes_count)
        self._axes_vao.release()

        if self._scene is not None and self._scene_bufs:
            scene_mvp = self._mvp(*self._scene.origin)
            view_rect = self._view_world_rect()
            self._program.setUniformValue(self._loc_mvp, scene_mvp)
            # Fills first, then lines and points on top of them.
            for name, mode in (("triangles", GL_TRIANGLES),
                               ("lines", GL_LINES),
                               ("points", GL_POINTS)):
                buf = self._scene_bufs.get(name)
                if buf is None:
                    continue
                vao, _vbo, _count = buf
                batch: Batch = getattr(self._scene, name)
                vao.bind()
                for first, count in batch.visible_runs(
                        view_rect, self.view.scale, MIN_TEXT_PX):
                    gl.glDrawArrays(mode, first, count)
                vao.release()
            self._program.release()
            self._draw_thick(gl, scene_mvp, view_rect)
        else:
            self._program.release()

        if self._overlay_scene is not None and self._overlay_bufs:
            self._program.bind()
            self._program.setUniformValue(
                self._loc_mvp, self._mvp(*self._overlay_scene.origin))
            for name, mode in (("triangles", GL_TRIANGLES),
                               ("lines", GL_LINES),
                               ("points", GL_POINTS)):
                buf = self._overlay_bufs.get(name)
                if buf is None:
                    continue
                vao, _vbo, count = buf
                vao.bind()
                gl.glDrawArrays(mode, 0, count)
                vao.release()
            self._program.release()

        self._paint_overlay()

    def _upload_overlay(self) -> None:
        for vao, vbo, _count in self._overlay_bufs.values():
            vbo.destroy()
            vao.destroy()
        self._overlay_bufs.clear()
        self._overlay_dirty = False
        if self._overlay_scene is None:
            return
        for name in ("triangles", "lines", "points"):
            batch: Batch = getattr(self._overlay_scene, name)
            if batch.vertex_count:
                self._overlay_bufs[name] = self._make_vao(batch.data)
        # Note: thick-lineweight quads in the overlay are not drawn yet —
        # freshly drawn entities default to thin lines; the next full regen
        # merges them with correct weights.

    def _view_world_rect(self) -> tuple[float, float, float, float]:
        x0, y1 = self.view.screen_to_world(0.0, 0.0)          # top-left
        x1, y0 = self.view.screen_to_world(self.width(), self.height())
        return (x0, y0, x1, y1)

    def _draw_thick(self, gl, scene_mvp: QMatrix4x4, view_rect) -> None:
        """Thick lineweight quads: one draw per visible weight range."""
        buf = self._scene_bufs.get("thick")
        if buf is None:
            return
        vao, _vbo, _count = buf
        batch = self._scene.thick
        x0, y0, x1, y1 = view_rect
        prog = self._thick_program
        prog.bind()
        prog.setUniformValue(self._loc_thick_mvp, scene_mvp)
        vao.bind()
        # No run merging here: u_half_world changes per lineweight.
        for i, rng in enumerate(batch.ranges):
            if batch.bounds is not None:
                bx0, by0, bx1, by1 = batch.bounds[i]
                if bx0 > x1 or bx1 < x0 or by0 > y1 or by1 < y0:
                    continue
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
        if self.tool_delegate is not None and self.tool_delegate.active():
            self._draw_tool_preview(p)
        if self._cursor is not None and not self._panning:
            self._draw_crosshair(p, self._cursor)
        p.end()

    # AutoSnap marker glyphs (classic yellow), drawn in logical pixels.
    MARKER_COLOR = QColor(255, 220, 0)
    MARKER_SIZE = 10

    def _draw_tool_preview(self, p: QPainter) -> None:
        delegate = self.tool_delegate
        preview_color = (QColor(90, 90, 90) if self._light_background()
                        else QColor(200, 200, 200))
        pen = QPen(preview_color, 1, Qt.DashLine)
        p.setPen(pen)
        for (ax, ay), (bx, by) in delegate.preview_segments():
            x1, y1 = self.view.world_to_screen(ax, ay)
            x2, y2 = self.view.world_to_screen(bx, by)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        hit = delegate.snap_hit
        if hit is not None:
            sx, sy = self.view.world_to_screen(hit.x, hit.y)
            self._draw_snap_marker(p, hit.kind, sx, sy)

    def _draw_snap_marker(self, p: QPainter, kind: str, x: float, y: float) -> None:
        s = self.MARKER_SIZE / 2.0
        p.setPen(QPen(self.MARKER_COLOR, 2))
        if kind == "END":       # square
            p.drawRect(x - s, y - s, 2 * s, 2 * s)
        elif kind == "MID":     # triangle
            p.drawPolygon([QPointF(x, y - s), QPointF(x - s, y + s),
                           QPointF(x + s, y + s)])
        elif kind == "CEN":     # circle
            p.drawEllipse(QPointF(x, y), s, s)
        elif kind == "NOD":     # circle with X
            p.drawEllipse(QPointF(x, y), s, s)
            p.drawLine(QPointF(x - s, y - s), QPointF(x + s, y + s))
            p.drawLine(QPointF(x - s, y + s), QPointF(x + s, y - s))
        elif kind == "INT":     # X
            p.drawLine(QPointF(x - s, y - s), QPointF(x + s, y + s))
            p.drawLine(QPointF(x - s, y + s), QPointF(x + s, y - s))
        elif kind == "PER":     # right-angle symbol
            p.drawLine(QPointF(x - s, y - s), QPointF(x - s, y + s))
            p.drawLine(QPointF(x - s, y + s), QPointF(x + s, y + s))
            p.drawLine(QPointF(x - s, y), QPointF(x, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + s))
        else:                   # NEA: bowtie
            p.drawPolygon([QPointF(x - s, y - s), QPointF(x + s, y + s),
                           QPointF(x + s, y - s), QPointF(x - s, y + s)])

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

    def _light_background(self) -> bool:
        if self._scene is None or self._scene.background is None:
            return False
        r, g, b, _a = self._scene.background
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 0.5

    def _draw_crosshair(self, p: QPainter, pos: QPointF) -> None:
        color = CROSSHAIR_COLOR_LIGHT if self._light_background() else CROSSHAIR_COLOR
        p.setPen(QPen(color, 1))
        x, y = pos.x(), pos.y()
        half = PICKBOX_PX / 2
        # Full-viewport crosshair with the pick box gap-free on top (classic).
        p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.drawRect(x - half, y - half, PICKBOX_PX, PICKBOX_PX)

    # -- input -----------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if self._zoom_window and event.button() == Qt.LeftButton:
            self._rubber_origin = event.position()
            if self._rubber is None:
                self._rubber = QRubberBand(QRubberBand.Rectangle, self)
            self._rubber.setGeometry(int(self._rubber_origin.x()),
                                     int(self._rubber_origin.y()), 0, 0)
            self._rubber.show()
            return
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._last_pos = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            self.update()
            return
        if (event.button() == Qt.LeftButton and self.tool_delegate is not None
                and self.tool_delegate.active()):
            pos = event.position()
            wx, wy = self.view.screen_to_world(pos.x(), pos.y())
            self.tool_delegate.on_click(wx, wy)
            self.update()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._zoom_window and event.button() == Qt.LeftButton:
            self._zoom_window = False
            self.setCursor(Qt.BlankCursor)
            if self._rubber is not None:
                self._rubber.hide()
            pos = event.position()
            x0, y0 = self._rubber_origin.x(), self._rubber_origin.y()
            if abs(pos.x() - x0) > 4 and abs(pos.y() - y0) > 4:
                wx0, wy0 = self.view.screen_to_world(x0, y0)
                wx1, wy1 = self.view.screen_to_world(pos.x(), pos.y())
                self.push_view()
                self.view.zoom_extents(min(wx0, wx1), min(wy0, wy1),
                                       max(wx0, wx1), max(wy0, wy1), margin=0.0)
            self.update()
            return
        if event.button() == Qt.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.BlankCursor)
            self.update()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._zoom_window and self._rubber is not None and self._rubber.isVisible():
            x0, y0 = self._rubber_origin.x(), self._rubber_origin.y()
            self._rubber.setGeometry(int(min(x0, pos.x())), int(min(y0, pos.y())),
                                     int(abs(pos.x() - x0)), int(abs(pos.y() - y0)))
            return
        if self._panning:
            delta = pos - self._last_pos
            self._last_pos = pos
            self.view.pan_pixels(delta.x(), delta.y())
        else:
            self._cursor = pos
            wx, wy = self.view.screen_to_world(pos.x(), pos.y())
            if self.tool_delegate is not None and self.tool_delegate.active():
                from views.tool_controller import SNAP_PX

                self.tool_delegate.on_hover(wx, wy, SNAP_PX / self.view.scale)
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
