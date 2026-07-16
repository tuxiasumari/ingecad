"""IngeCAD entry point.

Free 2D CAD for Linux in the spirit of classic AutoCAD. Part of the Inge
ecosystem (IngeTrazo 3D modeling, IngePresupuestos budgeting).

Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
Licensed under GPL-3.0-or-later. See LICENSE.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QLocale, QSettings
from PySide6.QtGui import QSurfaceFormat
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


def _init_language() -> None:
    """Load the saved UI language, or default to the system locale."""
    saved = QSettings().value("language")
    if not saved:
        saved = "es" if QLocale.system().language() == QLocale.Spanish else "en"
    i18n.set_language(str(saved))


def main() -> int:
    _configure_surface_format()
    app = QApplication(sys.argv)
    app.setApplicationName("IngeCAD")
    app.setOrganizationName("IngeCAD")
    # Wayland matches the running window to its .desktop entry by this name.
    app.setDesktopFileName("ingecad")
    _init_language()

    from views.main_window import MainWindow

    window = MainWindow()
    # A document passed on the command line (the OS file association's
    # double-click hands it as argv[1]) opens right away.
    if len(sys.argv) > 1:
        doc = Path(sys.argv[1])
        if doc.suffix.lower() in (".dxf", ".dwg") and doc.exists():
            window.open_path(doc)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
