# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Faint galaxies from ASTAP's HyperLeda database (hyperleda.csv, about 5 million galaxies).

The file is ASTAP's optional "HyperLeda" annotation database (download from hnsky.org/astap.htm).
It isn't bundled: it's large and comes with ASTAP. On first use the CSV is converted to a compact
index in PlateSolver's cache folder; after that a field is looked up in a fraction of a second.

File format (ASTAP deep-sky format), one object per line after two header lines:
    RA [0..864000 = 0.1 s of time], Dec [-324000..324000 = arc-seconds], name, length [0.1′],
    width [0.1′], orientation [°]
Lines are sorted from bright to faint, so the line number serves as a brightness rank. Lines with
only a position (no name or size) are skipped, since there is nothing to label them with.

HyperLeda: Makarov et al. 2014, A&A 570, A13, http://atlas.obs-hp.fr/hyperleda/
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np

from platesolver.core.interfaces import CatalogProvider
from platesolver.core.models import ImageData, SkyObject, SolveResult
from platesolver.core.plugin import TaskContext
from platesolver.core.settings import BOOL, FILE, FLOAT, INT, SettingField

INDEX_VERSION = 2
FILE_NAMES = ("hyperleda.csv", "HyperLeda.csv", "HYPERLEDA.CSV")
_ARRAYS = ("dec", "ra", "size", "width", "pa", "rank", "name_off", "names")

_NGC_IC = re.compile(r"^(NGC|IC)\d")

_index_cache: dict[str, dict] = {}


# --------------------------------------------------------------------------- finding the file
def candidate_files(astap_setting: str | None = None, db_folder: str | None = None) -> list[Path]:
    """Where ASTAP's installer may have put hyperleda.csv."""
    from platesolver.plugins.solvers import astap

    folders: list[Path] = []
    if db_folder:
        folders.append(Path(db_folder))
    exe = astap.resolve_executable(astap_setting) if astap_setting else None
    exe = exe or astap.resolve_executable(astap.detect_astap())
    if exe:
        folders.append(exe.parent)
    folders += astap.candidate_paths()
    folders += list(astap.SYSTEM_DB_DIRS)          # macOS: /usr/local/opt/astap
    out: list[Path] = []
    for f in folders:
        out.append(f)
        out += [f / sub for sub in ("databases", "Databases", "database", "Database")]
    seen, files = set(), []
    for folder in out:
        for name in FILE_NAMES:
            p = folder / name
            if str(p).lower() not in seen:
                seen.add(str(p).lower())
                files.append(p)
    return files


def validate_file(value) -> tuple[bool, str]:
    if not value:
        return True, "Looked for automatically in the ASTAP folder and its Database/databases folder."
    p = Path(str(value).strip().strip('"'))
    if not p.is_file():
        return False, "File not found."
    try:
        with open(p, encoding="latin-1") as fh:
            first = fh.readline()
    except OSError as exc:
        return False, f"Can't read the file ({exc})."
    if "HyperLeda" not in first and "hyperleda" not in p.name.lower():
        return False, "This doesn't look like ASTAP's hyperleda.csv."
    return True, "OK – " + first.split(".")[0].strip()[:90]


# --------------------------------------------------------------------------- names
_SPACE_AFTER = re.compile(r"^(PGC|UGCA|UGC|ESO|IC|NGC|CGCG|KUG|MRK|IRAS|FGC|LEDA|AGC|DDO|KIG|VV|ARP|UM)(?=[\d+\-])")
_J_NAMES = re.compile(r"^(SDSS|2MASX|2MASS|6DF|GALEX|WISEA|HIPASS|APMUKS\(BJ\))J", re.IGNORECASE)


def pretty_name(raw: str) -> str:
    """'PGC104548' -> 'PGC 104548', 'SDSSJ1001+41' -> 'SDSS J1001+41', 'NGC0253' -> 'NGC 253',
    'SAGITTARIUS_DWARF_SPHEROIDAL' -> 'Sagittarius Dwarf Spheroidal'."""
    if "_" in raw and raw.replace("_", "").isalpha():
        return raw.replace("_", " ").title()
    m = _J_NAMES.match(raw)
    if m:
        return f"{raw[:m.end() - 1]} J{raw[m.end():]}"
    m = _SPACE_AFTER.match(raw)
    if m:
        prefix, rest = raw[:m.end()], raw[m.end():]
        if prefix in ("PGC", "UGC", "NGC", "IC", "UGCA") and rest.isdigit():
            rest = str(int(rest))
        return f"{prefix} {rest}"
    return raw


