# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Plate solving with ASTAP (https://www.hnsky.org/astap.htm), installed locally.

The image is written to a temporary FITS file in the program's own pixel
orientation, so the solution ASTAP returns maps directly onto the image shown
on screen, whatever format the original file was.
"""
from __future__ import annotations

import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from platesolver.core.interfaces import Solver
from platesolver.core.models import ImageData, SolveResult
from platesolver.core.plugin import Cancelled, TaskContext
from platesolver.core.settings import ACTION, BOOL, CHOICE, FILE, FLOAT, FOLDER, INT, STR, SettingField

EXE_NAMES = ("astap_cli.exe", "astap.exe", "astap_cli", "astap")
DEFAULT_WINDOWS_PATH = r"C:\Program Files\astap\astap.exe"
KNOWN_DATABASES = {
    "d80": "D80 – large, for fields 0.15°–4°",
    "d50": "D50 – for fields 0.2°–10°",
    "d20": "D20 – for fields 0.3°–20°",
    "d05": "D05 – small, for fields 0.6°–20°",
    "v50": "V50 – variable-star photometry",
    "h18": "H18 (old format)",
    "h17": "H17 (old format)",
    "g05": "G05 – wide field",
    "w08": "W08 – very wide field (camera lenses, Milky Way)",
}
_DB_FILE = re.compile(r"^([dghvw]\d{2})[_.][0-9a-z_.]*$", re.IGNORECASE)
WIDE_DATABASES = ("w08", "g05")            # for very wide fields (camera lenses, Milky Way)
MEDIUM_DATABASES = ("d20", "d05", "g05", "w08")
NARROW_DATABASES = ("d50", "d80", "v50", "d20", "h18", "h17", "d05")   # telescope fields, best first


def candidate_paths() -> list[Path]:
    paths = []
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            paths.append(Path(base) / "astap")
            paths.append(Path(base) / "Programs" / "astap")
    paths += [Path(r"C:\Program Files\astap"), Path(r"C:\astap"),
              Path("/opt/astap"), Path("/usr/local/bin"), Path("/usr/bin"),
              Path("/Applications/ASTAP.app/Contents/MacOS")]
    return paths


def resolve_executable(setting: str | None) -> Path | None:
    """The setting may point at the program or at its folder."""
    if not setting:
        return None
    p = Path(str(setting).strip().strip('"')).expanduser()
    if p.is_dir():
        for name in EXE_NAMES:
            if (p / name).is_file():
                return p / name
        return None
    return p if p.is_file() else None


def detect_astap() -> str:
    """Best guess for the ASTAP program; used as the default setting."""
    for folder in candidate_paths():
        exe = resolve_executable(str(folder))
        if exe:
            return str(exe)
    for name in EXE_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return DEFAULT_WINDOWS_PATH if sys.platform == "win32" else ""


def find_databases(folder: Path | None) -> list[str]:
    """Star databases installed in a folder, e.g. ['d50']."""
    if not folder or not folder.is_dir():
        return []
    found = set()
    try:
        for f in folder.iterdir():
            m = _DB_FILE.match(f.name)
            if m:
                found.add(m.group(1).lower())
    except OSError:
        return []
    return sorted(found)


DB_SUBFOLDERS = ("databases", "database", "star databases", "stardatabases", "db")


# Where ASTAP's own installers put the star databases when they aren't next to the program:
# macOS database .pkg files install into /usr/local/opt/astap (the program stays in ASTAP.app),
# Linux .deb/.rpm packages into /opt/astap (the program is often reached through /usr/bin or /usr/local/bin);
# ASTAP itself falls back on /usr/share/astap/data when /opt/astap doesn't exist.
SYSTEM_DB_DIRS = (Path("/usr/local/opt/astap"), Path("/opt/astap"), Path("/usr/share/astap/data"))


def database_dir(exe_dir: Path | None) -> Path | None:
    """Where the star databases are when no folder is set: next to ASTAP, in a subfolder named
    e.g. 'Database' or 'databases', or where ASTAP's macOS/Linux installers put them.
    The folder with the most databases wins; next to ASTAP wins a tie."""
    candidates: list[Path] = []
    if exe_dir and exe_dir.is_dir():
        candidates.append(exe_dir)
        try:
            candidates += sorted(c for c in exe_dir.iterdir() if c.is_dir() and c.name.lower() in DB_SUBFOLDERS)
        except OSError:
            pass
    if sys.platform != "win32":
        for d in SYSTEM_DB_DIRS:
            if d not in candidates:
                candidates.append(d)
    best, count = None, 0
    for folder in candidates:
        n = len(find_databases(folder))
        if n > count:
            best, count = folder, n
    return best


def parse_ini(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            values[k.strip().upper()] = v.strip().strip("'\"")
    return values


def wcs_from_ini(values: dict[str, str]):
    """Build an astropy WCS from the keywords in ASTAP's .ini result file."""
    from astropy.wcs import WCS

    f = lambda k, d=None: float(values[k]) if k in values and values[k] != "" else d
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crpix = [f("CRPIX1"), f("CRPIX2")]
    w.wcs.crval = [f("CRVAL1"), f("CRVAL2")]
    if "CD1_1" in values:
        w.wcs.cd = np.array([[f("CD1_1"), f("CD1_2", 0.0)], [f("CD2_1", 0.0), f("CD2_2")]])
    else:
        c1, c2 = f("CDELT1"), f("CDELT2")
        rot = math.radians(f("CROTA2", f("CROTA1", 0.0)))
        w.wcs.cd = np.array([[c1 * math.cos(rot), -c2 * math.sin(rot)],
                             [c1 * math.sin(rot), c2 * math.cos(rot)]])
    w.wcs.set()
    return w


