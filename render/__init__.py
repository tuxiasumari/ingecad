# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""IngeCAD render layer: view math and (from Phase 1) the GL drawing backend.

Model coordinates are float64 end to end; float32 appears only inside vertex
buffers, always relative to a float64 batch/view origin so real-world UTM
coordinates (~500 km) never lose precision on screen.
"""
