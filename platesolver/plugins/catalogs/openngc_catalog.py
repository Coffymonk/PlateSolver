# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Offline catalogue: OpenNGC (all NGC, IC and Messier objects plus some extras), bundled with the program.

OpenNGC by Mattia Verga, https://github.com/mattiaverga/OpenNGC - licence CC-BY-SA-4.0.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from platesolver.core.interfaces import CatalogProvider
from platesolver.core.models import ImageData, SkyObject, SolveResult
from platesolver.core.plugin import TaskContext
from platesolver.core.settings import BOOL, CHOICE, FLOAT, INT, SettingField
from platesolver.services.simbad import best_common_name

DATA = Path(__file__).resolve().parents[2] / "data"

CATEGORY = {
    "*": "star", "**": "star", "Nova": "star",
    "*Ass": "cluster", "OCl": "cluster", "GCl": "cluster",
    "Cl+N": "nebula", "PN": "nebula", "HII": "nebula", "DrkN": "nebula", "EmN": "nebula", "Neb": "nebula",
    "RfN": "nebula", "SNR": "nebula",
    "G": "galaxy", "GPair": "galaxy", "GTrpl": "galaxy", "GGroup": "galaxy",
}
TYPE_LABEL = {
    "*": "Star", "**": "Double star", "Nova": "Nova", "*Ass": "Stellar association", "OCl": "Open cluster",
    "GCl": "Globular cluster", "Cl+N": "Star cluster with nebula", "PN": "Planetary nebula",
    "HII": "Emission nebula (HII region)", "DrkN": "Dark nebula", "EmN": "Emission nebula", "Neb": "Nebula",
    "RfN": "Reflection nebula", "SNR": "Supernova remnant", "G": "Galaxy", "GPair": "Pair of galaxies",
    "GTrpl": "Triplet of galaxies", "GGroup": "Group of galaxies", "Other": "Object",
}
_PREFIXES = (("NGC", "NGC "), ("IC", "IC "), ("Mel", "Mel "), ("B", "Barnard "), ("C", "Caldwell "),
             ("ESO", "ESO "), ("HCG", "HCG "), ("MWSC", "MWSC "), ("PGC", "PGC "), ("UGC", "UGC "),
             ("H", "H "), ("Cl", "Cl "))

_table: dict | None = None


def version_info() -> dict:
    """The bundled release, written by tools/update_openngc.py: {'version', 'release_date', 'objects', …}."""
    try:
        return json.loads((DATA / "openngc_version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def version_text() -> str:
    info = version_info()
    if not info.get("version"):
        return "Built-in version: unknown."
    date = f" of {info['release_date']}" if info.get("release_date") else ""
    n = (", " + f"{int(info['objects']):,}".replace(",", " ") + " objects") if info.get("objects") else ""
    return f"Built-in version: OpenNGC {info['version']} (release{date}{n}); updated with each PlateSolver release."


def _pretty(name: str) -> str:
    """'NGC0224' -> 'NGC 224', 'Mel022' -> 'Mel 22', 'B033' -> 'Barnard 33'."""
    for raw, nice in sorted(_PREFIXES, key=lambda p: -len(p[0])):
        if name.startswith(raw) and name[len(raw):][:1].isdigit():
            rest = name[len(raw):]
            digits = "".join(ch for ch in rest if ch.isdigit())
            suffix = rest[len(digits):]
            return f"{nice}{int(digits)}{suffix}"
    return name


def _f(value):
    try:
        return float(value) if value not in ("", None) else None
    except ValueError:
        return None


def load_table() -> dict:
    """Read the bundled CSV once; positions go into numpy arrays for fast searching."""
    global _table
    if _table is None:
        rows = []
        with open(DATA / "openngc.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        ra = np.radians([float(r["ra"]) for r in rows])
        dec = np.radians([float(r["dec"]) for r in rows])
        _table = {"rows": rows, "xyz": np.column_stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra),
                                                        np.sin(dec)])}
    return _table


