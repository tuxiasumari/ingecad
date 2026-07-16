# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""IngeCAD file bridges: DWG satellites (LibreDWG / ODA) and PDF output.

DWG is never parsed in-process — external converters produce DXF and ezdxf
does the rest (see CLAUDE.md, principle 2).
"""
