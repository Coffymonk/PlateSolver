# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Base class for all plugins and the task context they run in."""
from __future__ import annotations

import logging
import threading
from abc import ABC
from dataclasses import dataclass, field
from typing import Callable

from platesolver.core.settings import SectionSettings, SettingsSection


class Cancelled(Exception):
    """Raised when the user cancels a running task."""


@dataclass
class TaskContext:
    """Handed to long-running plugin calls: logging and cancel support."""
    log: Callable[[str], None] = field(default=lambda msg: logging.getLogger("platesolver").info(msg))
    cancel_event: threading.Event = field(default_factory=threading.Event)
    progress: Callable[[int, int], None] = field(default=lambda done, total: None)
    # Ask the user a yes/no question: confirm(title, text, remember_key="", yes="Yes", no="No") -> bool.
    # Without a user interface (tests, scripts) the answer is yes, i.e. the module's settings decide.
    confirm: Callable[..., bool] = field(default=lambda title, text, remember_key="", **labels: True)

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()


class Plugin(SettingsSection, ABC):
    """Every plugin subclasses one of the interfaces in `interfaces.py`, which subclass this.

    Class attributes to set:
        plugin_id    unique short id within its kind, e.g. "astap"
        name         shown in the UI
        description  one or two sentences, shown on its Settings page
        priority     lower runs first (e.g. ASTAP before astrometry.net)
    """

    kind: str = "plugin"
    kind_label: str = "Plugins"
    plugin_id: str = ""
    name: str = ""
    description: str = ""
    priority: int = 100
    enabled_by_default: bool = True

    ENABLED_KEY = "_enabled"

    def __init__(self, settings: SectionSettings | None = None):
        self.settings = settings
        self.log = logging.getLogger(f"platesolver.{self.kind}.{self.plugin_id}")

    @classmethod
    def section_key(cls) -> str:
        return f"{cls.kind}.{cls.plugin_id}"

    @property
    def section_id(self) -> str:  # type: ignore[override]
        return self.section_key()

    @property
    def enabled(self) -> bool:
        if self.settings is None:
            return self.enabled_by_default
        return bool(self.settings.get(self.ENABLED_KEY, self.enabled_by_default))

    def setting(self, key: str, default=None):
        if self.settings is None:
            for f in self.settings_schema():
                if f.key == key:
                    return f.default
            return default
        return self.settings.get(key, default)

    def is_available(self) -> tuple[bool, str]:
        """Can this plugin run right now? Return (ok, reason shown to the user)."""
        return True, ""