class OpenNgcCatalog(CatalogProvider):
    plugin_id = "openngc"
    name = "OpenNGC (offline)"
    _about = ("All NGC, IC and Messier objects from the OpenNGC catalogue, built into the program, so "
              "objects can be listed without an internet connection. Includes parallaxes and redshifts, "
              "so some distances work offline too. Data: OpenNGC by Mattia Verga (CC-BY-SA 4.0).")
    priority = 20

    @property
    def description(self) -> str:
        return self._about + " " + version_text()

    def settings_schema(self):
        return [
            SettingField("use", "Use this catalogue", CHOICE, "fallback", choices=[
                ("fallback", "Only when the online catalogues find nothing (e.g. no internet)"),
                ("always", "Always, together with the online catalogues")],
                help="Objects found in both are merged, so nothing is listed twice."),
            SettingField("galaxies", "Include galaxies", BOOL, True),
            SettingField("nebulae", "Include nebulae", BOOL, True),
            SettingField("clusters", "Include star clusters", BOOL, True),
            SettingField("min_size", "Minimum size (except Messier objects)", FLOAT, 0.0, minimum=0,
                         maximum=120, step=0.5, decimals=1, suffix="′"),
            SettingField("max_objects", "Maximum number of objects", INT, 150, minimum=5, maximum=2000, step=25),
        ]

    def only_as_fallback(self) -> bool:
        return self.setting("use") == "fallback"

    def find_objects(self, image: ImageData, solution: SolveResult, ctx: TaskContext) -> list[SkyObject]:
        table = load_table()
        ra0, dec0 = math.radians(solution.center_ra_deg), math.radians(solution.center_dec_deg)
        centre = np.array([math.cos(dec0) * math.cos(ra0), math.cos(dec0) * math.sin(ra0), math.sin(dec0)])
        half_diag = math.hypot(solution.fov_width_deg, solution.fov_height_deg) / 2.0
        radius = math.radians(half_diag + 2.0)   # a margin for big objects centred outside the frame
        near = np.nonzero(table["xyz"] @ centre >= math.cos(radius))[0]
        wanted = {"galaxy": self.setting("galaxies"), "nebula": self.setting("nebulae"),
                  "cluster": self.setting("clusters"), "star": False, "other": True}
        min_size = float(self.setting("min_size"))
        out = []
        for i in near:
            obj = self._make(table["rows"][int(i)])
            if obj is None or not wanted.get(obj.category, True):
                continue
            if min_size and not obj.extra.get("messier") and (obj.size_arcmin or 0) < min_size:
                continue
            x, y = solution.radec_to_pixel(obj.ra_deg, obj.dec_deg)
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            if not solution.contains(x, y):
                reach = (obj.size_arcmin or 0) * 30.0 / (solution.pixel_scale_arcsec or 1.0)
                dx = max(0.0, -x, x - (solution.width - 1))
                dy = max(0.0, -y, y - (solution.height - 1))
                if reach <= 0 or math.hypot(dx, dy) > reach:
                    continue
            obj.x, obj.y = x, y
            out.append(obj)
        out.sort(key=lambda o: (not o.extra.get("messier"), not o.common_name, -(o.size_arcmin or 0)))
        return out[: int(self.setting("max_objects"))]

    @staticmethod
    def _make(r: dict) -> SkyObject | None:
        t = r.get("type", "")
        ids: list[str] = []
        messier = r.get("messier", "").strip()
        if messier:
            ids.append(f"M {int(messier)}")
        main = _pretty(r["name"])
        ids.append(main)
        for col, prefix in (("ngc", "NGC "), ("ic", "IC ")):
            for part in (r.get(col) or "").split(","):
                part = part.strip()
                if part:
                    ids.append(prefix + str(int("".join(ch for ch in part if ch.isdigit()) or 0)) +
                               "".join(ch for ch in part if ch.isalpha()))
        commons = [c.strip() for c in (r.get("commonnames") or "").split(",") if c.strip()]
        ids += [f"NAME {c}" for c in commons]
        ids += [i.strip() for i in (r.get("identifiers") or "").split(",") if i.strip()]
        name = ids[0]
        mag = _f(r.get("vmag")) or _f(r.get("bmag"))
        return SkyObject(
            name=name, ra_deg=float(r["ra"]), dec_deg=float(r["dec"]),
            object_type=TYPE_LABEL.get(t, t or "Object"), category=CATEGORY.get(t, "other"),
            common_name=best_common_name([f"NAME {c}" for c in commons]),
            aliases=[i for i in ids[1:] if not i.startswith("NAME ")][:10],
            magnitude=mag, size_arcmin=_f(r.get("majax")), size_minor_arcmin=_f(r.get("minax")),
            position_angle_deg=_f(r.get("pa")), catalog="OpenNGC",
            extra={"identifiers": ids, "plx_mas": _f(r.get("parallax")), "plx_err_mas": None,
                   "redshift": _f(r.get("redshift")), "messier": bool(messier), "catalogued": True,
                   "famous": bool(messier) or main.startswith(("NGC", "IC")), "constellation": r.get("const", "")},
        )