def simbad_forms(pretty: str) -> list[str]:
    """Spellings SIMBAD may use for a HyperLeda name."""
    forms = [pretty]
    if pretty.startswith("PGC "):
        forms.append("LEDA " + pretty[4:])
    m = re.match(r"^ESO (\d+)-(.*)$", pretty)
    if m:
        forms.append(f"ESO {int(m.group(1))}-{m.group(2)}")
    if pretty.startswith("MCG") and pretty[-1:].isalpha():
        forms += [pretty[:-1] + pretty[-1].lower(), pretty[:-1]]
    return list(dict.fromkeys(forms))


# --------------------------------------------------------------------------- index
def _index_dir(csv_path: Path) -> Path:
    from platesolver.core.paths import cache_dir

    st = csv_path.stat()
    return cache_dir() / f"hyperleda-v{INDEX_VERSION}-{st.st_size}-{int(st.st_mtime)}"


def build_index(csv_path: Path, out_dir: Path, progress=None) -> int:
    """Convert hyperleda.csv to numpy arrays sorted by declination. Returns the number of objects."""
    ra, dec, size, width, pa, rank, names = [], [], [], [], [], [], []
    with open(csv_path, encoding="latin-1") as fh:
        for i, line in enumerate(fh):
            if i < 2:
                continue
            parts = line.rstrip("\r\n").split(",")
            if len(parts) < 4 or not parts[2]:
                continue
            try:
                r, d = float(parts[0]) / 2400.0, float(parts[1]) / 3600.0
                ln = float(parts[3]) / 10.0
                wd = float(parts[4]) / 10.0 if len(parts) > 4 and parts[4] else ln
                an = float(parts[5]) if len(parts) > 5 and parts[5] else math.nan
            except ValueError:
                continue
            ra.append(r)
            dec.append(d)
            size.append(ln)
            width.append(wd)
            pa.append(an)
            rank.append(i - 1)
            names.append(parts[2])
            if progress and len(names) % 200000 == 0:
                progress(len(names))
    order = np.argsort(np.asarray(dec, np.float64), kind="stable")
    blob = "\n".join(names[k] for k in order).encode("latin-1")
    lengths = np.fromiter((len(names[k]) + 1 for k in order), np.int64, len(order))
    name_off = np.concatenate([[0], np.cumsum(lengths)])
    arrays = {
        "dec": np.asarray(dec, np.float32)[order], "ra": np.asarray(ra, np.float32)[order],
        "size": np.asarray(size, np.float32)[order], "width": np.asarray(width, np.float32)[order],
        "pa": np.asarray(pa, np.float32)[order], "rank": np.asarray(rank, np.int32)[order],
        "name_off": name_off, "names": np.frombuffer(blob, np.uint8),
    }
    tmp = out_dir.with_name(out_dir.name + ".part")
    tmp.mkdir(parents=True, exist_ok=True)
    for key, arr in arrays.items():
        np.save(tmp / f"{key}.npy", arr)
    (tmp / "info.json").write_text(json.dumps({"source": str(csv_path), "objects": int(len(order)),
                                               "version": INDEX_VERSION}))
    if out_dir.exists():
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)
    tmp.rename(out_dir)
    return int(len(order))


def load_index(csv_path: Path, log=None) -> dict:
    out_dir = _index_dir(csv_path)
    key = str(out_dir)
    if key in _index_cache:
        return _index_cache[key]
    if not (out_dir / "info.json").exists():
        if log:
            log("HyperLeda: preparing the galaxy database for fast lookups (only the first time, "
                "takes about half a minute)…")
        n = build_index(csv_path, out_dir)
        if log:
            log(f"HyperLeda: {n:,} named galaxies indexed".replace(",", " "))
        for old in out_dir.parent.glob("hyperleda-v*"):
            if old != out_dir and old.is_dir():
                import shutil
                shutil.rmtree(old, ignore_errors=True)
    idx = {k: np.load(out_dir / f"{k}.npy", mmap_mode="r") for k in _ARRAYS}
    _index_cache[key] = idx
    return idx


def name_at(idx: dict, i: int) -> str:
    a, b = int(idx["name_off"][i]), int(idx["name_off"][i + 1]) - 1
    return bytes(idx["names"][a:b]).decode("latin-1")


