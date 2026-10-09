# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Runs slow work (loading, solving, lookups) off the UI thread."""
from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from platesolver.core.plugin import Cancelled, TaskContext

log = logging.getLogger(__name__)


class TaskWorker(QThread):
    message = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn: Callable[[TaskContext], Any], parent=None, confirm=None):
        super().__init__(parent)
        self.fn = fn
        self.ctx = TaskContext(log=self.message.emit)
        if confirm is not None:
            self.ctx.confirm = confirm

    def cancel(self):
        self.ctx.cancel_event.set()

    def run(self):
        try:
            result = self.fn(self.ctx)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            log.error("Task failed:\n%s", traceback.format_exc())
            self.failed.emit(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)
        else:
            self.succeeded.emit(result)
