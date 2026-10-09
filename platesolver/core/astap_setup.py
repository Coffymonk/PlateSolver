# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Helping the user install ASTAP and its star databases (third-party software from hnsky.org).

Nothing is installed silently: PlateSolver downloads the official installer from ASTAP's download site
(SourceForge, linked from hnsky.org) and starts it, so the user sees ASTAP's own installer and licence.
The program and the databases are separate, optional steps.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ASTAP_PAGE = "https://www.hnsky.org/astap.htm"
SF = "https://sourceforge.net/projects/astap-program/files"


@dataclass(frozen=True)
class Download:
    label: str
    url: str
    size: str = ""          # approximate, for the user ("870 MB")


def platform_key() -> str:
    """'windows', 'windows32', 'mac_arm', 'mac_intel', 'deb', 'rpm', 'linux' (anything else)."""
    if sys.platform == "win32":
        return "windows" if platform.machine().endswith("64") or sys.maxsize > 2 ** 32 else "windows32"
    if sys.platform == "darwin":
        return "mac_arm" if platform.machine() in ("arm64", "aarch64") else "mac_intel"
    if Path("/etc/debian_version").exists():
        return "deb"
    try:
        osr = Path("/etc/os-release").read_text().lower()
    except OSError:
        osr = ""
    if any(k in osr for k in ("fedora", "suse", "rhel", "centos")):
        return "rpm"
    return "linux"


def _arch() -> str:
    return "aarch64" if platform.machine().lower() in ("aarch64", "arm64") else "amd64"


def program_download(key: str | None = None) -> Download | None:
    """The ASTAP installer for this computer, or None when only the download page fits."""
    key = key or platform_key()
    if key == "windows":
        return Download("ASTAP for Windows (installer)", f"{SF}/windows_installer/astap_setup.exe/download", "about 15 MB")
    if key == "mac_arm":
        return Download("ASTAP for Mac with Apple silicon (M1 and newer)", f"{SF}/macOS%20installer/astap_M1.pkg/download")
    if key == "mac_intel":
        return Download("ASTAP for Mac with Intel processor", f"{SF}/macOS%20installer/astap.pkg/download")
    if key == "deb":
        return Download("ASTAP for Debian/Ubuntu (.deb package)", f"{SF}/linux_installer/astap_{_arch()}.deb/download")
    if key == "rpm" and _arch() == "amd64":
        return Download("ASTAP for Fedora/openSUSE (.rpm package)", f"{SF}/linux_installer/astap_amd64.rpm/download")
    return None           # Windows 32-bit (zip), Arch and others: the download page explains the options


@dataclass(frozen=True)
class Database:
    id: str
    name: str
    use: str
    sizes: dict          # platform family -> approximate size
    files: dict          # platform family -> file name on SourceForge


def _db(id_, name, use, size_win, size_mac, size_deb, base):
    return Database(id_, name, use, {"windows": size_win, "mac": size_mac, "deb": size_deb, "zip": size_deb},
                    {"windows": f"{base}.exe", "mac": f"{base}.pkg", "deb": f"{base}.deb", "zip": f"{base}.zip"})


DATABASES = [
    _db("d50", "D50", "telescopes and long lenses, fields 0.2–10° (most common choice)",
        "870 MB", "940 MB", "870 MB", "d50_star_database"),
    _db("d80", "D80", "long focal lengths, small fields 0.15–4°", "1.2 GB", "1.3 GB", "1.2 GB", "d80_star_database"),
    _db("d20", "D20", "fields 0.3–20°, smaller than D50", "390 MB", "430 MB", "390 MB", "d20_star_database"),
    _db("d05", "D05", "fields 0.6–20°, the smallest telescope database", "100 MB", "140 MB", "100 MB",
        "d05_star_database"),
    _db("w08", "W08", "phones and camera lenses, very wide fields over about 10°", "0.6 MB", "0.3 MB", "0.3 MB",
        "w08_star_database_mag08_astap"),
    _db("g05", "G05", "wide fields (alternative to W08, with fainter stars)", "100 MB", "140 MB", "100 MB",
        "g05_star_database"),
]


