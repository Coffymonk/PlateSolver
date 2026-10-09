# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Object hints: "this image shows M101" -> a position for the solvers to search around.

A hint can come from the file name (M101_final.jpg, NGC7380_stars.jpg), be typed by the user
(an object name or coordinates), or be asked for when a blind solve fails. Names are looked up in
the built-in OpenNGC catalogue first (offline), then in SIMBAD (online).

A hint only tells the solver where to look. The image scale is handled separately, and when it is
only a guess (a cropped and resized JPG), the solvers also try any scale near the hinted position.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from platesolver.core.identifiers import canonical
from platesolver.core.settings import BOOL, SettingField, SettingsSection


@dataclass
class PositionHint:
    ra_deg: float
    dec_deg: float
    label: str          # what the user will recognise, e.g. "M 101 (NGC 5457)"
    source: str         # "file name", "you", ...
    found_in: str       # "coordinates", "OpenNGC", "SIMBAD"

    def describe(self) -> str:
        from platesolver.core.formatting import format_dec, format_ra
        return (f"{self.label} at RA {format_ra(self.ra_deg)}, Dec {format_dec(self.dec_deg)} "
                f"({self.found_in}, from {self.source})")


class HintSettings(SettingsSection):
    section_id = "hints"
    name = "Object hints"
    description = ("If you know what an image shows, the solvers only need to search near that object, which is "
                   "much faster and more reliable than searching the whole sky, especially for JPG and TIFF "
                   "images that carry no position. Use Solve › Solve with object hint… (Shift+F5), or let "
                   "PlateSolver read the object from the file name. A cropped or resized image is fine: near "
                   "the object, any image scale is tried as well.")

    def settings_schema(self) -> list[SettingField]:
        return [
            SettingField("from_filename", "Recognise the object from the file name", BOOL, True,
                         help="E.g. M101_final.jpg, NGC7380_stars.tif, Sh2-155.jpg. Only for images without "
                              "a position of their own."),
            SettingField("ask_on_failure", "When an image can't be solved, ask what it shows", BOOL, True),
            SettingField("use_simbad", "Look up names in SIMBAD (online) when not in the built-in catalogue",
                         BOOL, True, help="Needed for named objects outside NGC/IC/Messier, e.g. Sh2-155 or "
                                          "'Wizard Nebula' if OpenNGC doesn't list that name."),
        ]


# ---------------------------------------------------------------- file names
_FILENAME_PATTERNS = [
    (re.compile(r"(?<![A-Z0-9])(?:M|MESSIER)[\s_.-]?0*(\d{1,3})(?![0-9])"), "M {}"),
    (re.compile(r"(?<![A-Z0-9])NGC[\s_.-]?0*(\d{1,4})(?![0-9])"), "NGC {}"),
    (re.compile(r"(?<![A-Z0-9])IC[\s_.-]?0*(\d{1,4})(?![0-9])"), "IC {}"),
    (re.compile(r"(?<![A-Z0-9])SH[\s_.-]?2[\s_.-]+0*(\d{1,3})(?![0-9])"), "Sh2-{}"),
    (re.compile(r"(?<![A-Z0-9])CALDWELL[\s_.-]?0*(\d{1,3})(?![0-9])"), "C {}"),
    (re.compile(r"(?<![A-Z0-9])BARNARD[\s_.-]?0*(\d{1,3})(?![0-9])"), "Barnard {}"),
    (re.compile(r"(?<![A-Z0-9])(?:LDN|LBN|VDB|ABELL|CED|MEL|CR)[\s_.-]?0*(\d{1,4})(?![0-9])"), None),
]


def guess_from_filename(path: str | Path) -> str | None:
    """'M101_JPEG.jpg' -> 'M 101'; 'NGC7380_FINAL.jpg' -> 'NGC 7380'; 'IMG_0001.jpg' -> None."""
    stem = Path(path).stem.upper()
    for rx, fmt in _FILENAME_PATTERNS:
        m = rx.search(stem)
        if not m:
            continue
        n = int(m.group(1))
        if fmt == "M {}" and not 1 <= n <= 110:
            continue
        if fmt is None:
            prefix = re.match(r"[A-Z]+", m.group(0).lstrip("_.- ")).group(0)
            return f"{prefix.title() if prefix in ('ABELL', 'CED', 'MEL') else prefix} {n}"
        return fmt.format(n)
    return common_name_in(stem)