def validate_path(value) -> tuple[bool, str]:
    exe = resolve_executable(value)
    if not exe:
        if not value:
            return False, "Not set – point this at astap.exe or the ASTAP folder."
        return False, "ASTAP was not found there."
    folder = database_dir(exe.parent)
    dbs = find_databases(folder)
    if dbs:
        where = "" if folder == exe.parent else (f" (in the {folder.name} folder)" if folder.parent == exe.parent
                                                  else f" (in {folder})")
        return True, f"Found {exe.name}. Star databases{where}: {', '.join(d.upper() for d in dbs)}"
    return True, (f"Found {exe.name}, but no star database next to it or in a Database folder inside it. "
                  "Set the database folder below if it's installed elsewhere.")


def validate_db_folder(value) -> tuple[bool, str]:
    if not value:
        return True, ("Found automatically: the ASTAP folder, or a 'Database' or 'databases' folder inside it"
                      + ("" if sys.platform == "win32" else ", or /usr/local/opt/astap (macOS), /opt/astap or /usr/share/astap/data (Linux)")
                      + ".")
    folder = Path(str(value))
    if not folder.is_dir():
        return False, "Folder not found."
    dbs = find_databases(folder)
    return (True, "Star databases: " + ", ".join(d.upper() for d in dbs)) if dbs else \
        (False, "No ASTAP star database files in this folder.")


