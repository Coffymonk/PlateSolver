# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Finds and creates plugins.

Built-in plugins live in the `platesolver.plugins` package. Extra plugins can be
dropped as .py files (or packages) into the user plugin folder. Any class that
subclasses one of the interfaces, sets `plugin_id` and isn't abstract is used.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import pkgutil
import sys
from pathlib import Path
from typing import Iterable, TypeVar

from platesolver.core.interfaces import ALL_KINDS
from platesolver.core.plugin import Plugin
from platesolver.core.settings import SettingsStore

log = logging.getLogger(__name__)
P = TypeVar("P", bound=Plugin)


class PluginRegistry:
    def __init__(self, store: SettingsStore, builtin_package: str = "platesolver.plugins",
                 extra_dirs: Iterable[Path] = ()):
        self.store = store
        self.builtin_package = builtin_package
        self.extra_dirs = [Path(d) for d in extra_dirs]
        self.plugins: list[Plugin] = []
        self.errors: list[tuple[str, str]] = []   # (module, error) for the log / settings

    # ------------------------------------------------------------------ discovery
    def discover(self) -> "PluginRegistry":
        self.plugins.clear()
        self.errors.clear()
        seen: set[str] = set()
        for module in self._iter_modules():
            for cls in self._plugin_classes(module):
                key = cls.section_key()
                if key in seen:
                    self.errors.append((module.__name__, f"Duplicate plugin id '{key}' ignored"))
                    continue
                seen.add(key)
                try:
                    plugin = cls()
                    plugin.settings = self.store.section(plugin)
                    self.plugins.append(plugin)
                except Exception as exc:
                    self.errors.append((module.__name__, f"{cls.__name__}: {exc}"))
                    log.exception("Could not create plugin %s", cls.__name__)
        self.plugins.sort(key=lambda p: (p.kind, p.priority, p.name))
        for mod, err in self.errors:
            log.warning("Plugin problem in %s: %s", mod, err)
        log.info("Loaded %d plugins", len(self.plugins))
        return self

    def _iter_modules(self):
        pkg = importlib.import_module(self.builtin_package)
        for info in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + "."):
            try:
                yield importlib.import_module(info.name)
            except Exception as exc:
                self.errors.append((info.name, f"{type(exc).__name__}: {exc}"))
        for folder in self.extra_dirs:
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                if path.name.startswith("_"):
                    continue
                if path.suffix == ".py":
                    target = path
                elif path.is_dir() and (path / "__init__.py").exists():
                    target = path / "__init__.py"
                else:
                    continue
                name = f"platesolver_user_plugins.{path.stem}"
                try:
                    spec = importlib.util.spec_from_file_location(
                        name, target, submodule_search_locations=[str(path)] if path.is_dir() else None)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[name] = module
                    spec.loader.exec_module(module)
                    yield module
                except Exception as exc:
                    self.errors.append((str(path), f"{type(exc).__name__}: {exc}"))

    @staticmethod
    def _plugin_classes(module):
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if (cls.__module__ == module.__name__ and issubclass(cls, Plugin)
                    and cls not in ALL_KINDS and not inspect.isabstract(cls) and cls.plugin_id):
                yield cls

    # ------------------------------------------------------------------ lookup
    def of_kind(self, kind: type[P], enabled_only: bool = True) -> list[P]:
        found = [p for p in self.plugins if isinstance(p, kind)]
        if enabled_only:
            found = [p for p in found if p.enabled]
        return sorted(found, key=lambda p: p.priority)

    def get(self, section_id: str) -> Plugin | None:
        return next((p for p in self.plugins if p.section_id == section_id), None)
