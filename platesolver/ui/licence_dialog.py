# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Help › Licence and credits: PlateSolver's licence and everything it is built on."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTabWidget, QTextBrowser, QVBoxLayout

LEGAL = Path(__file__).resolve().parent.parent / "legal"


def _read(name: str) -> str:
    try:
        return (LEGAL / name).read_text(encoding="utf-8")
    except OSError as exc:
        return f"({name} is missing: {exc})"


class LicenceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Licence and credits")
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        notices = QTextBrowser()
        notices.setOpenExternalLinks(True)
        notices.setMarkdown(_read("THIRD-PARTY-NOTICES.md"))
        tabs.addTab(notices, "Licence and credits")
        for title, name in (("GNU GPL 3.0 (PlateSolver)", "LICENSE.txt"), ("GNU LGPL 3.0 (Qt, libheif)", "LGPL-3.0.txt")):
            t = QTextBrowser()
            t.setPlainText(_read(name))
            tabs.addTab(t, title)
        self.tabs = tabs
        lay.addWidget(tabs, 1)
        row = QHBoxLayout()
        folder = QPushButton("Open the licence folder")
        folder.setToolTip("The licence texts of PlateSolver and the libraries it includes")
        folder.clicked.connect(self._open_folder)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(folder)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.resize(820, 640)

    @staticmethod
    def _open_folder():
        base = LEGAL.parent.parent                       # in the apps: _internal (licenses/ is next to platesolver/)
        target = base / "licenses" if (base / "licenses").is_dir() else LEGAL
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
