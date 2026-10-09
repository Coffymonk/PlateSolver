# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Install ASTAP and its star databases: a status overview, a clear disclaimer, optional separate steps."""
from __future__ import annotations

import html
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QVBoxLayout)

from platesolver.core import astap_setup as S
from platesolver.ui import theme

TERMS_KEY = "astap_terms_ok"


class _Downloader(QThread):
    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.cancel = threading.Event()

    def run(self):
        try:
            path = S.download(self.url, progress=lambda d, t: self.progress.emit(d, t), cancel=self.cancel)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.done.emit(str(path))


def _section(title: str) -> QLabel:
    lab = QLabel(f"<b>{html.escape(title)}</b>")
    lab.setStyleSheet("font-size: 11pt; margin-top: 8px;")
    return lab


class AstapSetupDialog(QDialog):
    """`store` is the settings store; `profile` the active equipment profile (for the recommendation)."""

    def __init__(self, store, profile=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Install ASTAP and star databases")
        self.store = store
        self.profile = profile
        self.key = S.platform_key()
        self.worker: _Downloader | None = None
        self.pending = ""                 # "program" or a database id while downloading
        self.changed = False
        self.before: set[str] | None = None   # databases present when an installer was started

        lay = QVBoxLayout(self)
        lay.setSpacing(6)

        # ---- disclaimer
        box = QFrame()
        box.setObjectName("terms")
        from platesolver.ui.object_filter import _checkmark_file
        box.setStyleSheet("#terms { background: #353c48; border: 1px solid #8a95a5; border-radius: 8px; }"
                          "#terms QLabel, #terms QCheckBox { color: #eef1f5; background: transparent; }"
                          "#terms QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #8a95a5;"
                          " border-radius: 3px; background: #1d2128; }"
                          "#terms QCheckBox::indicator:hover { border-color: #8cc4ff; }"
                          "#terms QCheckBox::indicator:checked { background: #3d8ee6; border-color: #8cc4ff;"
                          f" image: url({_checkmark_file()}); }}")
        bl = QVBoxLayout(box)
        terms = QLabel(
            "<b>About ASTAP</b><br>PlateSolver uses <b>ASTAP</b>, a free plate solver by Han Kleijn, to find where "
            "your image points in the sky. ASTAP is <b>separate software</b>: it is downloaded from its official "
            "download site (SourceForge, linked from <a href='https://www.hnsky.org/astap.htm' style='color:#8cc4ff'>"
            "hnsky.org</a>) and installed by its own installer, under its own licence terms.<br><br>"
            "<b>ASTAP also needs a star database</b> – the stars it compares your image with. The database is a "
            "separate download, which you can install now or later from this window. Some databases are large "
            "(up to about 1.3 GB), so the download can take a while on a slow connection.<br><br>"
            "You can also install ASTAP and the databases yourself from hnsky.org and choose them in Settings.")
        terms.setWordWrap(True)
        terms.setTextFormat(Qt.RichText)
        terms.setOpenExternalLinks(True)
        bl.addWidget(terms)
        self.agree = QCheckBox("I understand that ASTAP and its star databases are third-party software, "
                               "downloaded from ASTAP's own site.")
        self.agree.setChecked(bool(self.store.get("ui", TERMS_KEY)))
        self.agree.toggled.connect(self._agreed)
        bl.addWidget(self.agree)
        lay.addWidget(box)

        # ---- program
        lay.addWidget(_section("1  ASTAP program"))
        self.prog_status = QLabel()
        self.prog_status.setWordWrap(True)
        self.prog_status.setTextFormat(Qt.RichText)
        lay.addWidget(self.prog_status)
        row = QHBoxLayout()
        self.prog_btn = QPushButton("Download and install ASTAP")
        self.prog_btn.clicked.connect(self._get_program)
        page_btn = QPushButton("Open ASTAP's download page")
        page_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(S.ASTAP_PAGE)))
        row.addWidget(self.prog_btn)
        row.addWidget(page_btn)
        row.addStretch(1)
        lay.addLayout(row)

        # ---- databases
        lay.addWidget(_section("2  Star database (can be done later)"))
        self.db_status = QLabel()
        self.db_status.setWordWrap(True)
        self.db_status.setTextFormat(Qt.RichText)
        lay.addWidget(self.db_status)
        row = QHBoxLayout()
        self.db_combo = QComboBox()
        row.addWidget(self.db_combo, 1)
        self.db_btn = QPushButton("Download and install")
        self.db_btn.clicked.connect(self._get_database)
        row.addWidget(self.db_btn)
        lay.addLayout(row)

        # ---- progress
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress_label = QLabel()
        self.progress_label.setWordWrap(True)
        self.progress_label.setTextFormat(Qt.RichText)
        row = QHBoxLayout()
        row.addWidget(self.progress, 1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel)
        row.addWidget(self.cancel_btn)
        lay.addSpacing(4)
        lay.addLayout(row)
        lay.addWidget(self.progress_label)

        foot = QHBoxLayout()
        check = QPushButton("Check again")
        check.setToolTip("Look for ASTAP and its databases again, e.g. after an installer has finished")
        check.clicked.connect(self.refresh)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        foot.addWidget(check)
        foot.addStretch(1)
        foot.addWidget(close)
        lay.addStretch(1)
        lay.addLayout(foot)
        self.resize(640, 620)
        self.refresh()

    # ------------------------------------------------------------------ status
    def refresh(self):
        exe, dbs = S.refresh_settings(self.store)
        self.exe, self.dbs = exe, dbs
        ok, warn, bad = theme.OK, theme.WARN, theme.ERROR
        if exe:
            self.prog_status.setText(f"<span style='color:{ok}'>✓ Installed:</span> {html.escape(str(exe))}")
            self.prog_btn.setText("Download and install ASTAP again (update)")
        else:
            self.prog_status.setText(f"<span style='color:{bad}'>✗ ASTAP was not found on this computer.</span>")
            self.prog_btn.setText("Download and install ASTAP")
        rec = S.recommended(self.profile)
        rec_ids = [r[0] for r in rec]
        if dbs:
            have = ", ".join(d.upper() for d in dbs)
            suits = any(r in dbs for r in rec_ids) or (rec_ids[0] in ("d50", "d80") and
                                                       any(d in dbs for d in ("d50", "d80", "d20", "d05")))
            text = f"<span style='color:{ok}'>✓ Installed:</span> {have}"
            if not suits:
                r0 = S.get(rec_ids[0])
                text += (f"<br><span style='color:{warn}'>⚠ For your profile, <b>{r0.name}</b> is recommended "
                         f"({html.escape(rec[0][1])}).</span>")
        else:
            r0 = S.get(rec_ids[0])
            text = (f"<span style='color:{warn}'>⚠ No star database yet – ASTAP needs one before it can solve "
                    f"images.</span> Recommended for your profile: <b>{r0.name}</b> ({html.escape(rec[0][1])}).")
        if self.before is not None:
            lost = sorted(self.before - set(dbs))
            if lost:
                names = ", ".join(d.upper() for d in lost)
                mac = ("<br>On a Mac this happens when two of ASTAP's database installers share an internal "
                       "name: macOS treats the second as an update of the first and removes its files. Install "
                       f"{names} again with this window; it then offers to <b>protect</b> the other databases."
                       if sys.platform == "darwin" else "")
                text += (f"<br><span style='color:{bad}'>✗ {names} is no longer found after the last install."
                         f"</span>{mac}")
        self.db_status.setText(text)
        current = self.db_combo.currentData()
        self.db_combo.clear()
        order = rec_ids + [d.id for d in S.DATABASES if d.id not in rec_ids]
        for db_id in order:
            db = S.get(db_id)
            dl = S.database_download(db, self.key)
            tags = []
            if db_id in rec_ids:
                tags.append("recommended")
            if db_id in dbs:
                tags.append("installed")
            label = f"{db.name} – {db.use} – about {dl.size}" + (f"  ({', '.join(tags)})" if tags else "")
            self.db_combo.addItem(label, db_id)
        i = self.db_combo.findData(current)
        self.db_combo.setCurrentIndex(max(i, 0))
        self._agreed()

    def _agreed(self, *_):
        on = self.agree.isChecked() and self.worker is None
        if self.agree.isChecked():
            self.store.set("ui", TERMS_KEY, True)
        self.prog_btn.setEnabled(on)
        self.db_btn.setEnabled(on)

    # ------------------------------------------------------------------ actions
    def _get_program(self):
        dl = S.program_download(self.key)
        if dl is None:
            self.progress_label.setText("For this system there is no single installer: the download page opens, "
                                        "with the versions for your system. After installing, press "
                                        "<b>Check again</b>.")
            QDesktopServices.openUrl(QUrl(S.ASTAP_PAGE))
            return
        self._start(dl, "program")

    def _get_database(self):
        db = S.get(self.db_combo.currentData())
        if db is None:
            return
        self._start(S.database_download(db, self.key), db.id)

    def _start(self, dl: S.Download, what: str):
        self.pending = what
        self.worker = _Downloader(dl.url, self)
        self.worker.progress.connect(self._progress)
        self.worker.done.connect(self._downloaded)
        self.worker.failed.connect(self._failed)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.cancel_btn.setVisible(True)
        self.progress_label.setText(f"Downloading {html.escape(dl.label)}" +
                                    (f" (about {dl.size})" if dl.size else "") + "…")
        self._agreed()
        self.worker.start()

    def _progress(self, done: int, total: int):
        if total:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(1000 * done / total))
            self.progress.setFormat(f"{done / 1e6:.0f} of {total / 1e6:.0f} MB")
        else:
            self.progress.setFormat(f"{done / 1e6:.0f} MB")

    def _finish(self):
        self.worker = None
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._agreed()

    def _cancel(self):
        if self.worker:
            self.worker.cancel.set()

    def _failed(self, msg: str):
        self._finish()
        if msg == "cancelled":
            self.progress_label.setText("Download cancelled.")
            return
        self.progress_label.setText(f"<span style='color:{theme.ERROR}'>The download didn't work: "
                                    f"{html.escape(msg)}.</span> The download page opens instead, so you can "
                                    "download it there. Afterwards press <b>Check again</b>.")
        QDesktopServices.openUrl(QUrl(S.ASTAP_PAGE))

    def _downloaded(self, path: str):
        self._finish()
        p = Path(path)
        self.changed = True
        if p.suffix.lower() == ".zip":                       # Linux without packages: unpack it ourselves
            folder = self._zip_folder()
            try:
                S.unpack_zip(p, folder)
            except Exception as exc:
                self._failed(f"the database could not be unpacked ({exc})")
                return
            from platesolver.plugins.solvers.astap import AstapSolver
            self.store.set(AstapSolver.section_key(), "database_folder", str(folder))
            self.progress_label.setText(f"<span style='color:{theme.OK}'>✓ Unpacked into {html.escape(str(folder))}"
                                        "</span> and set as the star database folder.")
            self.refresh()
            return
        if not self._protect_on_mac(p):
            self.progress_label.setText(f"The installer was not started. It is saved as {html.escape(str(p))}.")
            return
        self.before = set(self.dbs)
        try:
            S.start(p)
        except Exception as exc:
            self._failed(f"the installer could not be started ({exc}); it is saved as {p}")
            return
        what = "ASTAP" if self.pending == "program" else f"the {S.get(self.pending).name} database"
        self.progress_label.setText(
            f"The installer for {what} has been started. Follow its steps (keep the suggested folder, so ASTAP "
            "finds the database); Windows may ask for permission. When it has finished, press <b>Check again</b>."
            + ("<br>Tip: ASTAP still needs a star database – choose one in step 2." if self.pending == "program"
               and not self.dbs else ""))

    def _protect_on_mac(self, pkg: Path) -> bool:
        """macOS: if this database installer would remove another database, say so and offer to prevent it.
        Returns False when the user cancels."""
        if sys.platform != "darwin" or pkg.suffix.lower() != ".pkg" or self.pending == "program":
            return True
        clash = S.mac_clash(pkg, self.pending)
        if not clash:
            return True
        ident, others = clash
        names = ", ".join(o.upper() for o in others)
        new = S.get(self.pending).name if S.get(self.pending) else self.pending.upper()
        box = QMessageBox(self)
        box.setWindowTitle("Keep your other star databases")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"Installing {new} would remove the {names} database from this Mac.")
        box.setInformativeText(
            f"ASTAP's {new} installer has the same internal name (\"{ident}\") as the one that installed {names}, "
            "so macOS treats it as an update and removes the earlier files.\n\n"
            "PlateSolver can prevent this by telling macOS to forget the earlier install. No files are deleted; "
            "macOS asks for your password.")
        protect = box.addButton("Protect and install", QMessageBox.AcceptRole)
        anyway = box.addButton("Install anyway", QMessageBox.DestructiveRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(protect)
        box.exec()
        clicked = box.clickedButton()
        if clicked is anyway:
            return True
        if clicked is not protect:
            return False
        try:
            S.forget_receipt(ident)
        except Exception as exc:
            if str(exc) == "cancelled":
                return False
            QMessageBox.warning(self, "Keep your other star databases",
                                f"macOS could not forget the earlier install ({exc}). The installer was not "
                                f"started. In Terminal: sudo pkgutil --forget {ident}")
            return False
        return True

    def _zip_folder(self) -> Path:
        from platesolver.core.paths import app_data_dir
        if self.exe and self.exe.parent.exists():
            try:
                test = self.exe.parent / ".platesolver_write_test"
                test.write_text("")
                test.unlink()
                return self.exe.parent
            except OSError:
                pass
        return app_data_dir() / "astap databases"

    def closeEvent(self, event):
        if self.worker:
            self.worker.cancel.set()
            self.worker.wait(3000)
        S.refresh_settings(self.store)
        super().closeEvent(event)
