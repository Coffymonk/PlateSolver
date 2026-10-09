# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Published distance measurements collected by SIMBAD (the mesDistance table).

Many objects have several published distances (Cepheids, tip of the red giant
branch, Tully-Fisher, …). The median is used, which is robust against outliers.
"""
from __future__ import annotations

import statistics
from typing import Sequence

from platesolver.core.formatting import parsec_to_ly
from platesolver.core.interfaces import DistanceResolver
from platesolver.core.models import Distance, SkyObject
from platesolver.core.plugin import TaskContext
from platesolver.core.settings import CHOICE, SettingField
from platesolver.services import simbad

UNIT_TO_PC = {"pc": 1.0, "kpc": 1e3, "mpc": 1e6, "gpc": 1e9}


class SimbadMeasuredDistance(DistanceResolver):
    plugin_id = "simbad_measured"
    name = "Published distances (SIMBAD)"
    description = ("Distances published in the literature and collected by SIMBAD. Used for galaxies, nebulae "
                   "and clusters, and for stars without a good parallax.")
    priority = 20

    def settings_schema(self):
        return [
            SettingField("combine", "When there are several measurements", CHOICE, "median", choices=[
                ("median", "Use the median (robust)"), ("latest", "Use the most recent publication")]),
        ]

    def prepare(self, objects: Sequence[SkyObject], ctx: TaskContext) -> None:
        oids = sorted({o.extra["simbad_oid"] for o in objects if o.extra.get("simbad_oid") is not None})
        found: dict[int, list[dict]] = {}
        for part in simbad.chunks(oids, 400):
            ctx.check_cancel()
            where = f"FROM mesDistance WHERE oidref IN ({simbad.in_list(part)})"
            try:
                rows = simbad.query("SELECT oidref, dist, unit, minus_err, plus_err, method, bibcode " + where)
            except simbad.net.NetworkError as exc:
                if "rejected" not in str(exc):
                    raise
                ctx.log("SIMBAD: detailed distance query rejected, retrying with the basic columns")
                rows = simbad.query("SELECT oidref, dist, unit " + where)
            for r in rows:
                try:
                    found.setdefault(int(float(r["oidref"])), []).append(r)
                except (KeyError, ValueError):
                    continue
        for o in objects:
            oid = o.extra.get("simbad_oid")
            if oid is not None:
                o.extra["distance_measurements"] = found.get(oid, [])

    def resolve(self, obj: SkyObject, ctx: TaskContext) -> Distance | None:
        values = []
        for r in obj.extra.get("distance_measurements") or []:
            d = simbad.num(r.get("dist"))
            factor = UNIT_TO_PC.get(str(r.get("unit", "")).strip().lower())
            if d is None or d <= 0 or factor is None:
                continue
            minus = simbad.num(r.get("minus_err"))
            plus = simbad.num(r.get("plus_err"))
            err = None
            if minus is not None or plus is not None:
                err = (abs(minus or 0) + abs(plus or 0)) / (2 if minus is not None and plus is not None else 1)
                err *= factor
            values.append((d * factor, err, (r.get("method") or "").strip(), (r.get("bibcode") or "").strip()))
        if not values:
            return None
        methods = sorted({m for _, _, m, _ in values if m})
        if self.setting("combine") == "latest":
            pc, err, method, bib = max(values, key=lambda v: v[3][:4])
            source = f"SIMBAD, {method or 'published'} ({bib})"
        else:
            pcs = [v[0] for v in values]
            pc = statistics.median(pcs)
            if len(pcs) >= 3:
                err = statistics.median(abs(p - pc) for p in pcs) * 1.4826
            else:
                errs = [v[1] for v in values if v[1]]
                err = statistics.median(errs) if errs else None
            n = len(values)
            source = (f"SIMBAD, median of {n} published distances" if n > 1 else "SIMBAD, published distance")
            if methods:
                source += " (" + ", ".join(methods[:4]) + (", …" if len(methods) > 4 else "") + ")"
        return Distance(parsec_to_ly(pc), parsec_to_ly(err) if err else None, "published", source)
