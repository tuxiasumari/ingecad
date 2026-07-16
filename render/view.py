# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""2D view transform: world (drawing units, float64) ↔ screen (logical px).

Pure Python on purpose — no Qt, no GL — so the pan/zoom math is unit-testable
headless. The viewport owns one instance, feeds it mouse events, and derives
its GL matrix from it each frame.

Conventions:
- World: X east, Y north (Y grows up), float64 (Python floats) throughout.
- Screen: Qt logical pixels, origin top-left, Y grows down.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ViewTransform2D:
    """Orthographic top view defined by a world center and a pixel scale."""

    cx: float = 0.0          # world X at the viewport center
    cy: float = 0.0          # world Y at the viewport center
    scale: float = 10.0      # pixels per drawing unit
    width: int = 800         # viewport size, logical px
    height: int = 600

    MIN_SCALE = 1e-9         # zoom guards: keep the transform invertible
    MAX_SCALE = 1e12

    # -- mapping ------------------------------------------------------------
    def world_to_screen(self, wx: float, wy: float) -> tuple[float, float]:
        sx = (wx - self.cx) * self.scale + self.width / 2.0
        sy = self.height / 2.0 - (wy - self.cy) * self.scale
        return sx, sy

    def screen_to_world(self, sx: float, sy: float) -> tuple[float, float]:
        wx = self.cx + (sx - self.width / 2.0) / self.scale
        wy = self.cy + (self.height / 2.0 - sy) / self.scale
        return wx, wy

    # -- interaction --------------------------------------------------------
    def pan_pixels(self, dx: float, dy: float) -> None:
        """Drag the *content* by (dx, dy) screen pixels (grab-and-move feel)."""
        self.cx -= dx / self.scale
        self.cy += dy / self.scale

    def zoom_at(self, sx: float, sy: float, factor: float) -> None:
        """Scale by ``factor`` keeping the world point under (sx, sy) fixed."""
        new_scale = min(max(self.scale * factor, self.MIN_SCALE), self.MAX_SCALE)
        if new_scale == self.scale:
            return
        wx, wy = self.screen_to_world(sx, sy)
        self.scale = new_scale
        # Re-center so (wx, wy) projects back to the same screen position.
        self.cx = wx - (sx - self.width / 2.0) / self.scale
        self.cy = wy + (sy - self.height / 2.0) / self.scale

    def zoom_extents(
        self,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
        margin: float = 0.05,
    ) -> None:
        """Fit the world rectangle into the viewport with a relative margin."""
        w = max(max_x - min_x, 1e-12)
        h = max(max_y - min_y, 1e-12)
        self.cx = (min_x + max_x) / 2.0
        self.cy = (min_y + max_y) / 2.0
        fit = min(self.width / w, self.height / h) * (1.0 - margin)
        self.scale = min(max(fit, self.MIN_SCALE), self.MAX_SCALE)

    # -- GL -----------------------------------------------------------------
    def ndc_factors(self) -> tuple[float, float, float, float]:
        """Coefficients mapping world → NDC: ``ndc = (w - center) * k``.

        Returns ``(kx, ky, cx, cy)`` with the center kept float64 — the caller
        builds its GL matrix (or subtracts the origin before float32 upload)
        from these, so precision is lost only at the last step.
        """
        kx = 2.0 * self.scale / max(self.width, 1)
        ky = 2.0 * self.scale / max(self.height, 1)
        return kx, ky, self.cx, self.cy