def cone(idx: dict, ra_deg: float, dec_deg: float, radius_deg: float) -> np.ndarray:
    """Indices of the objects within radius_deg of a position."""
    dec_arr = idx["dec"]
    lo = int(np.searchsorted(dec_arr, dec_deg - radius_deg, "left"))
    hi = int(np.searchsorted(dec_arr, dec_deg + radius_deg, "right"))
    if hi <= lo:
        return np.empty(0, np.int64)
    d = np.radians(np.asarray(dec_arr[lo:hi]))
    r = np.radians(np.asarray(idx["ra"][lo:hi]))
    d0, r0 = math.radians(dec_deg), math.radians(ra_deg)
    cosang = np.sin(d) * math.sin(d0) + np.cos(d) * math.cos(d0) * np.cos(r - r0)
    return lo + np.nonzero(cosang >= math.cos(math.radians(radius_deg)))[0]


# --------------------------------------------------------------------------- plugin
class HyperLedaCatalog(CatalogProvider):
    plugin_id = "hyperleda"
    name = "HyperLeda galaxies (from ASTAP)"
    description = ("Adds faint galaxies from ASTAP's optional HyperLeda database (hyperleda.csv, about 5 million "
                   "galaxies), so background galaxies in deep images get names too. Install it from "
                   "hnsky.org/astap.htm; it's found automatically in the ASTAP folder. The first time, the file "
                   "is indexed, which takes about half a minute. HyperLeda: Makarov et al. 2014.")
    priority = 40

    def settings_schema(self):
        return [
            SettingField("file", "HyperLeda file (hyperleda.csv)", FILE, "",
                         file_filter="HyperLeda (hyperleda.csv);;CSV files (*.csv);;All files (*)",
                         help="Leave empty to look for it in the ASTAP folder (and its Database/databases folder).",
                         validator=self._check_file),
            SettingField("max_objects", "Maximum number of galaxies", INT, 50, minimum=1, maximum=2000, step=10,
                         help="The brightest galaxies in the field are taken first."),
            SettingField("min_size", "Minimum size", FLOAT, 0.0, minimum=0, maximum=30, step=0.1, decimals=1,
                         suffix="′", help="Leave out galaxies smaller than this."),
            SettingField("max_field", "Only for fields up to", FLOAT, 15.0, minimum=0.5, maximum=180, step=1,
                         decimals=1, suffix="°", help="Faint galaxies can't be seen in wide-field and phone photos, "
                                                      "so they are only added for smaller fields."),
            SettingField("skip_ngc_ic", "Leave out NGC and IC objects (the other catalogues have them)", BOOL, True),
            SettingField("online_details", "Look up type and redshift in SIMBAD (gives distances)", BOOL, True,
                         help="One quick online query for all galaxies at once. Without it, HyperLeda-only "
                              "galaxies have no distance."),
        ]

    # ------------------------------------------------------------------ the file
    def _astap_setting(self, key: str):
        st = getattr(self.settings, "store", None)
        if st is None:
            return None
        from platesolver.plugins.solvers.astap import AstapSolver
        section = AstapSolver.section_key()
        return st.get(section, key) if st.has(section, key) else None

    def _check_file(self, value) -> tuple[bool, str]:
        """Shown under the file field in Settings: which file is used, or why none was found."""
        if str(value or "").strip():
            return validate_file(value)
        found = self._auto_file()
        if found is None:
            return False, ("Not found in the ASTAP folder or its Database/databases folder. Install ASTAP's "
                           "HyperLeda database, or choose hyperleda.csv here.")
        ok, msg = validate_file(str(found))
        return ok, f"Found automatically: {found}" + ("" if ok else f" – {msg}")

    def _auto_file(self) -> Path | None:
        for p in candidate_files(self._astap_setting("path"), self._astap_setting("database_folder")):
            try:
                if p.is_file():
                    return p
            except OSError:
                continue
        return None

    def csv_file(self) -> Path | None:
        configured = str(self.setting("file") or "").strip().strip('"')
        if configured:
            p = Path(configured)
            return p if p.is_file() else None
        return self._auto_file()

    def is_available(self) -> tuple[bool, str]:
        if self.csv_file() is None:
            if str(self.setting("file") or "").strip():
                return False, "the HyperLeda file set in Settings was not found"
            return False, "hyperleda.csv not found (optional – install ASTAP's HyperLeda database to use it)"
        return True, ""

    # ------------------------------------------------------------------ lookup
    def find_objects(self, image: ImageData, solution: SolveResult, ctx: TaskContext) -> list[SkyObject]:
        path = self.csv_file()
        if path is None:
            return []
        field = max(solution.fov_width_deg or 0, solution.fov_height_deg or 0)
        if field > float(self.setting("max_field") or 15):
            ctx.log(f"HyperLeda: skipped – the field is {field:.0f}° wide, more than the "
                    f"{float(self.setting('max_field') or 15):g}° set for faint galaxies")
            return []
        idx = load_index(path, ctx.log)
        half_diag = math.hypot(solution.fov_width_deg, solution.fov_height_deg) / 2.0
        near = cone(idx, solution.center_ra_deg, solution.center_dec_deg, half_diag + 0.5)
        if near.size == 0:
            return []
        near = near[np.argsort(np.asarray(idx["rank"])[near], kind="stable")]   # brightest first
        ra = np.asarray(idx["ra"])[near]
        dec = np.asarray(idx["dec"])[near]
        x, y = solution.wcs.world_to_pixel_values(ra, dec)
        x, y = np.asarray(x, float), np.asarray(y, float)
        size = np.asarray(idx["size"])[near]
        reach = size * 30.0 / (solution.pixel_scale_arcsec or 1.0)    # half the size, in pixels
        dx = np.maximum(0.0, np.maximum(-x, x - (solution.width - 1)))
        dy = np.maximum(0.0, np.maximum(-y, y - (solution.height - 1)))
        inside = np.isfinite(x) & np.isfinite(y) & (np.hypot(dx, dy) <= reach)
        min_size = float(self.setting("min_size") or 0)
        if min_size:
            inside &= size >= min_size
        skip = bool(self.setting("skip_ngc_ic"))
        limit = int(self.setting("max_objects"))
        out: list[SkyObject] = []
        for k in np.nonzero(inside)[0]:
            i = int(near[k])
            raw = name_at(idx, i)
            if skip and _NGC_IC.match(raw):
                continue
            out.append(self._make(idx, i, raw, float(x[k]), float(y[k])))
            if len(out) >= limit:
                break
        if out and self.setting("online_details"):
            try:
                self._simbad_details(out, ctx)
            except Exception as exc:     # no internet etc.: the galaxies are still listed
                ctx.log(f"HyperLeda: SIMBAD details not available ({exc})")
        return out

    @staticmethod
    def _make(idx: dict, i: int, raw: str, x: float, y: float) -> SkyObject:
        name = pretty_name(raw)
        pa = float(idx["pa"][i])
        ids = [name] + ([f"LEDA {name[4:]}"] if name.startswith("PGC ") else [])
        common = name if name != raw and raw.replace("_", "").isalpha() else ""
        return SkyObject(
            name=name, ra_deg=float(idx["ra"][i]), dec_deg=float(idx["dec"][i]),
            object_type="Galaxy", category="galaxy", common_name=common, aliases=ids[1:],
            magnitude=None, size_arcmin=float(idx["size"][i]) or None,
            size_minor_arcmin=float(idx["width"][i]) or None,
            position_angle_deg=None if math.isnan(pa) else pa, catalog="HyperLeda",
            x=x, y=y,
            extra={"identifiers": ids, "brightness_rank": int(idx["rank"][i]), "catalogued": True,
                   "famous": False},
        )

    @staticmethod
    def _simbad_details(objects: list[SkyObject], ctx: TaskContext) -> None:
        """Type, redshift and SIMBAD id (for measured distances and links), in one query per 300 galaxies."""
        from platesolver.core.identifiers import canonical
        from platesolver.services import simbad

        by_key: dict[str, SkyObject] = {}
        forms: list[str] = []
        for o in objects:
            if o.extra.get("simbad_oid") is not None:
                continue
            for f in simbad_forms(o.name):
                by_key.setdefault(canonical(f), o)
                forms.append(f)
        found = 0
        for part in simbad.chunks(forms, 300):
            ctx.check_cancel()
            quoted = ",".join("'" + f.replace("'", "''") + "'" for f in part)
            rows = simbad.query("SELECT i.id, b.oid, b.main_id, b.otype, b.rvz_redshift FROM ident AS i "
                                f"JOIN basic AS b ON b.oid = i.oidref WHERE i.id IN ({quoted})", timeout=60)
            for r in rows:
                o = by_key.get(canonical(r.get("id", "")))
                if o is None or o.extra.get("simbad_oid") is not None:
                    continue
                try:
                    o.extra["simbad_oid"] = int(float(r["oid"]))
                except (KeyError, ValueError):
                    continue
                found += 1
                z = simbad.num(r.get("rvz_redshift"))
                if z is not None:
                    o.extra["redshift"] = z
                cat, label = simbad.classify(r.get("otype", ""))
                if cat == "galaxy":
                    o.object_type = label
                main = simbad.clean_id(r.get("main_id", ""))
                if main and main != o.name:
                    o.extra["identifiers"] = list(dict.fromkeys(o.extra["identifiers"] + [main]))
                    o.aliases = list(dict.fromkeys(o.aliases + [main]))[:12]
        ctx.log(f"HyperLeda: SIMBAD knows {found} of {len(objects)} galaxies")
