# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""IngeCAD main window — classic pre-ribbon layout.

Menu bar + (from Phase 3) dockable toolbars, command line at the bottom, and a
status bar with coordinate readout and mode toggles. The ribbon does not exist
and will never exist here.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow

from core.i18n import tr
from core.version import __version__
from views.viewport import Viewport


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"IngeCAD — {tr('Untitled')}")
        self.resize(1280, 800)

        self.viewport = Viewport(self)
        self.setCentralWidget(self.viewport)

        self._build_menus()
        self._build_status_bar()
        self.viewport.cursorMoved.connect(self._on_cursor_moved)

    # -- chrome ---------------------------------------------------------------
    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu(tr("File"))
        quit_act = QAction(tr("Quit"), self)
        quit_act.setShortcut(QKeySequence.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        view_menu = self.menuBar().addMenu(tr("View"))
        extents_act = QAction(tr("Zoom Extents"), self)
        extents_act.triggered.connect(self.viewport.zoom_extents)
        view_menu.addAction(extents_act)

    def _build_status_bar(self) -> None:
        # Coordinate readout, bottom-left — the classic AutoCAD tracker.
        self._coords_label = QLabel("0.0000, 0.0000")
        self._coords_label.setMinimumWidth(220)
        self.statusBar().addWidget(self._coords_label)
        self.statusBar().addPermanentWidget(QLabel(f"IngeCAD {__version__}"))

    def _on_cursor_moved(self, wx: float, wy: float) -> None:
        self._coords_label.setText(f"{wx:.4f}, {wy:.4f}")

    # -- documents (entry point wired now; import lands in Phase 1/2) ---------
    def open_path(self, path: Path) -> None:
        """OS file associations and argv[1] land here."""
        self.statusBar().showMessage(
            tr("Cannot open {name} yet — file import lands in Phase 1", name=path.name),
            5000,
        )
