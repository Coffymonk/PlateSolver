# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""RA/Dec coordinate grid with labels, spaced automatically to suit the field of view."""
from __future__ import annotations

import math

import numpy as np

from platesolver.core import skygeom
from platesolver.core.interfaces import OverlayLayer
from platesolver.core.settings import BOOL, FLOAT, STR, SettingField

DEC_STEPS_ARCMIN = [1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1800]
RA_STEPS_SECONDS = [1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200, 10800]


def _pick(steps, span, lines):
    for s in steps:
        if span / s <= lines:
            return s
    return steps[-1]


def format_dec_label(dec: float, step_arcmin: float) -> str:
    sign = "-" if dec < -1e-9 else "+"
    total = round(abs(dec) * 60)
    d, m = divmod(total, 60)
    return f"{sign}{d}°" if step_arcmin >= 60 and m == 0 else f"{sign}{d}°{m:02d}′"


def format_ra_label(ra: float, step_seconds: float) -> str:
    total = round((ra % 360.0) / 15.0 * 3600.0) % 86400
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if step_seconds >= 3600 and m == 0:
        return f"{h}h"
    if step_seconds >= 60 and s == 0:
        return f"{h}h{m:02d}m"
    return f"{h}h{m:02d}m{s:02d}s"


class CoordinateGrid(OverlayLayer):
    plugin_id = "coordinate_grid"
    name = "RA/Dec grid"
    description = "Lines of equal right ascension and declination, with labels at the image edges."
    priority = 50
    visible_by_default = False

    def settings_schema(self):
        return [
            SettingField("lines", "Approximate number of lines across the image", FLOAT, 6, minimum=2,
                         maximum=20, step=1, decimals=0),
            SettingField("labels", "Show labels", BOOL, True),
            SettingField("color", "Colour", STR, "#5b8fc7"),
        ]

    def render(self, painter, image, solution, objects):
        w, h = image.width, image.height
        ra0, dec0 = solution.center_ra_deg, solution.center_dec_deg
        color = self.setting("color")
        n_lines = float(self.setting("lines"))

        # sky range covered by the frame (sampled along its border)
        t = np.linspace(0, 1, 60)
        bx = np.concatenate([t * (w - 1), np.full_like(t, w - 1), t * (w - 1), np.zeros_like(t)])
        by = np.concatenate([np.zeros_like(t), t * (h - 1), np.full_like(t, h - 1), t * (h - 1)])
        ra, dec = solution.wcs.pixel_to_world_values(bx, by)
        dra = ((np.asarray(ra) - ra0 + 180.0) % 360.0) - 180.0
        dec = np.asarray(dec)
        ra_lo, ra_hi = ra0 + dra.min(), ra0 + dra.max()
        dec_lo, dec_hi = float(dec.min()), float(dec.max())
        for pole in (90.0, -90.0):
            px, py = solution.radec_to_pixel(0.0, pole)
            if math.isfinite(px) and 0 <= px < w and 0 <= py < h:
                ra_lo, ra_hi = 0.0, 360.0
                dec_lo, dec_hi = (dec_lo, 90.0) if pole > 0 else (-90.0, dec_hi)

        dec_step = _pick(DEC_STEPS_ARCMIN, (dec_hi - dec_lo) * 60.0, n_lines) / 60.0
        ra_span_s = (ra_hi - ra_lo) / 15.0 * 3600.0
        ra_step_s = _pick(RA_STEPS_SECONDS, ra_span_s, n_lines)
        ra_step = ra_step_s * 15.0 / 3600.0
        pad_ra = (ra_hi - ra_lo) * 0.1 + ra_step
        pad_dec = (dec_hi - dec_lo) * 0.1 + dec_step
        size = 10

        # lines of constant declination
        d = math.ceil(dec_lo / dec_step) * dec_step
        while d <= dec_hi + 1e-9:
            if abs(d) < 90:
                ras = np.linspace(ra_lo - pad_ra, ra_hi + pad_ra, 300)
                lines = skygeom.project(solution, ras % 360.0, np.full_like(ras, d), margin=0.05)
                for line in lines:
                    painter.polyline(line, color, 1.0)
                if self.setting("labels"):
                    pts = skygeom.visible_points(lines, w, h)
                    if pts:
                        x, y = min(pts, key=lambda p: p[0])
                        painter.text(x + 4, y - 8, format_dec_label(d, dec_step * 60), color, size)
            d += dec_step

        # lines of constant right ascension
        r = math.ceil(ra_lo / ra_step) * ra_step
        count = 0
        while r <= ra_hi + 1e-9 and count < 200:
            decs = np.linspace(max(-89.999, dec_lo - pad_dec), min(89.999, dec_hi + pad_dec), 300)
            lines = skygeom.project(solution, np.full_like(decs, r % 360.0), decs, margin=0.05)
            for line in lines:
                painter.polyline(line, color, 1.0)
            if self.setting("labels"):
                pts = skygeom.visible_points(lines, w, h)
                if pts:
                    x, y = max(pts, key=lambda p: p[1])
                    painter.text(x + 4, y - 10, format_ra_label(r, ra_step_s), color, size)
            r += ra_step
            count += 1
