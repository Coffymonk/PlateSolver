# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Lets background tasks ask the user a yes/no question on the main (UI) thread."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import QCheckBox, QMessageBox


class ConfirmBridge(QObject):
    """Create once, in the main thread. `confirm` may be called from any thread."""

    _ask = Signal(str, str, str, str, str, object)

    def __init__(self, parent_window):
        super().__init__(parent_window)
        self.window = parent_window
        self.remembered: dict[str, bool] = {}       # answers kept until the program closes
        self._ask.connect(self._show, Qt.BlockingQueuedConnection)

    def confirm(self, title: str, text: str, remember_key: str = "", yes: str = "Yes", no: str = "No") -> bool:
        if remember_key and remember_key in self.remembered:
            return self.remembered[remember_key]
        holder: dict = {}
        if QThread.currentThread() is self.thread():
            self._show(title, text, remember_key, yes, no, holder)
        else:
            self._ask.emit(title, text, remember_key, yes, no, holder)   # waits until the user has answered
        return bool(holder.get("answer"))

    @Slot(str, str, str, str, str, object)
    def _show(self, title, text, remember_key, yes_label, no_label, holder):
        box = QMessageBox(self.window)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Warning if 'star' in title.lower() else QMessageBox.Question)
        box.setTextFormat(Qt.RichText)
        box.setText(text)
        yes = box.addButton(yes_label, QMessageBox.AcceptRole)
        box.addButton(no_label, QMessageBox.RejectRole)
        box.setDefaultButton(yes)
        remember = None
        if remember_key:
            remember = QCheckBox("Remember my answer until PlateSolver is closed")
            box.setCheckBox(remember)
        box.exec()
        answer = box.clickedButton() is yes
        holder["answer"] = answer
        if remember is not None and remember.isChecked():
            self.remembered[remember_key] = answer
