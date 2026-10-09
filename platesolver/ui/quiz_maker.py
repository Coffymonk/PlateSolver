# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Create a quiz from a spreadsheet: get the template, choose the filled-in file, name it, create."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QTextBrowser, QVBoxLayout)

from platesolver.core import quizmaker
from platesolver.core.quiz import my_quizzes_dir
from platesolver.ui import theme


def _step(number: int, title: str) -> QLabel:
    lab = QLabel(f"<span style='color:{theme.ACCENT}; font-size:13pt; font-weight:700'>{number}</span>"
                 f"&nbsp;&nbsp;<b>{html.escape(title)}</b>")
    lab.setTextFormat(Qt.RichText)
    return lab


def _muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


class QuizMakerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create a quiz")
        self.draft: quizmaker.QuizDraft | None = None
        self.sheet: Path | None = None
        self.created_key = ""
        self.result_message = ""

        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        lay.addWidget(_muted("Write your questions in a spreadsheet (Excel, Numbers or Google Sheets), one row per "
                             "question, and PlateSolver turns them into a quiz."))
        lay.addSpacing(6)

        lay.addWidget(_step(1, "Get the template"))
        lay.addWidget(_muted("A spreadsheet with the column headings and three example questions. Replace the "
                             "examples with your own questions and save it."))
        row = QHBoxLayout()
        tpl = QPushButton("Get the template…")
        tpl.clicked.connect(self._template)
        row.addWidget(tpl)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(8)

        lay.addWidget(_step(2, "Choose your spreadsheet"))
        row = QHBoxLayout()
        pick = QPushButton("Choose file…")
        pick.clicked.connect(self._choose)
        self.file_label = QLabel("No file chosen")
        self.file_label.setObjectName("muted")
        row.addWidget(pick)
        row.addWidget(self.file_label, 1)
        lay.addLayout(row)
        self.report = QTextBrowser()
        self.report.setMinimumHeight(120)
        self.report.setOpenExternalLinks(False)
        self.report.setHtml(f"<span style='color:{theme.MUTED}'>The check of your questions appears here.</span>")
        lay.addWidget(self.report, 1)
        lay.addSpacing(8)

        lay.addWidget(_step(3, "Name the quiz and create it"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Name:"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. My astronomy club quiz")
        row.addWidget(self.name, 1)
        self.create_btn = QPushButton("Create quiz")
        self.create_btn.setDefault(True)
        self.create_btn.setEnabled(False)
        self.create_btn.clicked.connect(self._create)
        row.addWidget(self.create_btn)
        lay.addLayout(row)

        foot = QHBoxLayout()
        folder = QPushButton("Open My quizzes folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(my_quizzes_dir()))))
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        foot.addWidget(folder)
        foot.addStretch(1)
        foot.addWidget(close)
        lay.addSpacing(6)
        lay.addLayout(foot)
        self.resize(620, 560)

    # ------------------------------------------------------------------ steps
    def _template(self):
        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(self, "Save the quiz template",
                                              str(Path(docs) / "PlateSolver quiz template.xlsx"),
                                              "Excel spreadsheet (*.xlsx);;CSV file (*.csv)")
        if not path:
            return
        try:
            out = quizmaker.write_template(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "Template", f"The template could not be saved: {exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))
        self.report.setHtml(f"Template saved as <b>{html.escape(str(out))}</b> and opened. Fill it in, save it, "
                            "then choose it in step 2.")

    def _choose(self):
        start = str(self.sheet.parent) if self.sheet else \
            (QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Choose your quiz spreadsheet", start,
                                              "Spreadsheets (*.xlsx *.xlsm *.csv);;All files (*)")
        if path:
            self.load(Path(path))

    def load(self, path: Path):
        """Read and check a spreadsheet (also used by tests)."""
        self.sheet = path
        self.file_label.setText(path.name)
        if not self.name.text().strip():
            self.name.setText(path.stem.replace("_", " "))
        self.draft = quizmaker.read_spreadsheet(path, self.name.text().strip())
        d = self.draft
        parts = []
        if d.ok:
            parts.append(f"<p style='color:{theme.OK}'><b>✓ Ready: {html.escape(d.summary())}.</b></p>")
        elif d.errors:
            parts.append(f"<p style='color:{theme.ERROR}'><b>Please fix these in the spreadsheet, save it and "
                         "choose it again:</b></p>")
        if d.errors:
            parts.append("<ul>" + "".join(f"<li>{html.escape(e)}</li>" for e in d.errors[:50]) + "</ul>")
            if len(d.errors) > 50:
                parts.append(f"<p>…and {len(d.errors) - 50} more.</p>")
        if d.warnings:
            parts.append(f"<p style='color:{theme.WARN}'><b>Worth a look (the quiz can still be made):</b></p><ul>"
                         + "".join(f"<li>{html.escape(w)}</li>" for w in d.warnings[:30]) + "</ul>")
        if d.ok and d.bank:
            parts.append("<p>Topics: " + ", ".join(
                f"{html.escape(t.name)} ({d.bank.count(t.id)})" for t in d.bank.topics) + "</p>")
        self.report.setHtml("".join(parts))
        self.create_btn.setEnabled(d.ok)

    def _create(self):
        if not self.draft or not self.draft.ok:
            return
        title = self.name.text().strip() or (self.sheet.stem if self.sheet else "My quiz")
        target = my_quizzes_dir() / quizmaker.safe_file_name(title)
        if target.exists() and QMessageBox.question(
                self, "Create quiz", f"A quiz called '{title}' already exists. Replace it?") != QMessageBox.Yes:
            return
        try:
            path = quizmaker.save_quiz(self.draft, title)
        except Exception as exc:
            QMessageBox.warning(self, "Create quiz", f"The quiz could not be saved: {exc}")
            return
        self.created_key = f"user:{path.name}"
        self.result_message = f"Created '{title}' ({self.draft.summary()}) in {path.parent}"
        self.report.setHtml(f"<p style='color:{theme.OK}'><b>✓ The quiz '{html.escape(title)}' was created</b> "
                            f"({html.escape(self.draft.summary())}).</p><p>It is now in the quiz list. The file "
                            f"is <b>{html.escape(str(path))}</b>; you can share it with others.</p>")
