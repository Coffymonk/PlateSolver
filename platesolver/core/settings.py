# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Settings: field descriptions (schemas) and a JSON-backed store.

Every settings page in the app is built from a list of `SettingField`s. The
General page is one section; every plugin is another. A plugin only has to
describe its settings - the Settings dialog builds the page automatically.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

log = logging.getLogger(__name__)

# Field types understood by the Settings dialog
STR, INT, FLOAT, BOOL, CHOICE, FILE, FOLDER = "str", "int", "float", "bool", "choice", "file", "folder"
ACTION = "action"     # a button; not a stored value


@dataclass
class SettingField:
    key: str
    label: str
    type: str = STR
    default: Any = None
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    decimals: int = 2
    suffix: str = ""
    choices: Sequence[tuple[Any, str]] = ()      # (stored value, shown label)
    file_filter: str = ""                        # e.g. "Programs (*.exe)"
    # Returns (ok, message). Shown live under the field in the dialog.
    validator: Callable[[Any], tuple[bool, str]] | None = None
    # ACTION fields: action(current values of the page, log) -> message; status() -> text shown under the button
    action: Callable[[dict, Callable[[str], None]], str] | None = None
    status: Callable[[], str] | None = None
    ui: bool = False          # ACTION runs in the window (it may open dialogs): action(values, parent) -> message
    enabled_by: str = ""     # key of a BOOL field on the same page: this field is greyed out while it is off


class SettingsSection:
    """Anything that owns a page in the Settings dialog."""

    section_id: str = ""
    name: str = ""
    description: str = ""

    def settings_schema(self) -> list[SettingField]:
        return []

    def describe(self, values: dict) -> str:
        """Optional live summary for the Settings page, given the values currently entered."""
        return ""


class SettingsStore:
    """All settings, stored as {section_id: {key: value}} in one JSON file."""

    def __init__(self, path: Path | None):
        self.path = path
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        if path and path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # corrupt file - keep a backup, start fresh
                log.warning("Could not read settings (%s); starting with defaults", exc)
                try:
                    path.replace(path.with_suffix(".broken.json"))
                except OSError:
                    pass

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self._data.get(section, {}).get(key, default)

    def has(self, section: str, key: str) -> bool:
        return key in self._data.get(section, {})

    def set(self, section: str, key: str, value: Any) -> None:
        with self._lock:
            self._data.setdefault(section, {})[key] = value

    def reset_section(self, section: str) -> None:
        with self._lock:
            self._data.pop(section, None)

    def save(self) -> None:
        if not self.path:
            return
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    def section(self, owner: SettingsSection) -> "SectionSettings":
        return SectionSettings(self, owner)


class SectionSettings:
    """The settings of one section, with defaults taken from its schema."""

    def __init__(self, store: SettingsStore, owner: SettingsSection):
        self.store = store
        self.owner = owner
        self._defaults: dict[str, Any] | None = None

    @property
    def section_id(self) -> str:
        return self.owner.section_id

    def defaults(self) -> dict[str, Any]:
        if self._defaults is None:
            self._defaults = {f.key: f.default for f in self.owner.settings_schema()}
        return self._defaults

    def get(self, key: str, default: Any = None) -> Any:
        if self.store.has(self.section_id, key):
            return self.store.get(self.section_id, key)
        return self.defaults().get(key, default)

    __getitem__ = get

    def set(self, key: str, value: Any) -> None:
        self.store.set(self.section_id, key, value)

    def reset(self) -> None:
        self.store.reset_section(self.section_id)
        self._defaults = None
