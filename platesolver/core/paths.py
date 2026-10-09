# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Where PlateSolver keeps its own files (settings, log, cache, user plugins)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from platesolver import APP_NAME


def app_data_dir() -> Path:
    """Per-user folder for settings, log and cache.

    Windows: %APPDATA%\\PlateSolver   Other: ~/.platesolver
    Can be overridden with the PLATESOLVER_HOME environment variable.
    """
    override = os.environ.get("PLATESOLVER_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"]) / APP_NAME
    else:
        base = Path.home() / ".platesolver"
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_file() -> Path:
    return app_data_dir() / "settings.json"


def log_file() -> Path:
    return app_data_dir() / "platesolver.log"


def cache_dir() -> Path:
    d = app_data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_plugin_dir() -> Path:
    """Extra plugins dropped here are loaded at start-up, next to the built-in ones."""
    d = app_data_dir() / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d
