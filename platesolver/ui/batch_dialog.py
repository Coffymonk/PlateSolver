# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Tools › Batch solve folder."""
from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from platesolver.core.batch import BatchOptions, BatchSettings, find_images, run_batch, write_summary_csv
from platesolver.core.formatting import format_dec, format_ra
from platesolver.core.plugin import Cancelled, TaskContext
from platesolver.ui import theme

STATUS_COLORS = {"solved": theme.OK, "already solved": theme.ACCENT, "failed": theme.WARN, "skipped": theme.MUTED,
                 "error": theme.ERROR, "cancelled": theme.MUTED}


class BatchWorker(QThread):
    message = Signal(str)
    progress = Signal(int, int)
    item_done = Signal(int, object)
    finished_items = Signal(object, str)     # items, error text ('' if fine / 'cancelled')

    def __init__(self, pipeline, files, options, parent=None, confirm=None):
        super().__init__(parent)
        self.pipeline, self.files, self.options = pipeline, files, options
        self.ctx = TaskContext(log=self.message.emit, progress=self.progress.emit)
        if confirm is not None:
            self.ctx.confirm = confirm

    def run(self):
        items = []
        try:
            items = run_batch(self.pipeline, self.files, self.options, self.ctx,
                              on_item=lambda i, it: self.item_done.emit(i, it))
            self.finished_items.emit(items, "")
        except Cancelled:
            self.finished_items.emit(items, "cancelled")
        except Exception as exc:
            traceback.print_exc()
            self.finished_items.emit(items, str(exc))


class BatchDialog(QDialog):
    open_requested = Signal(str)

    COLS = ("File", "Status", "Solver", "Centre", "Scale", "Time", "Result / message")

    def __init__(self, pipeline, store, general, start_folder: str = "", parent=None, confirm=None):
        super().__init__(parent)
        self.confirm = confirm
        self.setWindowTitle("Batch solve folder")
        self.resize(1000, 620)
        self.pipeline = pipeline
        self.store = store
        self.general = general
        self.section = BatchSettings()
        self.settings = store.section(self.section)
        self.items = []
        self.worker: BatchWorker | None = None

        self.folder = QLineEdit(start_folder)
        self.folder.setPlaceholderText("Folder with images…")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        top = QHBoxLayout()
        top.addWidget(QLabel("Folder:"))
        top.addWidget(self.folder, 1)
        top.addWidget(browse)

        self.checks: dict[str, QCheckBox] = {}
        opts = QVBoxLayout()
        for f in self.section.settings_schema():
            cb = QCheckBox(f.label)
            cb.setChecked(bool(self.settings.get(f.key)))
            cb.setToolTip(f.help)
            self.checks[f.key] = cb
            opts.addWidget(cb)
        warn = QLabel(f"<span style='color:{theme.WARN}'>Writing into FITS files changes your originals "
                      f"(header only). Keep a backup if unsure.</span>")
        warn.setWordWrap(True)
        warn.setVisible(self.checks["write_fits"].isChecked())
        self.checks["write_fits"].toggled.connect(warn.setVisible)
        opts.addWidget(warn)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(len(self.COLS) - 1, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.status = QLabel("Choose a folder and press Start. Double-click a row afterwards to open that image.")
        self.status.setObjectName("muted")

        self.start_btn = QPushButton("Start")
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        self.save_btn = QPushButton("Save summary CSV…")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_summary)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.cancel_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(close)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(opts)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        lay.addLayout(buttons)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Folder with images", self.folder.text())
        if d:
            self.folder.setText(str(Path(d)))

    def _options(self) -> BatchOptions:
        o = BatchOptions(**{k: cb.isChecked() for k, cb in self.checks.items()})
        o.ly_decimals = int(self.general.get("ly_decimals"))
        o.csv_separator = str(self.store.get("export", "csv_separator", "comma"))
        return o

    def start(self):
        folder = Path(self.folder.text().strip())
        if not folder.is_dir():
            self.status.setText("That folder doesn't exist.")
            return
        for k, cb in self.checks.items():   # remember the choices for next time
            self.settings.set(k, cb.isChecked())
        self.store.save()
        exts = {e for l in self.pipeline.loaders() for e in l.extensions}
        files = find_images(folder, exts, self.checks["recursive"].isChecked())
        if not files:
            self.status.setText("No JPG, PNG, FITS or TIFF files in that folder.")
            return
        self.table.setRowCount(len(files))
        for r, f in enumerate(files):
            self._set_row(r, [str(f.relative_to(folder)), "waiting", "", "", "", "", ""])
        self.progress.setRange(0, len(files))
        self.progress.setValue(0)
        self.worker = BatchWorker(self.pipeline, files, self._options(), self, self.confirm)
        self.worker.message.connect(self.status.setText)
        self.worker.progress.connect(lambda d, t: self.progress.setValue(d))
        self.worker.item_done.connect(self._item_done)
        self.worker.finished_items.connect(self._finished)
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.save_btn.setEnabled(False)
        self.worker.start()

    def cancel(self):
        if self.worker:
            self.status.setText("Cancelling after the current image…")
            self.worker.ctx.cancel_event.set()

    def _set_row(self, r, values, color=None):
        for c, v in enumerate(values):
            it = QTableWidgetItem(v)
            if c == 1 and color:
                it.setForeground(QColor(color))
            self.table.setItem(r, c, it)

    def _item_done(self, r, it):
        centre = f"{format_ra(it.ra_deg)}  {format_dec(it.dec_deg)}" if it.ra_deg is not None else ""
        scale = f"{it.scale:.2f}″/px" if it.scale else ""
        result = ", ".join(it.outputs) if it.outputs else it.message
        if it.objects is not None:
            result = f"{it.objects} objects · " + result
        folder = Path(self.folder.text().strip())
        try:
            name = str(it.path.relative_to(folder))
        except ValueError:
            name = it.path.name
        self._set_row(r, [name, it.status, it.solver, centre, scale, f"{it.seconds:.1f} s", result],
                      STATUS_COLORS.get(it.status))
        self.table.item(r, 0).setData(Qt.UserRole, str(it.path))
        self.table.resizeColumnsToContents()
        self.progress.setValue(r + 1)

    def _finished(self, items, error):
        self.items = items
        self.worker = None
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.save_btn.setEnabled(bool(items))
        solved = sum(1 for i in items if i.status in ("solved", "already solved"))
        text = f"Done: {solved} of {len(items)} images solved."
        if error == "cancelled":
            text = f"Cancelled. {solved} images solved before stopping."
        elif error:
            text = f"Stopped by an error: {error}"
        self.status.setText(text)

    def save_summary(self):
        start = str(Path(self.folder.text().strip()) / "platesolver_batch_summary.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Save summary", start, "CSV (*.csv)")
        if path:
            write_summary_csv(Path(path), self.items)
            self.status.setText(f"Saved {path}")

    def _open_row(self, r, _c):
        item = self.table.item(r, 0)
        path = item.data(Qt.UserRole) if item else None
        if path and self.worker is None:
            self.open_requested.emit(path)

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.ctx.cancel_event.set()
            self.worker.wait(10000)
        super().closeEvent(event)
