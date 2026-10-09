# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Distance of galaxies from their redshift (Hubble flow), when nothing better is known."""
from __future__ import annotations

from platesolver.core.formatting import parsec_to_ly
from platesolver.core.interfaces import DistanceResolver
from platesolver.core.models import Distance, SkyObject
from platesolver.core.plugin import TaskContext
from platesolver.core.settings import CHOICE, FLOAT, SettingField


class RedshiftDistance(DistanceResolver):
    plugin_id = "redshift"
    name = "Redshift (Hubble flow)"
    description = ("Estimates a galaxy's distance from how fast the expansion of the universe carries it away. "
                   "Only used when no direct measurement exists. Unreliable for very nearby galaxies, whose own "
                   "motion dominates, so those are skipped.")
    priority = 30

    def settings_schema(self):
        return [
            SettingField("h0", "Hubble constant", FLOAT, 70.0, minimum=50, maximum=90, step=0.5, decimals=1,
                         suffix=" km/s/Mpc"),
            SettingField("measure", "Distance measure", CHOICE, "light_travel", choices=[
                ("light_travel", "Light-travel distance (how long the light has travelled)"),
                ("comoving", "Comoving distance (how far away the galaxy is now)")],
                help="The two agree for nearby galaxies and differ for distant ones."),
            SettingField("min_z", "Ignore redshifts below", FLOAT, 0.003, minimum=0, maximum=0.1, step=0.001,
                         decimals=4, help="Below about z = 0.003 (≈ 900 km/s) a galaxy's own motion swamps the "
                                          "expansion, so the estimate would be meaningless."),
        ]

    def resolve(self, obj: SkyObject, ctx: TaskContext) -> Distance | None:
        z = obj.extra.get("redshift")
        if obj.category != "galaxy" or z is None or z < float(self.setting("min_z")) or z > 20:
            return None
        from astropy.cosmology import FlatLambdaCDM
        import astropy.units as u

        cosmo = FlatLambdaCDM(H0=float(self.setting("h0")), Om0=0.3)
        lookback = float(cosmo.lookback_time(z).to(u.yr).value)
        size_distance = float(cosmo.angular_diameter_distance(z).to(u.lyr).value)
        if self.setting("measure") == "comoving":
            ly = float(cosmo.comoving_distance(z).to(u.lyr).value)
            what = "comoving"
        else:
            ly = lookback   # light that travelled for t years has covered t light-years
            what = "light-travel"
        return Distance(ly, None, "redshift",
                        f"estimate from redshift z = {z:.4f} ({what}, H₀ = {float(self.setting('h0')):g})",
                        angular_diameter_ly=size_distance, lookback_years=lookback)
