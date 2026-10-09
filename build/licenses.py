# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""The licence texts of the Python libraries bundled in the apps, for the PyInstaller recipe.

Many of the libraries' licences (BSD, MIT, Apache, LGPL) ask that their licence text travels with every
binary copy. This walks the libraries PlateSolver needs, and everything they need in turn, and returns
their licence files as PyInstaller "datas" entries, so they end up in a `licenses/<library>/` folder
inside the app.
"""
from __future__ import annotations

import importlib.metadata as md
import re
from pathlib import Path

ROOTS = ["PySide6", "numpy", "astropy", "tifffile", "imagecodecs", "Pillow", "pillow-heif", "lz4", "zstandard",
         "openpyxl", "setuptools"]
_LICENCE_NAME = re.compile(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)


def _name(req: str) -> str:
    return re.split(r"[ ;<>=!~\[(]", req.strip(), maxsplit=1)[0]


def distributions(roots=ROOTS) -> list:
    """The installed distributions for `roots` and their (non-optional) requirements, each once."""
    seen: set[str] = set()
    out = []

    def walk(name: str) -> None:
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            return
        key = (dist.metadata["Name"] or name).lower().replace("_", "-")
        if key in seen:
            return
        seen.add(key)
        out.append(dist)
        for req in dist.requires or []:
            if "extra ==" not in req:
                walk(_name(req))

    for r in roots:
        walk(r)
    return out


def license_files(roots=ROOTS) -> list[tuple[str, str]]:
    """[(source file, destination folder in the app)] for every licence file in the libraries' metadata."""
    entries = []
    for dist in distributions(roots):
        name = dist.metadata["Name"] or "unknown"
        for f in dist.files or []:
            parts = Path(str(f)).parts
            if not parts or not parts[0].endswith(".dist-info") or not _LICENCE_NAME.search(parts[-1]):
                continue
            src = Path(dist.locate_file(f))
            if not src.is_file():
                continue
            inner = [p for p in parts[1:-1] if p != "licenses"]            # keep sub-folders apart
            entries.append((str(src), "/".join(["licenses", name] + inner)))
    return entries


if __name__ == "__main__":
    for d in distributions():
        print(f"{d.metadata['Name']} {d.version}: {d.metadata.get('License-Expression') or d.metadata.get('License') or '?'}")
    print(len(license_files()), "licence files")
