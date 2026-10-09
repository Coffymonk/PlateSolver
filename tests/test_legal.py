# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Licence: GPL text, third-party notices, source headers and the licence files the apps carry."""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGAL = ROOT / "platesolver" / "legal"


def test_gpl_text_and_copies():
    gpl = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in gpl and "Version 3, 29 June 2007" in gpl
    assert (LEGAL / "LICENSE.txt").read_text(encoding="utf-8") == gpl
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in (LEGAL / "LGPL-3.0.txt").read_text(encoding="utf-8")
    assert (LEGAL / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8") == \
        (ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")


def test_every_library_is_credited():
    notices = (ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8").lower()
    manual = (ROOT / "platesolver" / "help" / "manual.html").read_text(encoding="utf-8").lower()
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        name = re.split(r"[<>=!~ ]", line.strip(), maxsplit=1)[0].lower()
        if name:
            assert name in notices, f"{name} missing from THIRD-PARTY-NOTICES.md"
            assert name in manual, f"{name} missing from the manual's credits"
    for data in ("openngc", "d3-celestial", "simbad", "hipparcos", "tycho-2", "hyperleda", "astap"):
        assert data in notices


def test_source_files_carry_the_licence_header():
    missing = []
    for p in list((ROOT / "platesolver").rglob("*.py")) + list((ROOT / "tools").glob("*.py")) + \
            list((ROOT / "build").glob("*.py")) + list((ROOT / "tests").glob("*.py")):
        if "__pycache__" in p.parts:
            continue
        if "SPDX-License-Identifier: GPL-3.0-or-later" not in p.read_text(encoding="utf-8")[:300]:
            missing.append(str(p.relative_to(ROOT)))
    assert not missing, missing


def test_apps_get_the_library_licences():
    sys.path.insert(0, str(ROOT / "build"))
    import licenses
    entries = licenses.license_files()
    libs = {dest.split("/")[1].lower() for _src, dest in entries}
    assert {"numpy", "astropy", "pillow", "openpyxl"} <= libs
    assert all(os.path.isfile(src) and dest.startswith("licenses/") for src, dest in entries)
    spec = (ROOT / "build" / "platesolver.spec").read_text(encoding="utf-8")
    assert '"platesolver" / "legal"' in spec and "license_files()" in spec


def test_licence_dialog_and_about(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from platesolver import COPYRIGHT, __license__
    from platesolver.ui.licence_dialog import LicenceDialog
    assert __license__ == "GPL-3.0-or-later" and "Miklos Elmberg" in COPYRIGHT
    dlg = LicenceDialog()
    assert dlg.tabs.count() == 3 and "Version 3" in dlg.tabs.widget(1).toPlainText()
    assert "pillow-heif" in dlg.tabs.widget(0).toPlainText()
    dlg.close()
    app.processEvents()
