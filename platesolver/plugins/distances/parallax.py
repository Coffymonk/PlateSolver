# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Distance from trigonometric parallax (mostly Gaia): d [pc] = 1000 / parallax [mas]."""
from __future__ import annotations

from platesolver.core.formatting import parsec_to_ly
from platesolver.core.interfaces import DistanceResolver
from platesolver.core.models import Distance, SkyObject
from platesolver.core.plugin import TaskContext
from platesolver.core.settings import FLOAT, SettingField


class ParallaxDistance(DistanceResolver):
    plugin_id = "parallax"
    name = "Parallax"
    description = ("The most direct distance measurement, used for stars and nearby star clusters. "
                   "Parallaxes come with the catalogue data (mostly from the Gaia satellite).")
    priority = 10

    def settings_schema(self):
        return [
            SettingField("max_rel_error", "Largest accepted parallax error", FLOAT, 20.0, minimum=1, maximum=100,
                         step=5, decimals=0, suffix=" %",
                         help="Parallaxes less precise than this are skipped, so another source is used instead. "
                              "Above about 20 % the simple 1/parallax distance becomes unreliable."),
        ]

    def resolve(self, obj: SkyObject, ctx: TaskContext) -> Distance | None:
        plx = obj.extra.get("plx_mas")
        err = obj.extra.get("plx_err_mas")
        if not plx or plx <= 0:
            return None
        if err is not None and err / plx > float(self.setting("max_rel_error")) / 100.0:
            return None
        pc = 1000.0 / plx
        unc = None
        if err:
            # half the spread between the near and far ends of the error bar
            near, far = 1000.0 / (plx + err), 1000.0 / max(plx - err, 1e-9)
            unc = parsec_to_ly((far - near) / 2.0)
        text = f"parallax {plx:.3f}" + (f" ± {err:.3f}" if err else "") + " mas"
        return Distance(parsec_to_ly(pc), unc, "parallax", f"SIMBAD, {text}")
