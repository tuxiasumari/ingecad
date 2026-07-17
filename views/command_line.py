# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Classic AutoCAD command window: history above, prompt below.

Muscle-memory semantics live here: Space and Enter both execute, Enter on
an empty prompt repeats the last command (the dispatcher handles that),
Esc cancels, Up/Down walk the input history. Autocompletion offers command
names and aliases.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QCompleter,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr

_STYLE = """
CommandLine QPlainTextEdit, CommandLine QLineEdit {
    background: #1e1e22;
    color: #d8d8d8;
    border: 1px solid #3a3940;
    font-family: monospace;
}
CommandLine QLineEdit {
    padding: 3px 6px;
}
"""


class _PromptEdit(QLineEdit):
    """Input line with AutoCAD keys: Space executes, Esc cancels."""

    submitted = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._history: list[str] = []
        self._cursor = 0

    def _typed_text(self) -> str:
        """The text the user actually typed, without the inline suggestion.

        Muscle memory rules: "l" must run LINE (via the alias), not whatever
        completion happens to be highlighted. The inline suggestion sits as
        a trailing selection; strip it on execute (Tab/Right accept it).
        """
        text = self.text()
        if self.hasSelectedText():
            start = self.selectionStart()
            if start + len(self.selectedText()) == len(text):
                return text[:start]
        return text

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter) or (
            key == Qt.Key_Space and not self.text().endswith(" ")
            and self._space_executes()
        ):
            text = self._typed_text()
            if text.strip():
                self._history.append(text.strip())
            self._cursor = len(self._history)
            self.clear()
            self.submitted.emit(text)
            return
        if key == Qt.Key_Escape:
            self.clear()
            self.cancelled.emit()
            return
        if key == Qt.Key_Up and self._history:
            self._cursor = max(0, self._cursor - 1)
            self.setText(self._history[self._cursor])
            return
        if key == Qt.Key_Down and self._history:
            self._cursor = min(len(self._history), self._cursor + 1)
            self.setText(self._history[self._cursor]
                         if self._cursor < len(self._history) else "")
            return
        super().keyPressEvent(event)

    def _space_executes(self) -> bool:
        # Space acts as Enter unless the line already carries an argument
        # with spaces (file names etc. arrive in later phases; command
        # tokens never contain spaces).
        return True


class CommandLine(QWidget):
    """History + prompt, docked at the bottom of the main window."""

    submitted = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CommandLine")
        self.setStyleSheet(_STYLE)

        self.history = QPlainTextEdit(self)
        self.history.setReadOnly(True)
        self.history.setMaximumBlockCount(500)
        self.history.setFixedHeight(72)
        self.history.setFocusPolicy(Qt.NoFocus)

        self.input = _PromptEdit(self)
        self.input.setPlaceholderText(tr("Type a command"))
        self.input.submitted.connect(self.submitted)
        self.input.cancelled.connect(self.cancelled)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.addWidget(self.history)
        layout.addWidget(self.input)

    def set_completions(self, names: list[str]) -> None:
        completer = QCompleter(names, self.input)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        # Inline, not popup: a popup steals the first Enter (and can hijack
        # "l" into "LA"), breaking the type-alias-hit-Enter muscle memory.
        completer.setCompletionMode(QCompleter.InlineCompletion)
        self.input.setCompleter(completer)

    def echo(self, text: str) -> None:
        self.history.appendPlainText(text)
        scrollbar = self.history.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def echo_input(self, text: str) -> None:
        prompt = text.strip() or ""
        self.echo(f"{tr('Command')}: {prompt}")

    def type_ahead(self, event: QKeyEvent) -> None:
        """Forward a keystroke typed over the viewport (AutoCAD feel)."""
        self.input.setFocus()
        self.input.keyPressEvent(event)
