# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""A small on-disk cache so reopening an image doesn't repeat online lookups."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from platesolver.core import paths


class JsonCache:
    """Key -> JSON value, one file per key, with an age limit."""

    def __init__(self, name: str, max_age_days: float = 30.0, folder: Path | None = None):
        self.folder = (folder or paths.cache_dir()) / name
        self.folder.mkdir(parents=True, exist_ok=True)
        self.max_age = max_age_days * 86400.0

    def _file(self, key: str) -> Path:
        return self.folder / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    def get(self, key: str) -> Any | None:
        f = self._file(key)
        try:
            if self.max_age > 0 and time.time() - f.stat().st_mtime > self.max_age:
                return None
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def set(self, key: str, value: Any) -> None:
        f = self._file(key)
        try:
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            tmp.replace(f)
        except OSError:
            pass

    def clear(self) -> int:
        n = 0
        for f in self.folder.glob("*.json"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
        return n