_GENERIC = {"nebula", "galaxy", "cluster", "star", "stars", "the", "great", "complex", "cloud", "region"}
_names_by_key: dict | None = None


def _common_name_keys() -> dict:
    """'crescent' -> 'Crescent Nebula', 'northamerica' -> 'North America Nebula', from OpenNGC common names."""
    global _names_by_key
    if _names_by_key is None:
        from platesolver.plugins.catalogs.openngc_catalog import load_table
        keys = {}
        for r in load_table()["rows"]:
            for name in (r.get("commonnames") or "").split(","):
                name = name.strip()
                words = [w for w in re.findall(r"[a-z0-9]+", name.lower().replace("'s", "")) if w not in _GENERIC]
                key = "".join(words)
                if len(key) >= 4 and not key.isdigit():
                    keys.setdefault(key, name)
        _names_by_key = keys
    return _names_by_key


def common_name_in(stem: str) -> str | None:
    """A known common name in a file name: 'crescent_final2_PS' -> 'Crescent Nebula'."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", stem.lower().replace("'s", "")) if t not in _GENERIC]
    keys = _common_name_keys()
    for n in (3, 2, 1):                      # longest phrase first: 'north_america' before 'north'
        for i in range(len(tokens) - n + 1):
            key = "".join(tokens[i:i + n])
            if len(key) >= 4 and key in keys:
                return keys[key]
    return None


# ---------------------------------------------------------------- coordinates
_NUM = r"[+-]?\d+(?:\.\d+)?"


def parse_coordinates(text: str) -> tuple[float, float] | None:
    """'210.80 54.35' (degrees), '14 03 12.6 +54 20 56', '14:03:12.6 +54:20:56', '14h03m12s +54°20′56″'."""
    from platesolver.core.formatting import parse_sexagesimal
    t = text.strip().replace(",", " ")
    nums = re.findall(_NUM, re.sub(r"[hHdDmMsS°′″'\":]", " ", t))
    if len(nums) < 2:
        return None
    sexa = bool(re.search(r"[hH:]|\d\s+\d", t)) and len(nums) >= 4
    if len(nums) == 2 and not re.search(r"[hH:]", t):
        ra, dec = float(nums[0]), float(nums[1])             # decimal degrees
    elif len(nums) == 2:
        parts = t.split()
        if len(parts) != 2:
            return None
        ra, dec = parse_sexagesimal(parts[0].replace("h", ":").replace("m", ":").rstrip("s")), \
            parse_sexagesimal(parts[1])
        if ra is None or dec is None:
            return None
        ra *= 15.0
    elif sexa and len(nums) in (4, 6):
        half = len(nums) // 2
        rh = [float(x) for x in nums[:half]]
        dd = nums[half:]
        sign = -1.0 if dd[0].startswith("-") else 1.0
        dv = [abs(float(x)) for x in dd]
        ra = 15.0 * (rh[0] + rh[1] / 60 + (rh[2] / 3600 if half == 3 else 0))
        dec = sign * (dv[0] + dv[1] / 60 + (dv[2] / 3600 if half == 3 else 0))
    else:
        return None
    if not (0 <= ra < 360 and -90 <= dec <= 90):
        return None
    return ra, dec


# ---------------------------------------------------------------- name lookup
_ngc_index: dict | None = None


def _openngc_index() -> dict:
    global _ngc_index
    if _ngc_index is None:
        from platesolver.plugins.catalogs.openngc_catalog import _pretty, load_table
        idx = {}
        for r in load_table()["rows"]:
            names = [r["name"]]
            if r.get("messier"):
                names.append(f"M{int(r['messier'])}")
            names += [x for x in (r.get("identifiers") or "").split(",") if x]
            label = _pretty(r["name"])
            if r.get("messier"):
                label = f"M {int(r['messier'])} ({label})"
            entry = (float(r["ra"]), float(r["dec"]), label)
            for n in names:
                idx.setdefault(canonical(n), entry)
            for common in (r.get("commonnames") or "").split(","):
                if common.strip():
                    idx.setdefault("NAME:" + common.strip().lower(), entry)
        _ngc_index = idx
    return _ngc_index


def lookup_offline(name: str) -> tuple[float, float, str] | None:
    idx = _openngc_index()
    hit = idx.get(canonical(name)) or idx.get("NAME:" + name.strip().lower())
    if hit is None and name.lower().startswith("the "):
        hit = idx.get("NAME:" + name.strip().lower()[4:])
    return hit


def _simbad_forms(name: str) -> list[str]:
    """Spellings SIMBAD's identifier table uses: 'M 101', 'NGC 7380', 'Sh 2-155', 'NAME Wizard Nebula'."""
    s = " ".join(name.split())
    out = [s]
    m = re.match(r"^(M|NGC|IC|Barnard|LDN|LBN|Abell|Ced|Mel|Cr|VdB|C)\s*0*(\d+)$", s, re.IGNORECASE)
    if m:
        pre = {"m": "M", "ngc": "NGC", "ic": "IC", "barnard": "Barnard", "ldn": "LDN", "lbn": "LBN",
               "abell": "Abell", "ced": "Ced", "mel": "Cl Melotte", "cr": "Cl Collinder", "vdb": "VdB",
               "c": "C"}[m.group(1).lower()]
        out.append(f"{pre} {int(m.group(2))}")
    m = re.match(r"^Sh\s*2\s*-?\s*0*(\d+)$", s, re.IGNORECASE)
    if m:
        out.append(f"Sh 2-{int(m.group(1))}")
    title = " ".join(w if w.isupper() else w.capitalize() for w in s.split())
    out += [f"NAME {s}", f"NAME {title}"]
    return list(dict.fromkeys(out))


