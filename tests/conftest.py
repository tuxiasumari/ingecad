# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Shared fixtures: headless Qt for widget tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Widget tests run without a display, in CI and locally alike.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Tests import project packages from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
