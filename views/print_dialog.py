# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""PLOT dialog — paper, orientation, area, scale; PDF or system printer.

Kept to what a civil plan needs: pick the paper, plot the extents or the
current view, at Fit or a real 1:N metric scale (drawing unit metres or
millimetres), then save a vector PDF or send to a printer.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QPushButton,
)

from core.i18n import tr
from formats import pdf_out


class PrintDialog(QDialog):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle(tr("Plot"))
        self.setMinimumWidth(320)
        form = QFormLayout(self)

        self.paper = QComboBox(self)
        self.paper.addItems(list(pdf_out.PAPER_SIZES_MM))
        self.orientation = QComboBox(self)
        self.orientation.addItem(tr("Landscape"), True)
        self.orientation.addItem(tr("Portrait"), False)
        self.area = QComboBox(self)
        self.area.addItem(tr("Extents"), "extents")
        self.area.addItem(tr("Current view"), "view")
        self.scale = QComboBox(self)
        self.scale.addItem(tr("Fit to paper"), None)
        for n in pdf_out.COMMON_SCALES:
            self.scale.addItem(f"1:{n}", n)
        self.units = QComboBox(self)
        self.units.addItem(tr("Meters"), 1000.0)       # 1 unit = 1000 mm
        self.units.addItem(tr("Millimeters"), 1.0)

        form.addRow(tr("Paper size"), self.paper)
        form.addRow(tr("Orientation"), self.orientation)
        form.addRow(tr("Plot area"), self.area)
        form.addRow(tr("Scale"), self.scale)
        form.addRow(tr("Drawing unit"), self.units)

        buttons = QDialogButtonBox(self)
        pdf_btn = QPushButton(tr("Save PDF..."), self)
        printer_btn = QPushButton(tr("Print..."), self)
        buttons.addButton(pdf_btn, QDialogButtonBox.AcceptRole)
        buttons.addButton(printer_btn, QDialogButtonBox.ActionRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.reject)
        pdf_btn.clicked.connect(self._to_pdf)
        printer_btn.clicked.connect(self._to_printer)
        form.addRow(buttons)

    # -- plot parameters -------------------------------------------------------
    def _mm_per_unit(self):
        n = self.scale.currentData()
        if n is None:
            return None                         # fit
        return self.units.currentData() / n     # 1:N metric

    def _area_rect(self):
        if self.area.currentData() == "view":
            return self.window.viewport._view_world_rect()
        return None                             # extents

    def _plot_on(self, printer) -> None:
        pdf_out.plot(
            self.window.document, printer,
            layout_name=getattr(self.window, "_active_layout", None),
            area=self._area_rect(),
            mm_per_unit=self._mm_per_unit())

    # -- outputs ---------------------------------------------------------------
    def _to_pdf(self) -> None:
        name = self.window.document.name if self.window.document else "plano"
        path, _f = QFileDialog.getSaveFileName(
            self, tr("Save PDF"), f"{name}.pdf", "PDF (*.pdf)")
        if not path:
            return
        printer = pdf_out.make_pdf_printer(
            path, self.paper.currentText(),
            landscape=self.orientation.currentData())
        self._plot_on(printer)
        self.window.statusBar().showMessage(
            tr("PDF saved: {p}", p=path), 5000)
        self.accept()

    def _to_printer(self) -> None:
        from PySide6.QtGui import QPageLayout, QPageSize
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.HighResolution)
        size_id = getattr(QPageSize, self.paper.currentText(), QPageSize.A4)
        printer.setPageSize(QPageSize(size_id))
        printer.setPageOrientation(
            QPageLayout.Landscape if self.orientation.currentData()
            else QPageLayout.Portrait)
        dlg = QPrintDialog(printer, self)
        if dlg.exec():
            self._plot_on(printer)
            self.accept()