def family(key: str | None = None) -> str:
    key = key or platform_key()
    return {"windows": "windows", "windows32": "windows", "mac_arm": "mac", "mac_intel": "mac",
            "deb": "deb"}.get(key, "zip")


def database_download(db: Database, key: str | None = None) -> Download:
    fam = family(key)
    kind = {"windows": "installer", "mac": "installer", "deb": "package"}.get(fam, "zip file to unpack")
    return Download(f"{db.name} star database ({kind})", f"{SF}/star_databases/{db.files[fam]}/download",
                    db.sizes[fam])


def get(db_id: str) -> Database | None:
    return next((d for d in DATABASES if d.id == db_id), None)


# --------------------------------------------------------------------------- what fits the profile
def recommended(profile) -> list[tuple[str, str]]:
    """Databases to suggest for a profile: [(id, reason)], most important first."""
    if profile is None:
        return [("d50", "the usual choice for telescopes"), ("w08", "for phone and camera-lens photos")]
    kind = profile.kind
    e = profile.values.get("equipment", {})
    if kind == "phone":
        return [("w08", "phone photos cover 50–110° of sky")]
    if kind == "dslr_lens":
        return [("w08", "camera lenses up to about 150 mm give fields wider than 10°"),
                ("d50", "for long lenses with fields under 10°")]
    fl, px, sh = float(e.get("focal_length") or 0), float(e.get("pixel_size") or 0), float(e.get("sensor_height") or 0)
    if fl and px and sh:
        fov = 206.265 * px * sh / fl / 3600.0
        if fov < 0.3:
            return [("d80", f"your field is only about {fov:.2f}° high")]
        if fov > 10:
            return [("w08", f"your field is about {fov:.0f}° high")]
        return [("d50", f"your field is about {fov:.1f}° high")]
    return [("d50", "the usual choice for telescopes")]


# --------------------------------------------------------------------------- downloading and starting
class DownloadError(RuntimeError):
    pass


def file_name_from_url(url: str) -> str:
    parts = [p for p in urllib.parse.urlsplit(url).path.split("/") if p]
    if parts and parts[-1] == "download":
        parts = parts[:-1]
    return urllib.parse.unquote(parts[-1]) if parts else "download"


def downloads_dir() -> Path:
    """The user's Downloads folder (so installers are easy to find and run again), else a temporary folder."""
    d = Path.home() / "Downloads"
    if d.is_dir() and os.access(d, os.W_OK):
        return d
    return Path(tempfile.mkdtemp(prefix="platesolver_astap_"))


def download(url: str, folder: Path | None = None, progress: Callable[[int, int], None] = lambda done, total: None,
             cancel: threading.Event | None = None, timeout: float = 60) -> Path:
    """Download a file (following SourceForge's redirects to a mirror). Returns the saved file."""
    from platesolver.core.net import USER_AGENT
    folder = Path(folder or downloads_dir())
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / file_name_from_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "text/html" in ctype:
                raise DownloadError("the download site answered with a web page instead of the file")
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            part = target.with_name(target.name + ".part")
            with open(part, "wb") as fh:
                while True:
                    if cancel is not None and cancel.is_set():
                        fh.close()
                        part.unlink(missing_ok=True)
                        raise DownloadError("cancelled")
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    progress(done, total)
            if total and done < total:
                part.unlink(missing_ok=True)
                raise DownloadError("the download was incomplete")
            part.replace(target)
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(f"the download failed ({exc})") from exc
    return target


def start(path: Path) -> None:
    """Open a downloaded installer/package with the system, like double-clicking it."""
    if sys.platform == "win32":
        os.startfile(str(path))                       # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def unpack_zip(path: Path, folder: Path) -> list[str]:
    """For systems without an installer: unpack a database zip into the database folder."""
    import zipfile
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        z.extractall(folder)
        return z.namelist()