def lookup_simbad(name: str) -> tuple[float, float, str] | None:
    from platesolver.services import simbad
    forms = _simbad_forms(name)
    quoted = ",".join("'" + f.replace("'", "''") + "'" for f in forms)
    rows = simbad.query("SELECT TOP 1 b.ra, b.dec, b.main_id FROM basic AS b JOIN ident AS i ON i.oidref = b.oid "
                        f"WHERE i.id IN ({quoted})", timeout=30)
    for r in rows:
        ra, dec = simbad.num(r.get("ra")), simbad.num(r.get("dec"))
        if ra is not None and dec is not None:
            return ra, dec, simbad.clean_id(r.get("main_id", name)) or name
    return None


def resolve(text: str, source: str = "you", use_simbad: bool = True) -> PositionHint:
    """Object name or coordinates -> PositionHint. Raises LookupError with a readable reason."""
    text = (text or "").strip()
    if not text:
        raise LookupError("no object given")
    coords = parse_coordinates(text)
    if coords:
        return PositionHint(coords[0], coords[1], "the given coordinates", source, "coordinates")
    hit = lookup_offline(text)
    if hit:
        return PositionHint(hit[0], hit[1], hit[2], source, "built-in catalogue")
    if use_simbad:
        try:
            hit = lookup_simbad(text)
        except Exception as exc:
            raise LookupError(f"'{text}' is not in the built-in catalogue and SIMBAD could not be reached "
                              f"({exc})") from exc
        if hit:
            return PositionHint(hit[0], hit[1], hit[2], source, "SIMBAD")
        raise LookupError(f"'{text}' was not found in the built-in catalogue or SIMBAD")
    raise LookupError(f"'{text}' is not in the built-in catalogue (SIMBAD lookups are turned off)")


def apply(image, hint: PositionHint) -> None:
    """Put the hint into the image's solve hints; marked so solvers also try without it."""
    h = image.hints
    h.ra_deg, h.dec_deg = hint.ra_deg, hint.dec_deg
    h.source["ra"] = h.source["dec"] = f"hint: {hint.label}"
    h.position_hint = hint.label
