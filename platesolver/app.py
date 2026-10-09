# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Start-up: logging, settings, plugins, then the main window."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from platesolver import APP_NAME, __version__
from platesolver.core import paths
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import SettingsStore


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = RotatingFileHandler(paths.log_file(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    # astropy must create its own logger first; touching "astropy" before it is
    # imported leaves a plain Logger behind and astropy then fails to import.
    import astropy  # noqa: F401
    logging.getLogger("astropy").setLevel(logging.WARNING)


def selftest(report_path: str = "") -> int:
    """Check a packaged build without showing a window: modules, data files, reading images, the UI.

    PlateSolver --selftest [report-file]   (exit code 0 = everything works)
    """
    import os
    import tempfile
    import traceback
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    lines, ok = [f"{APP_NAME} {__version__} self-test", f"Python {sys.version.split()[0]} on {sys.platform}",
                 f"Frozen: {getattr(sys, 'frozen', False)}"], True

    def check(name, fn):
        nonlocal ok
        try:
            result = fn()
            lines.append(f"OK    {name}" + (f": {result}" if result not in (None, "") else ""))
        except Exception as exc:
            ok = False
            lines.append(f"FAIL  {name}: {type(exc).__name__}: {exc}")
            lines.append("      " + traceback.format_exc().strip().replace("\n", "\n      "))

    tmp = Path(tempfile.mkdtemp(prefix="platesolver_selftest_"))
    os.environ["PLATESOLVER_HOME"] = str(tmp / "home")
    store = SettingsStore(tmp / "settings.json")
    reg = {}

    def plugins():
        reg["r"] = PluginRegistry(store).discover()
        kinds = sorted({p.kind for p in reg["r"].plugins})
        if reg["r"].errors:
            raise RuntimeError("; ".join(f"{m}: {e}" for m, e in reg["r"].errors))
        if len(reg["r"].plugins) < 15:
            raise RuntimeError(f"only {len(reg['r'].plugins)} modules found")
        return f"{len(reg['r'].plugins)} modules ({', '.join(kinds)})"
    check("modules", plugins)

    def data_files():
        from platesolver.plugins.catalogs.openngc_catalog import load_table
        from platesolver.core.quiz import load_bank
        from platesolver.ui.help_window import MANUAL
        n = len(load_table()["rows"])
        q = len(load_bank("").questions)
        q2 = len(load_bank("builtin:quiz_astrophysics.dat").questions)
        if not MANUAL.exists():
            raise FileNotFoundError(MANUAL)
        from platesolver.core import starcatalog
        stars = len(starcatalog._read(starcatalog.BUNDLED)["ra"])
        from platesolver.ui.licence_dialog import LEGAL
        for name in ("LICENSE.txt", "LGPL-3.0.txt", "THIRD-PARTY-NOTICES.md"):
            if not (LEGAL / name).is_file():
                raise FileNotFoundError(LEGAL / name)
        return (f"OpenNGC {n} objects, quizzes {q} + {q2} questions, star catalogue {stars} stars, manual and "
                "licence files present")
    check("data files", data_files)

    def images():
        import numpy as np
        from PIL import Image
        from astropy.io import fits
        from platesolver.core.general import GeneralSettings
        from platesolver.core.pipeline import Pipeline
        p = Pipeline(reg["r"], store.section(GeneralSettings()))
        Image.fromarray(np.zeros((40, 60), np.uint8)).save(tmp / "M101_test.jpg")
        fits.PrimaryHDU(np.zeros((40, 60), np.uint16)).writeto(tmp / "test.fits")
        a, b = p.load(tmp / "M101_test.jpg"), p.load(tmp / "test.fits")
        from platesolver.core.objecthint import resolve
        h = resolve("M101", use_simbad=False)
        exts = sorted({e for l in p.loaders() for e in l.extensions})
        return f"{a.format} + {b.format} read; M101 at RA {h.ra_deg:.2f}; formats {' '.join(exts)}"
    check("reading images", images)

    def optional_libs():
        out = []
        for mod in ("pillow_heif", "lz4.block", "zstandard", "tifffile"):
            __import__(mod)
            out.append(mod.split(".")[0])
        return ", ".join(out)
    check("optional libraries", optional_libs)

    def ui():
        from PySide6.QtWidgets import QApplication
        from platesolver.ui.main_window import MainWindow
        app = QApplication.instance() or QApplication([])
        w = MainWindow(store, reg["r"])
        w.close()
        app.processEvents()
        return "main window created"
    check("user interface", ui)

    lines.append("RESULT: " + ("all checks passed" if ok else "PROBLEMS FOUND"))
    text = "\n".join(lines)
    print(text)
    if report_path:
        Path(report_path).write_text(text + "\n", encoding="utf-8")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--selftest" in argv:
        i = argv.index("--selftest")
        return selftest(argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("-") else "")
    setup_logging()
    logging.getLogger(__name__).info("Starting %s %s", APP_NAME, __version__)

    from PySide6.QtWidgets import QApplication
    from platesolver.ui.main_window import MainWindow
    from platesolver.ui.theme import apply_dark_theme

    def excepthook(exc_type, exc, tb):
        import traceback
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        logging.getLogger("platesolver").error("Unhandled error:\n%s", text)
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, APP_NAME, f"Unexpected error:\n\n{exc}\n\nDetails are in {paths.log_file()}")
        except Exception:
            pass
    sys.excepthook = excepthook

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    icon = Path(__file__).resolve().parent / "data" / "icon.png"
    if icon.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon)))
    apply_dark_theme(app)

    store = SettingsStore(paths.settings_file())
    registry = PluginRegistry(store, extra_dirs=[paths.user_plugin_dir()]).discover()
    window = MainWindow(store, registry)
    window.show()
    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        window.open_file(Path(files[0]))
    return app.exec()
