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
from PySide6.QtGui import (
    QColor,
    QMatrix4x4,
    QOpenGLFunctions,
    QPainter,
    QPen,
    QPolygonF,
)
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

GRIP_PICK_PX = 7.0  # grip hit aperture, logical pixels
SNAP_PX_HOVER = 12.0  # osnap aperture while a hot grip follows the cursor
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
        # Ghost preview (MOVE/COPY/PASTE drag): tessellated ONCE, then only a
        # per-frame translation in the MVP — no rebuild while the mouse moves.
        self._ghost_scene: Optional[Scene] = None
        self._ghost_dirty = False
        self._ghost_bufs: dict[str, tuple] = {}
        self._ghost_offset = (0.0, 0.0)
        self._sel_press = None  # pending left press in selection mode
        self._grip_hover = None  # grip under the cursor, if any
        self._pan_mode = False   # interactive PAN command (open-hand cursor)
        # Interactive tool hook (ToolController): hover/click/preview/markers.
        self.tool_delegate = None

    # -- document hooks -------------------------------------------------------
    def set_scene(self, scene: Optional[Scene]) -> None:
        """Adopt a packed scene; the GL upload happens on the next frame."""
        self._scene = scene
        self._scene_dirty = True
        self.update()

    # -- interactive PAN command (open/closed hand, AutoCAD-style) ------------
    def start_pan_mode(self) -> None:
        self._pan_mode = True
        self._cursor = None            # hide the crosshair; show the hand
        self.setCursor(Qt.OpenHandCursor)
        self.update()

    def stop_pan_mode(self) -> None:
        if not self._pan_mode:
            return
        self._pan_mode = False
        self._panning = False
        self.setCursor(Qt.BlankCursor)
        self.update()

    def set_overlay_scene(self, scene: Optional[Scene]) -> None:
        """Freshly drawn entities, rendered on top of the base scene."""
        self._overlay_scene = scene
        self._overlay_dirty = True
        self.update()

    def set_ghost_scene(self, scene: Optional[Scene]) -> None:
        """The dragged geometry preview; drawn dimmed at the ghost offset."""
        self._ghost_scene = scene
        self._ghost_dirty = True
        self._ghost_offset = (0.0, 0.0)
        self.update()

    def set_ghost_offset(self, dx: float, dy: float) -> None:
        """Move the ghost: only the MVP translation changes — free per frame."""
        self._ghost_offset = (dx, dy)
        self.update()

    def hide_handles(self, handles) -> None:
        """Make edited entities vanish instantly (alpha 0), no regen.

        The next full regen rebuilds the base scene without them; until then
        this hides their vertices in the existing buffers — a few KB of GPU
        update instead of seconds of regen (surgical display).
        """
        if self._scene is None or not self._scene.handle_ranges:
            return
        touched = False
        for h in handles:
            for batch_name, first, count in self._scene.handle_ranges.get(h, ()):
                getattr(self._scene, batch_name).data["rgba"][
                    first:first + count, 3] = 0
                touched = True
        if touched:
            self._scene_dirty = True   # re-upload the mutated buffers
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
        if self._ghost_dirty:
            self._upload_ghost()

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

        if self._ghost_scene is not None and self._ghost_bufs:
            # The ghost translates by shifting the vertex origin in the MVP:
            # same buffers every frame, only this uniform changes.
            ox, oy = self._ghost_scene.origin
            dx, dy = self._ghost_offset
            self._program.bind()
            self._program.setUniformValue(self._loc_mvp,
                                          self._mvp(ox + dx, oy + dy))
            for name, mode in (("triangles", GL_TRIANGLES),
                               ("lines", GL_LINES),
                               ("points", GL_POINTS)):
                buf = self._ghost_bufs.get(name)
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

    def _upload_ghost(self) -> None:
        for vao, vbo, _count in self._ghost_bufs.values():
            vbo.destroy()
            vao.destroy()
        self._ghost_bufs.clear()
        self._ghost_dirty = False
        if self._ghost_scene is None:
            return
        for name in ("triangles", "lines", "points"):
            batch: Batch = getattr(self._ghost_scene, name)
            if batch.vertex_count:
                data = batch.data.copy()
                # dim so the ghost reads as a preview, not committed geometry
                data["rgba"][:, 3] = (data["rgba"][:, 3] * 0.55).astype("u1")
                self._ghost_bufs[name] = self._make_vao(data)

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
        if self.tool_delegate is not None:
            self._draw_selection(p)
            self._draw_grips(p)
            if self.tool_delegate.active():
                self._draw_tool_preview(p)
            self._draw_live_text(p)
        if self._cursor is not None and not self._panning:
            self._draw_crosshair(p, self._cursor)
        p.end()

    # AutoSnap marker glyphs (classic yellow), drawn in logical pixels.
    MARKER_COLOR = QColor(255, 220, 0)
    MARKER_SIZE = 10
    # Selection visuals (AutoCAD colors): dashed highlight, blue window,
    # green crossing.
    HIGHLIGHT_COLOR = QColor(60, 170, 255)
    WINDOW_FILL = QColor(70, 130, 255, 50)
    WINDOW_BORDER = QColor(90, 140, 255)
    CROSSING_FILL = QColor(80, 220, 110, 50)
    CROSSING_BORDER = QColor(90, 220, 120)

    GRIP_COLOR = QColor(0, 170, 90)          # classic AutoCAD grip blue-green
    GRIP_HOVER = QColor(255, 90, 90)
    GRIP_SIZE = 8

    def _draw_grips(self, p: QPainter) -> None:
        grips = self.tool_delegate.grip_points()
        if not grips:
            return
        s = self.GRIP_SIZE / 2.0
        hovered = self._grip_hover
        for x, y, role, h, i in grips:
            sx, sy = self.view.world_to_screen(x, y)
            is_hot = hovered is not None and hovered[3] == h and hovered[4] == i
            p.setPen(QPen(self.GRIP_HOVER if is_hot else self.GRIP_COLOR, 1))
            p.setBrush(self.GRIP_HOVER if is_hot else self.GRIP_COLOR)
            if role == "mid":                          # triangle: add/stretch
                p.drawPolygon([QPointF(sx, sy - s), QPointF(sx - s, sy + s),
                               QPointF(sx + s, sy + s)])
            elif role == "center":
                p.drawEllipse(QPointF(sx, sy), s, s)   # round: move whole
            else:
                p.drawRect(sx - s, sy - s, 2 * s, 2 * s)  # square: vertices/ends
        p.setBrush(Qt.NoBrush)

    def _draw_live_text(self, p: QPainter) -> None:
        info = self.tool_delegate.live_text()
        if info is None:
            return
        from PySide6.QtGui import QFont

        pos, buffer, height, rotation = info
        sx, sy = self.view.world_to_screen(pos[0], pos[1])
        px = max(6.0, height * self.view.scale)   # world height -> pixels
        p.save()
        p.translate(sx, sy)
        p.rotate(-rotation)                        # world CCW -> screen
        font = QFont()
        font.setPixelSize(int(px))
        p.setFont(font)
        p.setPen(QPen(QColor(230, 230, 230)))
        text = buffer if buffer else ""
        fm = p.fontMetrics()
        p.drawText(QPointF(0, 0), text)            # baseline at the pick point
        caret_x = fm.horizontalAdvance(text)
        p.setPen(QPen(QColor(255, 200, 0), 1))     # blinking-less caret bar
        p.drawLine(QPointF(caret_x + 1, -px * 0.75), QPointF(caret_x + 1, px * 0.15))
        p.restore()

    def _draw_selection(self, p: QPainter) -> None:
        delegate = self.tool_delegate
        segs, circles, boxes = delegate.highlight_geometry()
        if len(segs) or len(circles) or len(boxes):
            p.setPen(QPen(self.HIGHLIGHT_COLOR, 2, Qt.DashLine))
            for s in segs[:4000]:
                x1, y1 = self.view.world_to_screen(s[0], s[1])
                x2, y2 = self.view.world_to_screen(s[2], s[3])
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            import math as _math

            for c in circles[:1000]:
                x, y = self.view.world_to_screen(c[0], c[1])
                r = c[2] * self.view.scale
                if len(c) >= 6 and c[3] != 0.0:
                    # highlight the ARC's real sweep, not its full circle
                    a0 = _math.degrees(c[4])
                    span = _math.degrees(c[5] - c[4])
                    p.drawArc(int(x - r), int(y - r), int(2 * r), int(2 * r),
                              int(a0 * 16), int(span * 16))
                else:
                    p.drawEllipse(QPointF(x, y), r, r)
            for b in boxes[:1000]:
                x1, y1 = self.view.world_to_screen(b[0], b[3])
                x2, y2 = self.view.world_to_screen(b[2], b[1])
                p.drawRect(x1, y1, x2 - x1, y2 - y1)
        rect_info = delegate.selection_rect()
        if rect_info is not None:
            (x0, y0, x1, y1), crossing = rect_info
            sx1, sy1 = self.view.world_to_screen(x0, y1)
            sx2, sy2 = self.view.world_to_screen(x1, y0)
            fill = self.CROSSING_FILL if crossing else self.WINDOW_FILL
            border = self.CROSSING_BORDER if crossing else self.WINDOW_BORDER
            p.fillRect(sx1, sy1, sx2 - sx1, sy2 - sy1, fill)
            p.setPen(QPen(border, 1, Qt.DashLine if crossing else Qt.SolidLine))
            p.drawRect(sx1, sy1, sx2 - sx1, sy2 - sy1)

    def _draw_tool_preview(self, p: QPainter) -> None:
        delegate = self.tool_delegate
        preview_color = (QColor(90, 90, 90) if self._light_background()
                        else QColor(200, 200, 200))
        pen = QPen(preview_color, 1, Qt.DashLine)
        p.setPen(pen)
        dim = delegate.preview_dimension()
        if dim is not None:
            self._draw_dim_preview(p, dim, preview_color)
        else:
            for (ax, ay), (bx, by) in delegate.preview_segments():
                x1, y1 = self.view.world_to_screen(ax, ay)
                x2, y2 = self.view.world_to_screen(bx, by)
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        hit = delegate.snap_hit
        if hit is not None:
            sx, sy = self.view.world_to_screen(hit.x, hit.y)
            self._draw_snap_marker(p, hit.kind, sx, sy)

    def _draw_dim_preview(self, p: QPainter, dim: dict, color: QColor) -> None:
        """A real-looking dimension preview: extension + dimension lines,
        arrowheads, and the live measurement — floats with the cursor."""
        import math

        s = self.view.world_to_screen
        p1 = QPointF(*s(*dim["p1"]))
        p2 = QPointF(*s(*dim["p2"]))
        d1 = QPointF(*s(*dim["d1"]))
        d2 = QPointF(*s(*dim["d2"]))
        solid = QPen(color, 1)
        thin = QPen(color, 1, Qt.DashLine)
        # extension lines (dashed), dimension line (solid)
        p.setPen(thin)
        p.drawLine(p1, d1)
        p.drawLine(p2, d2)
        p.setPen(solid)
        p.drawLine(d1, d2)
        # arrowheads pointing outward along the dimension line
        ang = math.atan2(d2.y() - d1.y(), d2.x() - d1.x())
        self._arrow_head(p, d1, ang, color)
        self._arrow_head(p, d2, ang + math.pi, color)
        # measurement text, upright, centred above the dimension line
        mid = QPointF((d1.x() + d2.x()) / 2, (d1.y() + d2.y()) / 2)
        p.save()
        p.setPen(QPen(color))
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(dim["text"])
        p.drawText(QPointF(mid.x() - w / 2, mid.y() - 4), dim["text"])
        p.restore()

    def _arrow_head(self, p: QPainter, tip: QPointF, angle: float,
                    color: QColor) -> None:
        import math
        size = 9.0
        a1 = angle + math.radians(20)     # base corners open inward
        a2 = angle - math.radians(20)
        poly = QPolygonF([
            tip,
            QPointF(tip.x() + size * math.cos(a1), tip.y() + size * math.sin(a1)),
            QPointF(tip.x() + size * math.cos(a2), tip.y() + size * math.sin(a2)),
        ])
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPolygon(poly)
        p.restore()

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
        if self._pan_mode:
            if event.button() == Qt.LeftButton:
                self._panning = True   # grab: closed hand, pan follows cursor
                self._last_pos = event.position()
                self.setCursor(Qt.ClosedHandCursor)
                return
            if event.button() == Qt.RightButton:
                self.stop_pan_mode()   # right-click ends PAN, like AutoCAD
                return
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
        if event.button() == Qt.LeftButton and self.tool_delegate is not None:
            pos = event.position()
            wx, wy = self.view.screen_to_world(pos.x(), pos.y())
            shift = bool(event.modifiers() & Qt.ShiftModifier)
            if self.tool_delegate.in_selection_mode():
                if self.tool_delegate._grip_drag is not None:
                    # a grip is already "hot": this click drops it here
                    # (snap-resolved, like the live follow)
                    tx, ty = self.tool_delegate.grip_target(wx, wy)
                    self.tool_delegate.finish_grip_drag(tx, ty)
                    self.update()
                    return
                grip = self.tool_delegate.grip_at(
                    wx, wy, GRIP_PICK_PX / self.view.scale)
                if grip is not None:
                    # click to grab; the point then follows the cursor freely
                    self.tool_delegate.begin_grip_drag(grip)
                    self.update()
                    return
            if self.tool_delegate.wants_drag_rect():
                # defer to release: a drag becomes a window, a click a pick
                self._sel_press = (pos, (wx, wy), shift)
                return
            self.tool_delegate.on_click(wx, wy, shift)
            self.update()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._pan_mode and event.button() == Qt.LeftButton:
            self._panning = False           # release: back to open hand
            self.setCursor(Qt.OpenHandCursor)
            return
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
        if (event.button() == Qt.LeftButton and self._sel_press is not None
                and self.tool_delegate is not None):
            press_pos, press_world, shift = self._sel_press
            self._sel_press = None
            pos = event.position()
            dragged = (abs(pos.x() - press_pos.x()) > 4
                       or abs(pos.y() - press_pos.y()) > 4)
            if dragged:
                # drag-window: anchor at press, complete at release
                self.tool_delegate.start_window(*press_world)
                wx, wy = self.view.screen_to_world(pos.x(), pos.y())
                self.tool_delegate.on_click(wx, wy, shift)
            else:
                self.tool_delegate.on_click(*press_world, shift)
            self.update()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._pan_mode:
            if self._panning:
                delta = pos - self._last_pos
                self._last_pos = pos
                self.view.pan_pixels(delta.x(), delta.y())
                self.update()
            return   # open hand otherwise: no crosshair, no hover
        if self._zoom_window and self._rubber is not None and self._rubber.isVisible():
            x0, y0 = self._rubber_origin.x(), self._rubber_origin.y()
            self._rubber.setGeometry(int(min(x0, pos.x())), int(min(y0, pos.y())),
                                     int(abs(pos.x() - x0)), int(abs(pos.y() - y0)))
            return
        if (self.tool_delegate is not None
                and self.tool_delegate._grip_drag is not None):
            # grip is hot: it follows the cursor with NO button held
            # (AutoCAD click-move-click), snapping like a drawing point
            self._cursor = pos
            wx, wy = self.view.screen_to_world(pos.x(), pos.y())
            self.tool_delegate.on_hover(wx, wy, SNAP_PX_HOVER / self.view.scale)
            self.tool_delegate.update_grip_drag(*self.tool_delegate.grip_target(wx, wy))
            self.cursorMoved.emit(wx, wy)
            self.update()
            return
        if self._panning:
            delta = pos - self._last_pos
            self._last_pos = pos
            self.view.pan_pixels(delta.x(), delta.y())
        else:
            self._cursor = pos
            wx, wy = self.view.screen_to_world(pos.x(), pos.y())
            if self.tool_delegate is not None:
                self._grip_hover = self.tool_delegate.grip_at(
                    wx, wy, GRIP_PICK_PX / self.view.scale)
                from views.tool_controller import SNAP_PX

                if (self._sel_press is not None
                        and (abs(pos.x() - self._sel_press[0].x()) > 4
                             or abs(pos.y() - self._sel_press[0].y()) > 4)):
                    # live drag-window rectangle while the button is held
                    self.tool_delegate.start_window(*self._sel_press[1])
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