# --------------------------------------------------------------------------- after installing
def refresh_settings(store) -> tuple[Path | None, list[str]]:
    """Find ASTAP (and its databases) after an install and fill in Settings › ASTAP › program if needed."""
    from platesolver.plugins.solvers import astap as A
    section = A.AstapSolver.section_key()
    current = store.get(section, "path") if store.has(section, "path") else ""
    exe = A.resolve_executable(current) if current else None
    if exe is None:
        found = A.resolve_executable(A.detect_astap())
        if found is not None:
            store.set(section, "path", str(found))
            exe = found
    folder = str(store.get(section, "database_folder") or "") if store.has(section, "database_folder") else ""
    dbs = A.find_databases(Path(folder) if folder else A.database_dir(exe.parent if exe else None))
    return exe, dbs


# --------------------------------------------------------------------------- macOS: database packages that clash
# macOS remembers which files each installer package (by its identifier) installed, and installing a package
# with an identifier it already knows counts as an update: files of the "old version" that the new one doesn't
# contain are removed. Some of ASTAP's database packages share an identifier (d50_star_database.pkg is "d05",
# g05_star_database.pkg is "v50"), so installing one database removes another. Telling macOS to forget the
# earlier record ("pkgutil --forget") deletes nothing and keeps both.

_ID_OK = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_DB_IN_PATH = re.compile(r"(?:^|/)([dghvw]\d{2})_", re.IGNORECASE)
PKGUTIL = "/usr/sbin/pkgutil"


def pkg_identifier(pkg: Path) -> str | None:
    """Read the package identifier from a macOS .pkg (a xar archive) without installing or unpacking it."""
    try:
        with open(pkg, "rb") as f:
            head = f.read(28)
            if len(head) < 28 or head[:4] != b"xar!":
                return None
            hsize, _ver, toc_len, _toc_raw, _alg = struct.unpack(">HHQQI", head[4:28])
            f.seek(hsize)
            toc = ET.fromstring(zlib.decompress(f.read(toc_len)))
            heap = hsize + toc_len
            for node in toc.iter("file"):
                if (node.findtext("name") or "") != "PackageInfo" or node.find("data") is None:
                    continue
                data = node.find("data")
                f.seek(heap + int(data.findtext("offset")))
                raw = f.read(int(data.findtext("length")))
                style = (data.find("encoding").get("style", "") if data.find("encoding") is not None else "")
                if "gzip" in style or "zlib" in style:
                    raw = zlib.decompress(raw)
                ident = ET.fromstring(raw).get("identifier")
                if ident:
                    return ident
    except (OSError, ValueError, struct.error, zlib.error, ET.ParseError, TypeError):
        return None
    return None


def receipt_databases(identifier: str, run=subprocess.run) -> set[str] | None:
    """Databases whose files macOS has recorded under this package identifier; None if there is no record."""
    if not _ID_OK.match(identifier):
        return None
    try:
        r = run([PKGUTIL, "--files", identifier], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return {m.group(1).lower() for line in r.stdout.splitlines() if (m := _DB_IN_PATH.search(line))}


def mac_clash(pkg: Path, db_id: str, run=subprocess.run) -> tuple[str, list[str]] | None:
    """(identifier, databases that would be removed) when installing this database package would remove
    other databases, else None."""
    ident = pkg_identifier(pkg)
    if not ident or not _ID_OK.match(ident):
        return None
    owned = receipt_databases(ident, run)
    if not owned:
        return None
    others = sorted(owned - {db_id.lower()})
    return (ident, others) if others else None


def forget_receipt(identifier: str, run=subprocess.run) -> None:
    """Ask macOS to forget a package record (shows the system's password prompt). Deletes no files."""
    if not _ID_OK.match(identifier):
        raise ValueError(f"unexpected package identifier {identifier!r}")
    script = f'do shell script "{PKGUTIL} --forget {identifier}" with administrator privileges'
    r = run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        msg = (r.stderr or "").strip()
        raise RuntimeError("cancelled" if "-128" in msg or "canceled" in msg.lower() else (msg or "it didn't work"))
