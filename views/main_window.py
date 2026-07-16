# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""IngeCAD main window — classic pre-ribbon layout.

Menu bar + (from Phase 3) dockable toolbars, command line at the bottom, and a
status bar with coordinate readout and mode toggles. The ribbon does not exist
and will never exist here.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QMessageBox

from core import i18n
from core.document import Document, DocumentError
from core.i18n import tr
from core.version import __version__
from views.viewport import Viewport


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.document: Document | None = None
        self.setWindowTitle(f"IngeCAD — {tr('Untitled')}")
        self.resize(1280, 800)

        self.viewport = Viewport(self)
        self.setCentralWidget(self.viewport)

        self._build_menus()
        self._build_status_bar()
        self.viewport.cursorMoved.connect(self._on_cursor_moved)

    # -- chrome ---------------------------------------------------------------
    def _build_menus(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.clear()

        file_menu = menu_bar.addMenu(tr("File"))
        open_act = QAction(tr("Open..."), self)
        open_act.setShortcut(QKeySequence.Open)
        open_act.triggered.connect(self._open_dialog)
        file_menu.addAction(open_act)
        file_menu.addSeparator()
        quit_act = QAction(tr("Quit"), self)
        quit_act.setShortcut(QKeySequence.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        view_menu = menu_bar.addMenu(tr("View"))
        extents_act = QAction(tr("Zoom Extents"), self)
        extents_act.triggered.connect(self.viewport.zoom_extents)
        view_menu.addAction(extents_act)

        tools_menu = menu_bar.addMenu(tr("Tools"))
        lang_menu = tools_menu.addMenu(tr("Language"))
        lang_group = QActionGroup(self)
        # Each language is listed in its own name — recognizable no matter
        # which language is currently active.
        for code, native_name in (("en", "English"), ("es", "Español")):
            act = QAction(native_name, self)
            act.setCheckable(True)
            act.setChecked(i18n.current_language() == code)
            act.triggered.connect(lambda _=False, c=code: self._set_language(c))
            lang_group.addAction(act)
            lang_menu.addAction(act)

    def _set_language(self, code: str) -> None:
        """Switch the UI language, persist it, and retranslate live."""
        if code == i18n.current_language():
            return
        QSettings().setValue("language", code)
        i18n.set_language(code)
        self._retranslate()

    def _retranslate(self) -> None:
        name = self.document.name if self.document else tr("Untitled")
        self.setWindowTitle(f"IngeCAD — {name}")
        self._build_menus()

    def _build_status_bar(self) -> None:
        # Coordinate readout, bottom-left — the classic AutoCAD tracker.
        self._coords_label = QLabel("0.0000, 0.0000")
        self._coords_label.setMinimumWidth(220)
        self.statusBar().addWidget(self._coords_label)
        self.statusBar().addPermanentWidget(QLabel(f"IngeCAD {__version__}"))

    def _on_cursor_moved(self, wx: float, wy: float) -> None:
        self._coords_label.setText(f"{wx:.4f}, {wy:.4f}")

    # -- documents -------------------------------------------------------------
    def _open_dialog(self) -> None:
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            tr("Open Drawing"),
            "",
            tr("DXF drawings (*.dxf);;All files (*)"),
        )
        if filename:
            self.open_path(Path(filename))

    def open_path(self, path: Path) -> None:
        """OS file associations, argv[1], and File > Open land here."""
        if path.suffix.lower() == ".dwg":
            # LibreDWG bridge lands in Phase 2.
            self.statusBar().showMessage(
                tr("DWG opens land in Phase 2 — convert to DXF with dwg2dxf for now"),
                8000,
            )
            return
        from render.backend import build_scene

        try:
            document = Document.load(path)
            scene = build_scene(document)
        except DocumentError as exc:
            QMessageBox.warning(
                self,
                tr("Open Drawing"),
                tr("Cannot open {name}: {error}", name=path.name, error=str(exc)),
            )
            return
        self.document = document
        self.viewport.set_scene(scene)
        self.viewport.zoom_extents()
        self.setWindowTitle(f"IngeCAD — {document.name}")
        self.statusBar().showMessage(tr("Opened {name}", name=document.name), 5000)
