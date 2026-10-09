# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""The ASTAP install helper: download links, recommendations, downloading, finding the install afterwards."""
from __future__ import annotations

import io
import os
import threading
import zipfile
from pathlib import Path

import pytest

from platesolver.core import astap_setup as S
from platesolver.core.profiles import Profile
from platesolver.core.settings import SettingsStore
from platesolver.plugins.solvers import astap as A


def test_program_links_per_system():
    assert S.program_download("windows").url.endswith("/windows_installer/astap_setup.exe/download")
    assert "astap_M1.pkg" in S.program_download("mac_arm").url
    assert S.program_download("mac_intel").url.endswith("/astap.pkg/download")
    assert ".deb/download" in S.program_download("deb").url
    assert S.program_download("windows32") is None and S.program_download("linux") is None
    for key in ("windows", "mac_arm", "mac_intel", "deb"):
        assert S.program_download(key).url.startswith("https://sourceforge.net/projects/astap-program/files/")


def test_database_links_and_families():
    d50 = S.get("d50")
    assert S.database_download(d50, "windows").url.endswith("/star_databases/d50_star_database.exe/download")
    assert S.database_download(d50, "mac_arm").url.endswith("d50_star_database.pkg/download")
    assert S.database_download(d50, "deb").url.endswith("d50_star_database.deb/download")
    assert S.database_download(d50, "linux").url.endswith("d50_star_database.zip/download")
    assert "w08_star_database_mag08_astap" in S.database_download(S.get("w08"), "windows").url
    assert {d.id for d in S.DATABASES} >= {"d50", "d80", "d20", "d05", "w08", "g05"}
    assert S.get("nope") is None


def test_file_name_from_url():
    assert S.file_name_from_url(f"{S.SF}/macOS%20installer/astap_M1.pkg/download") == "astap_M1.pkg"
    assert S.file_name_from_url(f"{S.SF}/star_databases/d50_star_database.exe/download") == "d50_star_database.exe"


def _profile(kind, **eq):
    return Profile("p", "P", kind, {"equipment": eq})


