# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Objects in the solved field, from SIMBAD.

Three queries around the image centre:
  1. everything with a well-known designation (Messier, NGC, IC, Sharpless, named objects, …)
  2. bright stars and objects larger than the minimum size
  3. very large objects whose centre is outside the frame but which reach into it
then one query for the identifiers of what was found, to pick good names.
"""
from __future__ import annotations

import math

from platesolver.core.interfaces import CatalogProvider
from platesolver.core.models import ImageData, SkyObject, SolveResult
from platesolver.core.plugin import TaskContext
from platesolver.core.settings import BOOL, FLOAT, INT, SettingField
from platesolver.services import simbad
from platesolver.services.simbad import (CATALOG_PREFIXES, FAMOUS_PREFIXES, best_common_name, classify,
                                         clean_id, num)

COLUMNS = ("b.oid, b.main_id, b.ra, b.dec, b.otype, b.galdim_majaxis, b.galdim_minaxis, "
           "b.galdim_angle, b.plx_value, b.plx_err, b.rvz_redshift, f.\"V\" AS vmag")
NAMED_PATTERNS = ("M %", "NGC %", "IC %", "NAME %", "Sh2%", "Sh 2-%", "LBN %", "LDN %", "Barnard %",
                  "VdB %", "Ced %", "Cl %", "Abell %")
ID_PATTERNS = NAMED_PATTERNS + ("* %", "V* %", "HD %", "HIP %", "UGC %", "PGC %", "MCG %", "PN %", "ACO %")
BIG_OUTSIDE_ARCMIN = 20.0      # objects at least this large are also searched for just outside the frame


def _cone(ra: float, dec: float, radius_deg: float) -> str:
    return (f"CONTAINS(POINT('ICRS', b.ra, b.dec), "
            f"CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.5f})) = 1")


def _like_any(column: str, patterns) -> str:
    return "(" + " OR ".join(f"{column} LIKE '{p}'" for p in patterns) + ")"


class SimbadCatalog(CatalogProvider):
    plugin_id = "simbad"
    name = "SIMBAD"
    description = ("Finds galaxies, nebulae, star clusters and bright stars in the solved field using the "
                   "SIMBAD astronomical database (CDS, Strasbourg). Needs an internet connection; results "
                   "are cached for two weeks.")
    priority = 10

    def settings_schema(self):
        return [
            SettingField("galaxies", "Include galaxies", BOOL, True),
            SettingField("nebulae", "Include nebulae", BOOL, True),
            SettingField("clusters", "Include star clusters", BOOL, True),
            SettingField("stars", "Include stars", BOOL, True),
            SettingField("star_mag", "Stars brighter than magnitude", FLOAT, 7.0, minimum=-2, maximum=15,
                         step=0.5, decimals=1, help="Named stars are shown up to 3 magnitudes fainter."),
            SettingField("min_size", "Minimum size for uncatalogued objects", FLOAT, 1.0, minimum=0,
                         maximum=120, step=0.5, decimals=1, suffix="′",
                         help="Objects with a Messier, NGC, IC or other well-known designation are always shown."),
            SettingField("max_objects", "Maximum number of objects", INT, 150, minimum=5, maximum=2000, step=25,
                         help="The most prominent objects are kept when there are more."),
        ]

    # ------------------------------------------------------------------ queries
    def _queries(self, ra: float, dec: float, radius: float) -> list[tuple[str, str]]:
        cone = _cone(ra, dec, radius)
        size = max(0.0, float(self.setting("min_size")))
        star_mag = float(self.setting("star_mag"))
        q_named = (f"SELECT TOP 3000 {COLUMNS}, i.id AS matched_id FROM basic AS b "
                   f"JOIN ident AS i ON i.oidref = b.oid LEFT JOIN allfluxes AS f ON f.oidref = b.oid "
                   f"WHERE {cone} AND {_like_any('i.id', NAMED_PATTERNS)}")
        conds = []
        if self.setting("stars"):
            conds.append(f'f."V" <= {star_mag:.2f}')
        if size > 0:
            conds.append(f"b.galdim_majaxis >= {size:.3f}")
        queries = [("named objects", q_named)]
        if conds:
            queries.append(("bright and large objects",
                            f"SELECT TOP 3000 {COLUMNS} FROM basic AS b LEFT JOIN allfluxes AS f ON f.oidref = b.oid "
                            f"WHERE {cone} AND ({' OR '.join(conds)})"))
        wide = _cone(ra, dec, radius + BIG_OUTSIDE_ARCMIN * 6 / 60.0)
        queries.append(("large objects around the field",
                        f"SELECT TOP 500 {COLUMNS} FROM basic AS b LEFT JOIN allfluxes AS f ON f.oidref = b.oid "
                        f"WHERE {wide} AND b.galdim_majaxis >= {max(size, BIG_OUTSIDE_ARCMIN):.1f}"))
        return queries

    def _identifiers(self, oids: list[int], ctx: TaskContext) -> dict[int, list[str]]:
        ids: dict[int, list[str]] = {}
        for part in simbad.chunks(sorted(oids), 400):
            ctx.check_cancel()
            rows = simbad.query(f"SELECT oidref, id FROM ident WHERE oidref IN ({simbad.in_list(part)}) "
                                f"AND {_like_any('id', ID_PATTERNS)}")
            for r in rows:
                oid = int(float(r["oidref"]))
                ids.setdefault(oid, []).append(" ".join(r["id"].split()))
        return ids

    # ------------------------------------------------------------------ main
    def find_objects(self, image: ImageData, solution: SolveResult, ctx: TaskContext) -> list[SkyObject]:
        ra, dec = solution.center_ra_deg, solution.center_dec_deg
        half_diag = math.hypot(solution.fov_width_deg, solution.fov_height_deg) / 2.0
        radius = half_diag * 1.02
        rows: dict[int, dict] = {}
        for label, adql in self._queries(ra, dec, radius):
            ctx.check_cancel()
            ctx.log(f"SIMBAD: {label}…")
            for r in simbad.query(adql):
                try:
                    oid = int(float(r["oid"]))
                except (KeyError, ValueError):
                    continue
                rows.setdefault(oid, r)
        if not rows:
            return []
        idents = self._identifiers(list(rows), ctx)
        objects = [o for o in (self._make_object(oid, r, idents.get(oid, []), solution)
                               for oid, r in rows.items()) if o is not None]
        objects.sort(key=self._score, reverse=True)
        return objects[: int(self.setting("max_objects"))]

    def _make_object(self, oid: int, r: dict, ids: list[str], solution: SolveResult) -> SkyObject | None:
        ra, dec = num(r.get("ra")), num(r.get("dec"))
        if ra is None or dec is None:
            return None
        category, type_label = classify(r.get("otype", ""))
        wanted = {"galaxy": "galaxies", "nebula": "nebulae", "cluster": "clusters", "star": "stars"}
        if category in wanted and not self.setting(wanted[category]):
            return None
        vmag = num(r.get("vmag", r.get("V")))
        major = num(r.get("galdim_majaxis"))
        minor = num(r.get("galdim_minaxis"))
        main_id = " ".join(r.get("main_id", "").split())
        all_ids = list(dict.fromkeys(ids + [main_id]))
        names = [i for i in all_ids if i.startswith("NAME ")]
        famous = any(i.startswith(FAMOUS_PREFIXES) for i in all_ids)
        catalogued = famous or any(i.startswith(CATALOG_PREFIXES) for i in all_ids)

        # --- filter
        star_mag = float(self.setting("star_mag"))
        if category == "star":
            limit = star_mag + (3.0 if names else 0.0)
            if vmag is None or vmag > limit:
                return None
        elif not catalogued and not names:
            if major is None or major < float(self.setting("min_size")):
                return None

        # --- position: inside the frame, or big enough to reach into it
        x, y = solution.radec_to_pixel(ra, dec)
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        if not solution.contains(x, y):
            reach_px = (major or 0.0) * 60.0 / 2.0 / (solution.pixel_scale_arcsec or 1.0)
            dx = max(0.0, -x, x - (solution.width - 1))
            dy = max(0.0, -y, y - (solution.height - 1))
            if reach_px <= 0 or math.hypot(dx, dy) > reach_px:
                return None

        # --- names
        common = best_common_name(names)
        if category == "star":
            desig = next((clean_id(i) for p in ("* ", "V* ", "HD ", "HIP ") for i in all_ids if i.startswith(p)),
                         clean_id(main_id))
            name, common = (common, desig) if common else (desig, "")
        else:
            name = next((clean_id(i) for p in CATALOG_PREFIXES for i in all_ids if i.startswith(p)),
                        clean_id(main_id))
        aliases = [clean_id(i) for i in all_ids if clean_id(i) not in (name, common)][:10]

        return SkyObject(
            name=name, ra_deg=ra, dec_deg=dec, object_type=type_label, aliases=aliases, magnitude=vmag,
            size_arcmin=major, size_minor_arcmin=minor, position_angle_deg=num(r.get("galdim_angle")),
            x=x, y=y, catalog="SIMBAD", category=category, common_name=common,
            extra={"simbad_oid": oid, "simbad_main_id": main_id, "otype": r.get("otype", ""),
                   "plx_mas": num(r.get("plx_value")), "plx_err_mas": num(r.get("plx_err")),
                   "redshift": num(r.get("rvz_redshift")), "identifiers": all_ids,
                   "famous": famous, "catalogued": catalogued},
        )

    @staticmethod
    def _score(o: SkyObject) -> float:
        ids = o.extra.get("identifiers", [])
        s = 0.0
        if any(i.startswith("M ") for i in ids):
            s += 10000
        if any(i.startswith(("NGC ", "IC ")) for i in ids):
            s += 3000
        if o.common_name:
            s += 1500
        if o.extra.get("catalogued"):
            s += 500
        if o.size_arcmin:
            s += min(o.size_arcmin, 300.0) * 10
        if o.magnitude is not None:
            s += max(0.0, 15.0 - o.magnitude) * 60
        return s
