"""IngeCAD entry point.

Free 2D CAD for Linux in the spirit of classic AutoCAD. Part of the Inge
ecosystem (IngeTrazo 3D modeling, IngePresupuestos budgeting).

Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
Licensed under GPL-3.0-or-later. See LICENSE.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QPalette, QSurfaceFormat
from PySide6.QtWidgets import QApplication

from core import i18n


def _configure_surface_format() -> None:
    """Request an OpenGL 3.3 Core context (matches the GLSL 330 shaders).

    No depth buffer request: the canvas is 2D and draws back-to-front. MSAA
    stays off the widget surface (IngeTrazo lesson: multisampled surfaces
    interleave stale frames on Wayland); AA arrives later in an offscreen FBO
    if line quality asks for it.
    """
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    QSurfaceFormat.setDefaultFormat(fmt)


def _apply_dark_theme(app: QApplication) -> None:
    """Force dark UI chrome regardless of the desktop theme.

    Model space is dark by design; light menus and title bar clash with it.
    ``setColorScheme`` drives the platform pieces (Wayland client-side title
    bar, native menus); the Fusion style + palette cover every widget so the
    look does not depend on whatever desktop theme is installed.
    """
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    app.setStyle("Fusion")

    window = QColor(45, 45, 48)
    base = QColor(37, 37, 40)
    text = QColor(224, 224, 224)
    disabled = QColor(128, 128, 128)
    highlight = QColor(42, 93, 143)

    p = QPalette()
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, window)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.PlaceholderText, disabled)
    p.setColor(QPalette.Button, window)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor(255, 96, 96))
    p.setColor(QPalette.ToolTipBase, QColor(58, 58, 61))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Highlight, highlight)
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.Link, QColor(74, 163, 224))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.HighlightedText):
        p.setColor(QPalette.Disabled, role, disabled)
    app.setPalette(p)


def _init_language() -> None:
    """Load the saved UI language; English by default.

    Engineers learned AutoCAD in English — commands, menus, muscle memory —
    so English is the default regardless of the system locale. Spanish is a
    deliberate opt-in via Tools > Language.
    """
    i18n.set_language(str(QSettings().value("language", "en")))


def main() -> int:
    _configure_surface_format()
    app = QApplication(sys.argv)
    app.setApplicationName("IngeCAD")
    app.setOrganizationName("IngeCAD")
    # Wayland matches the running window to its .desktop entry by this name.
    app.setDesktopFileName("ingecad")
    _apply_dark_theme(app)
    _init_language()

    from views.main_window import MainWindow

    window = MainWindow()
    # A document passed on the command line (the OS file association's
    # double-click hands it as argv[1]) opens right away; otherwise start with
    # a blank drawing, like AutoCAD's Drawing1, so the panels and commands work
    # from the first click instead of waiting for File > New.
    opened = False
    if len(sys.argv) > 1:
        doc = Path(sys.argv[1])
        if doc.suffix.lower() in (".dxf", ".dwg") and doc.exists():
            window.open_path(doc)
            opened = True
    if not opened:
        window.new_document()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
