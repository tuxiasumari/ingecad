# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""IngeCAD core: document model, headless actions, commands, i18n.

Nothing in this package may import Qt widgets or GL. The document lives here
(as an ezdxf drawing), and every edit is a headless action wrapped in a
Command — the invariant that keeps the engine scriptable and testable.
"""