class AstapSolver(Solver):
    plugin_id = "astap"
    name = "ASTAP"
    description = ("Fast local plate solver. Needs ASTAP and at least one of its star databases "
                   "(e.g. D50) installed. Download from hnsky.org/astap.htm.")
    priority = 20
    retry_prepared = True

    def _stored(self, key: str):
        """A saved setting, without touching the defaults (settings_schema can't call self.setting)."""
        st = getattr(self.settings, "store", None)
        if st is not None and st.has(self.section_id, key):
            return st.get(self.section_id, key)
        return None

    def settings_schema(self):
        exe = resolve_executable(self._stored("path") or detect_astap())
        folder = str(self._stored("database_folder") or "").strip()
        dbs = find_databases(Path(folder) if folder else database_dir(exe.parent if exe else None))
        db_choices = [("", "Automatic (the best installed database for the field size)")]
        db_choices += [(d, KNOWN_DATABASES.get(d, d.upper()) + (" – installed" if d in dbs else ""))
                       for d in (dbs or KNOWN_DATABASES)]
        return [
            SettingField("setup", "Install ASTAP and star databases…", ACTION, ui=True, action=self._setup_action,
                         help="Shows what is installed and recommends a star database for your profile. Downloads "
                              "the official installers from ASTAP's site; the program and the databases are "
                              "separate, optional steps."),
            SettingField("path", "ASTAP program", FILE, detect_astap(),
                         file_filter="ASTAP (astap.exe astap_cli.exe astap astap_cli);;Programs (*.exe);;All files (*)",
                         help="astap.exe, astap_cli.exe or the folder they're in.",
                         validator=validate_path),
            SettingField("database_folder", "Star database folder", FOLDER, "",
                         help="Leave empty if the databases are in the ASTAP folder or in a folder called "
                              "Database or databases inside it – they're found automatically.",
                         validator=validate_db_folder),
            SettingField("database", "Star database", CHOICE, "", choices=db_choices,
                         help="Automatic picks the best installed database for the field size: D50/D80 (or "
                              "D20/D05) for telescope fields, W08/G05 for camera lenses and phones."),
            SettingField("search_radius", "Search radius (with position hint)", FLOAT, 30.0,
                         minimum=1, maximum=180, step=5, decimals=0, suffix="°",
                         help="How far from the position in the file header to search."),
            SettingField("blind_radius", "Search radius (no hint, blind solve)", FLOAT, 180.0,
                         minimum=1, maximum=180, step=10, decimals=0, suffix="°",
                         help="180° searches the whole sky. Slow with large databases."),
            SettingField("fov_override", "Field of view height", FLOAT, 0.0,
                         minimum=0, maximum=180, step=0.1, decimals=3, suffix="°",
                         help="0 = from the file / default equipment settings, or let ASTAP try."),
            SettingField("any_scale_retry", "If not solved, try again with any image scale", BOOL, True,
                         help="Only when the expected scale comes from Settings › Equipment, not from the image "
                              "(e.g. a drizzled, binned or resized image, or one taken with other equipment). "
                              "Slower."),
            SettingField("max_stars", "Maximum stars used", INT, 500, minimum=10, maximum=5000, step=50),
            SettingField("downsample", "Downsample", INT, 0, minimum=0, maximum=4,
                         help="0 = automatic. 2 or 3 speeds up large images."),
            SettingField("timeout", "Give up after", INT, 300, minimum=10, maximum=3600, step=30, suffix=" s"),
            SettingField("extra_args", "Extra command-line options", STR, "",
                         help="Passed to ASTAP as-is, for advanced use."),
            SettingField("keep_files", "Keep ASTAP's work files (for troubleshooting)", BOOL, False),
        ]

    def _setup_action(self, values: dict, parent) -> str:
        from platesolver.core import astap_setup
        from platesolver.core.profiles import ProfileManager
        from platesolver.ui.astap_setup_dialog import AstapSetupDialog
        store = self.settings.store
        dlg = AstapSetupDialog(store, ProfileManager(store).active(), parent)
        dlg.exec()
        exe, dbs = astap_setup.refresh_settings(store)
        return (f"ASTAP: {exe}" if exe else "ASTAP not found yet") + \
               (f" – star databases: {', '.join(d.upper() for d in dbs)}" if dbs else " – no star database yet")

    def executable(self) -> Path | None:
        return resolve_executable(self.setting("path"))

    def is_available(self) -> tuple[bool, str]:
        if self.executable() is None:
            return False, "ASTAP not found – set its location in Settings › Plate solvers › ASTAP"
        return True, ""

    # ------------------------------------------------------------------ solving
    def database_folder(self) -> Path | None:
        """The folder set in Settings, or else the one found next to ASTAP."""
        folder = str(self.setting("database_folder") or "").strip()
        if folder:
            return Path(folder)
        exe = self.executable()
        return database_dir(exe.parent) if exe else None

    def installed_databases(self) -> list[str]:
        return find_databases(self.database_folder())

    def auto_database(self, fov_deg: float) -> str:
        """With 'Automatic' selected: the best installed database for the field size.

        Always named explicitly, so ASTAP never falls back on a database it remembers from earlier
        (e.g. W08 after a wide-field session, which can't solve telescope images).
        """
        installed = self.installed_databases()
        if fov_deg and fov_deg >= 20:
            wanted = WIDE_DATABASES
        elif fov_deg and fov_deg >= 10:
            wanted = MEDIUM_DATABASES
        else:
            wanted = NARROW_DATABASES + ("g05",)
        return next((d for d in wanted if d in installed), "")

    def wide_field_advice(self, fov_deg: float) -> str:
        if fov_deg and fov_deg >= 10 and not any(d in self.installed_databases() for d in WIDE_DATABASES):
            return (f" – this is a wide-field image (about {fov_deg:.0f}° high); ASTAP needs its wide-field star "
                    f"database W08 (or G05) for that. Download it from hnsky.org/astap.htm and install it in "
                    f"the ASTAP folder")
        return ""

    def database_mismatch(self, db: str, fov_deg: float, image: ImageData) -> str:
        """Explain a star database that doesn't suit the expected field size."""
        db = (db or "").lower()
        if not fov_deg:
            return ""
        if db in WIDE_DATABASES and fov_deg < (15 if db == "w08" else 3):
            origin = ("Settings › ASTAP › Field of view height" if float(self.setting("fov_override") or 0)
                      else image.hints.scale_origin() or "the image")
            return (f" – the {db.upper()} database is for wide fields, but the expected field is only "
                    f"{fov_deg:.1f}° high (from {origin}). If this is a camera-lens or phone photo, that "
                    f"expected size is wrong: set the real field height under Settings › ASTAP › Field of "
                    f"view height, or better choose a smartphone or camera-lens profile in the toolbar")
        if db.startswith(("d", "v", "h")) and fov_deg >= 15:
            return (f" – the {db.upper()} database is for telescope fields up to about 10°, but this field is "
                    f"{fov_deg:.0f}° high. Install W08 and set Star database to Automatic")
        return ""

    def expected_fov(self, image: ImageData) -> float:
        return float(self.setting("fov_override") or 0) or (image.hints.fov_height_deg(image.height) or 0.0)

    def scale_is_a_guess(self, image: ImageData) -> bool:
        """True when the expected scale comes from Settings › Equipment rather than from the image itself."""
        if float(self.setting("fov_override") or 0):
            return False
        h = image.hints
        if h.scale_arcsec() is None:
            return True
        return any(str(v).startswith("Settings") for k, v in h.source.items()
                   if k in ("focal_length", "pixel_size", "scale"))

    def attempts(self, image: ImageData) -> list[tuple[float | None, bool]]:
        """(fov, blind) for each try: None = expected scale, 0.0 = any scale (ASTAP's automatic mode).

        Near a known position, trying any scale is quick, so it is always done. A whole-sky search with
        any scale is slow, so it is only done when the expected scale is a guess from Settings and
        there is no object hint.
        """
        h = image.hints
        fov = self.expected_fov(image)
        retry = bool(self.setting("any_scale_retry")) and 0 < fov < 10
        guess = self.scale_is_a_guess(image)
        tries: list[tuple[float | None, bool]] = []
        if h.has_position and h.position_exact:
            return [(None, False)]       # refining a known solution: no other scales, no whole-sky search
        if h.has_position:
            tries.append((None, False))
            if retry and (guess or h.position_hint):
                tries.append((0.0, False))
        if not h.has_position or h.position_hint:
            tries.append((None, True))
            # whole sky AND any scale is slow (minutes); with a hint it would only help if both the hint
            # and the scale were wrong, so it is left out then
            if retry and guess and not h.position_hint:
                tries.append((0.0, True))
        return tries

    def build_command(self, exe: Path, fits_path: Path, image: ImageData,
                      fov: float | None = None, blind: bool = False) -> tuple[list[str], str]:
        cmd = [str(exe), "-f", str(fits_path)]
        h = image.hints
        any_scale = fov == 0
        if fov is None:
            fov = self.expected_fov(image)
        cmd += ["-fov", f"{fov:.4f}"]
        if h.has_position and not blind:
            radius = max(1.0, fov * 0.25) if h.position_exact else float(self.setting("search_radius"))
            cmd += ["-ra", f"{h.ra_deg / 15.0:.6f}", "-spd", f"{h.dec_deg + 90.0:.6f}",
                    "-r", f"{radius:.1f}"]
            mode = (f"near {h.position_hint}" if h.position_hint else
                    f"near RA {h.ra_deg:.3f}°, Dec {h.dec_deg:+.3f}°")
        else:
            cmd += ["-r", f"{float(self.setting('blind_radius')):.1f}"]
            mode = ("blind (the whole sky, in case the object hint is wrong)" if h.has_position
                    else "blind (no position in the file)")
        cmd += ["-s", str(int(self.setting("max_stars"))), "-z", str(int(self.setting("downsample")))]
        db_folder = self.database_folder()
        if db_folder and (str(self.setting("database_folder") or "").strip() or db_folder != exe.parent):
            cmd += ["-d", str(db_folder)]
        db = str(self.setting("database") or "").strip() or self.auto_database(fov)
        if db:
            cmd += ["-D", db]
            mode += f", {db.upper()} star database" + ("" if str(self.setting("database") or "").strip()
                                                         else " (chosen automatically)")
        extra = str(self.setting("extra_args") or "").strip()
        if extra:
            cmd += shlex.split(extra, posix=(os.name != "nt"))
        if any_scale:
            mode += ", any image scale from about 10° down to 0.3° (slower)"
        elif fov:
            origin = ("Field of view height setting" if float(self.setting("fov_override") or 0)
                      else h.scale_origin())
            mode += f", field height {fov:.3f}°" + (f" (from {origin})" if origin else "")
        return cmd, mode

    def solve(self, image: ImageData, ctx: TaskContext) -> SolveResult:
        exe = self.executable()
        if exe is None:
            return SolveResult.failed("ASTAP not found", self.plugin_id, self.name)
        workdir = Path(tempfile.mkdtemp(prefix="platesolver_astap_"))
        try:
            fits_path = workdir / "image.fits"
            write_solver_fits(image, fits_path)
            fov = self.expected_fov(image)
            tries = self.attempts(image)
            ini = fits_path.with_suffix(".ini")
            for attempt, blind in tries:
                if attempt == 0.0 and fov:
                    ctx.log("ASTAP: " + ("the expected scale came from Settings › Equipment and may not fit this "
                                         "image (e.g. cropped and resized, drizzled, binned or other equipment)"
                                         if self.scale_is_a_guess(image) else "trying other scales near the hint")
                            + " – trying any image scale")
                cmd, mode = self.build_command(exe, fits_path, image, attempt, blind)
                ctx.log(f"ASTAP: solving {mode}")
                self.log.info("Command: %s", subprocess.list2cmdline(cmd))
                ini.unlink(missing_ok=True)
                t0 = time.monotonic()
                output = self._run(cmd, ctx, float(self.setting("timeout")))
                elapsed = time.monotonic() - t0
                if not ini.exists():
                    msg = "ASTAP produced no result file"
                    if output.strip():
                        msg += f": {output.strip().splitlines()[-1][:200]}"
                    return SolveResult.failed(msg, self.plugin_id, self.name)
                values = parse_ini(ini.read_text(errors="replace"))
                if values.get("PLTSOLVD", "F").upper() == "T":
                    break
                said = [l.strip() for l in output.splitlines() if l.strip()][-3:]
                if said:
                    ctx.log("ASTAP said: " + " | ".join(x[:160] for x in said))
            else:
                why = values.get("ERROR") or values.get("WARNING") or "no match found"
                db = str(self.setting("database") or "").strip() or self.auto_database(fov)
                advice = self.database_mismatch(db, fov, image) or self.wide_field_advice(fov)
                if len(tries) > 1:
                    advice += " (also tried with any image scale)"
                return SolveResult.failed(f"not solved ({why}){advice}", self.plugin_id, self.name)
            wcs = wcs_from_ini(values)
            res = SolveResult.from_wcs(wcs, image.width, image.height, self.plugin_id, self.name, elapsed)
            warning = values.get("WARNING", "")
            if "scale was inaccurate" in warning.lower():
                # harmless: ASTAP searched other scales and still solved. The pipeline explains why.
                res.message = "ASTAP had to correct the expected image scale, but the solution is good."
            elif warning:
                res.message = warning
            return res
        finally:
            if self.setting("keep_files"):
                ctx.log(f"ASTAP work files kept in {workdir}")
            else:
                shutil.rmtree(workdir, ignore_errors=True)

    def _run(self, cmd: list[str], ctx: TaskContext, timeout: float) -> str:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, creationflags=flags)
        except OSError as exc:
            raise RuntimeError(f"could not start ASTAP ({exc.strerror or exc})") from exc
        deadline = time.monotonic() + timeout
        while True:
            try:
                out, _ = proc.communicate(timeout=0.25)
                return (out or b"").decode(errors="replace")
            except subprocess.TimeoutExpired:
                if ctx.cancelled:
                    proc.kill()
                    proc.communicate()
                    raise Cancelled()
                if time.monotonic() > deadline:
                    proc.kill()
                    proc.communicate()
                    raise TimeoutError(f"ASTAP took longer than {timeout:.0f} s")


def write_solver_fits(image: ImageData, path: Path) -> None:
    """Mono 16-bit FITS of the image, rows in the same order as the display.

    Array row 0 becomes FITS row 1, so ASTAP's pixel coordinates are ours + 1,
    which is exactly how astropy's WCS (origin 0) interprets them.
    """
    from astropy.io import fits

    lum = image.luminance()
    lo, hi = np.percentile(lum[::4, ::4], [0.01, 99.99])
    if hi <= lo:
        lo, hi = float(lum.min()), float(lum.max()) or 1.0
    scaled = np.clip((lum - lo) / (hi - lo), 0, 1) * 65535.0
    hdu = fits.PrimaryHDU(scaled.astype(np.uint16))
    # No FOCALLEN/XPIXSZ here: the expected scale is passed with -fov. With "-fov 0" (any scale) ASTAP
    # would otherwise take the scale from these keywords and never try any other.
    hdu.writeto(path, overwrite=True)