def test_recommendations_follow_the_profile():
    assert S.recommended(None)[0][0] == "d50"
    assert S.recommended(_profile("phone"))[0][0] == "w08"
    assert [r[0] for r in S.recommended(_profile("dslr_lens"))] == ["w08", "d50"]
    # 1000 mm, 3.76 µm pixels, 4000 px high → about 0.86° → D50
    assert S.recommended(_profile("telescope", focal_length=1000, pixel_size=3.76, sensor_height=4000))[0][0] == "d50"
    # 4000 mm, 2.4 µm, 2000 px → about 0.07° → D80
    assert S.recommended(_profile("telescope", focal_length=4000, pixel_size=2.4, sensor_height=2000))[0][0] == "d80"
    # 50 mm, 4.3 µm, 4000 px → about 20° → W08
    assert S.recommended(_profile("telescope", focal_length=50, pixel_size=4.3, sensor_height=4000))[0][0] == "w08"
    assert S.recommended(_profile("telescope"))[0][0] == "d50"


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, ctype="application/octet-stream", length=None):
        super().__init__(data)
        self.headers = {"Content-Type": ctype, "Content-Length": str(len(data) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_download_saves_file_and_reports_progress(tmp_path, monkeypatch):
    data = os.urandom(3 * (1 << 20) + 17)
    monkeypatch.setattr(S.urllib.request, "urlopen", lambda req, timeout=0: _Resp(data))
    seen = []
    out = S.download(f"{S.SF}/star_databases/d05_star_database.exe/download", tmp_path,
                     progress=lambda d, t: seen.append((d, t)))
    assert out == tmp_path / "d05_star_database.exe" and out.read_bytes() == data
    assert seen[-1] == (len(data), len(data)) and len(seen) == 4
    assert not list(tmp_path.glob("*.part"))


def test_download_refuses_web_pages_cancel_and_short_files(tmp_path, monkeypatch):
    monkeypatch.setattr(S.urllib.request, "urlopen", lambda req, timeout=0: _Resp(b"<html>", "text/html"))
    with pytest.raises(S.DownloadError, match="web page"):
        S.download("https://example.org/x.exe", tmp_path)

    monkeypatch.setattr(S.urllib.request, "urlopen", lambda req, timeout=0: _Resp(b"abc", length=100))
    with pytest.raises(S.DownloadError, match="incomplete"):
        S.download("https://example.org/x.exe", tmp_path)

    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(S.urllib.request, "urlopen", lambda req, timeout=0: _Resp(b"abc"))
    with pytest.raises(S.DownloadError, match="cancelled"):
        S.download("https://example.org/x.exe", tmp_path, cancel=stop)
    assert not list(tmp_path.iterdir())

    def boom(req, timeout=0):
        raise OSError("no network")
    monkeypatch.setattr(S.urllib.request, "urlopen", boom)
    with pytest.raises(S.DownloadError, match="no network"):
        S.download("https://example.org/x.exe", tmp_path)


def test_unpack_zip(tmp_path):
    z = tmp_path / "d05.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("d05_0101.1476", b"x")
        f.writestr("d05_0102.1476", b"y")
    names = S.unpack_zip(z, tmp_path / "db")
    assert len(names) == 2 and A.find_databases(tmp_path / "db") == ["d05"]


def test_refresh_settings_finds_a_new_install(tmp_path, monkeypatch):
    astap_dir = tmp_path / "astap"
    (astap_dir / "Database").mkdir(parents=True)
    exe = astap_dir / "astap_cli"
    exe.write_text("")
    (astap_dir / "Database" / "d50_0101.1476").write_text("")
    monkeypatch.setattr(A, "detect_astap", lambda: str(exe))
    store = SettingsStore(tmp_path / "s.json")
    key = A.AstapSolver.section_key()
    store.set(key, "path", str(tmp_path / "missing" / "astap.exe"))     # an old, invalid setting
    found, dbs = S.refresh_settings(store)
    assert found == exe and dbs == ["d50"] and store.get(key, "path") == str(exe)

    # a valid setting is never replaced
    other = tmp_path / "other" / "astap"
    other.parent.mkdir()
    other.write_text("")
    store.set(key, "path", str(other))
    found, _ = S.refresh_settings(store)
    assert found == other and store.get(key, "path") == str(other)


def test_setup_dialog_basics(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(A, "detect_astap", lambda: "")
    from platesolver.ui.astap_setup_dialog import AstapSetupDialog
    store = SettingsStore(tmp_path / "s.json")
    store.set(A.AstapSolver.section_key(), "path", "")
    dlg = AstapSetupDialog(store, _profile("phone"))
    # buttons wait for the disclaimer to be accepted, and the choice is remembered
    assert not dlg.prog_btn.isEnabled() and not dlg.db_btn.isEnabled()
    assert "not found" in dlg.prog_status.text()
    assert dlg.db_combo.currentData() == "w08" and "recommended" in dlg.db_combo.currentText()
    assert dlg.db_combo.count() == len(S.DATABASES)
    dlg.agree.setChecked(True)
    assert dlg.prog_btn.isEnabled() and dlg.db_btn.isEnabled() and store.get("ui", "astap_terms_ok")
    again = AstapSetupDialog(store, None)
    assert again.agree.isChecked() and again.db_combo.currentData() == "d50"
    dlg.close()
    again.close()
    app.processEvents()


def test_databases_in_system_folder_are_found(tmp_path, monkeypatch):
    """macOS database installers use /usr/local/opt/astap while the program stays in ASTAP.app."""
    app = tmp_path / "ASTAP.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "astap").write_text("")
    system = tmp_path / "usr_local_opt_astap"
    system.mkdir()
    (system / "d50_0101.1476").write_text("")
    monkeypatch.setattr(A, "SYSTEM_DB_DIRS", (system,))
    monkeypatch.setattr(A.sys, "platform", "darwin")
    assert A.database_dir(app) == system
    ok, msg = A.validate_path(str(app / "astap"))
    assert ok and "D50" in msg and str(system) in msg
    # databases next to the program still win a tie
    (app / "d50_0101.1476").write_text("")
    assert A.database_dir(app) == app
    # without a program, the system folder is still found
    assert A.database_dir(None) == system
    # Windows never looks there
    monkeypatch.setattr(A.sys, "platform", "win32")
    assert A.database_dir(tmp_path / "nothing") is None


# --------------------------------------------------------------------------- macOS package clashes
def _make_pkg(path: Path, identifier: str, nested: bool = True, compress: bool = True) -> Path:
    """A minimal macOS .pkg (xar archive) holding just a PackageInfo, like ASTAP's database installers."""
    import struct
    import zlib
    info = f'<?xml version="1.0"?><pkg-info identifier="{identifier}" version="1.0" install-location="/usr/local/opt/astap"/>'.encode()
    stored = zlib.compress(info) if compress else info
    enc = "application/x-gzip" if compress else "application/octet-stream"
    data = (f'<data><length>{len(stored)}</length><offset>20</offset><size>{len(info)}</size>'
            f'<encoding style="{enc}"/></data>')
    leaf = f'<file id="2"><name>PackageInfo</name><type>file</type>{data}</file>'
    body = f'<file id="1"><name>x.pkg</name><type>directory</type>{leaf}</file>' if nested else leaf
    toc = (f'<?xml version="1.0"?><xar><toc><checksum style="sha1"><offset>0</offset><size>20</size></checksum>'
           f'{body}</toc></xar>').encode()
    ztoc = zlib.compress(toc)
    path.write_bytes(b"xar!" + struct.pack(">HHQQI", 28, 1, len(ztoc), len(toc), 1) + ztoc + b"\0" * 20 + stored)
    return path


class _Run:
    """Stands in for pkgutil/osascript."""
    def __init__(self, receipts: dict):
        self.receipts, self.calls = receipts, []

    def __call__(self, cmd, **kw):
        import subprocess
        self.calls.append(cmd)
        if cmd[1] == "--files":
            files = self.receipts.get(cmd[2])
            return subprocess.CompletedProcess(cmd, 0 if files is not None else 1, "\n".join(files or []), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def test_pkg_identifier_reads_the_installer(tmp_path):
    assert S.pkg_identifier(_make_pkg(tmp_path / "d50_star_database.pkg", "d05")) == "d05"
    assert S.pkg_identifier(_make_pkg(tmp_path / "flat.pkg", "v50", nested=False, compress=False)) == "v50"
    (tmp_path / "junk.pkg").write_bytes(b"not a package")
    assert S.pkg_identifier(tmp_path / "junk.pkg") is None
    assert S.pkg_identifier(tmp_path / "missing.pkg") is None


def test_mac_clash_finds_databases_that_would_be_removed(tmp_path):
    d50 = _make_pkg(tmp_path / "d50_star_database.pkg", "d05")      # ASTAP's D50 package uses "d05"
    d05 = _make_pkg(tmp_path / "d05_star_database.pkg", "d05")
    owned_by_d05 = ["d05_0101.1476", "d05_0102.1476", "deep_sky.csv", "hyperleda.csv"]
    run = _Run({"d05": owned_by_d05})
    assert S.mac_clash(d50, "d50", run) == ("d05", ["d05"])        # installing D50 would remove D05
    assert S.mac_clash(d05, "d05", run) is None                    # reinstalling D05 removes nothing else
    assert S.mac_clash(d50, "d50", _Run({})) is None               # no earlier install recorded
    g05 = _make_pkg(tmp_path / "g05_star_database.pkg", "v50")
    assert S.mac_clash(g05, "g05", _Run({"v50": ["v50_0101.1476"]})) == ("v50", ["v50"])
    assert S.receipt_databases("bad id; rm -rf /", run) is None    # odd identifiers are never passed on


def test_forget_receipt(tmp_path):
    run = _Run({})
    S.forget_receipt("d05", run)
    assert run.calls[-1][0] == "/usr/bin/osascript" and "pkgutil --forget d05" in run.calls[-1][2]
    assert "administrator privileges" in run.calls[-1][2]
    with pytest.raises(ValueError):
        S.forget_receipt('d05" & do shell script "evil', run)

    def refused(cmd, **kw):
        import subprocess
        return subprocess.CompletedProcess(cmd, 1, "", "execution error: User canceled. (-128)")
    with pytest.raises(RuntimeError, match="cancelled"):
        S.forget_receipt("d05", refused)


def test_downloads_go_to_the_downloads_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(S.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "Downloads").mkdir()
    assert S.downloads_dir() == tmp_path / "Downloads"


def test_dialog_reports_a_database_that_disappeared(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(A, "detect_astap", lambda: "")
    from platesolver.ui.astap_setup_dialog import AstapSetupDialog
    store = SettingsStore(tmp_path / "s.json")
    store.set(A.AstapSolver.section_key(), "path", "")
    dlg = AstapSetupDialog(store, None)
    dlg.before = {"d50"}
    dlg.refresh()
    assert "D50 is no longer found" in dlg.db_status.text()
    dlg.close()
    app.processEvents()
