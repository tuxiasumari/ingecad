# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Custom dark title bar.

On GNOME/Wayland the Qt decoration plugins color the title bar from the
*system* color scheme (org.freedesktop.appearance), so a light desktop theme
forces a light bar no matter what the app palette says. The only way to keep
the chrome dark everywhere is to own the bar: frameless window + this widget.
Dragging and snapping stay native through QWindow.startSystemMove().
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

HEIGHT = 34

_STYLE = """
TitleBar {
    background: #201f24;
}
TitleBar QLabel {
    color: #c8c8c8;
    font-weight: bold;
}
TitleBar QToolButton {
    border: none;
    background: transparent;
    color: #c8c8c8;
    font-size: 13px;
    padding: 0px;
}
TitleBar QToolButton:hover {
    background: #3a3940;
}
TitleBar QToolButton#close:hover {
    background: #b0343c;
    color: #ffffff;
}
"""


class TitleBar(QWidget):
    """Title text centered, window buttons on the right, native drag."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window
        self.setFixedHeight(HEIGHT)
        self.setStyleSheet(_STYLE)
        self.setAutoFillBackground(True)

        self._title = QLabel(window.windowTitle(), self)
        self._title.setAlignment(Qt.AlignCenter)
        window.windowTitleChanged.connect(self._title.setText)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(2)
        # Left spacer mirrors the buttons' width so the title stays centered.
        self._left_pad = QWidget(self)
        layout.addWidget(self._left_pad)
        layout.addWidget(self._title, 1)
        self._btn_min = self._button("–", window.showMinimized)
        self._btn_max = self._button("□", self._toggle_maximized)
        self._btn_close = self._button("✕", window.close, object_name="close")
        for btn in (self._btn_min, self._btn_max, self._btn_close):
            layout.addWidget(btn)
        self._left_pad.setFixedWidth(3 * 34 + 2 * 2)

    def _button(self, glyph: str, slot, object_name: str = "") -> QToolButton:
        btn = QToolButton(self)
        btn.setText(glyph)
        btn.setFixedSize(QSize(34, HEIGHT - 6))
        btn.setFocusPolicy(Qt.NoFocus)
        if object_name:
            btn.setObjectName(object_name)
        btn.clicked.connect(slot)
        return btn

    def _toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    # -- native move ----------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._toggle_maximized()
            return
        super().mouseDoubleClickEvent(event)
