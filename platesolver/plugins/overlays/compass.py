# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""North/East compass drawn on solved images."""
from __future__ import annotations

import math

from platesolver.core.interfaces import OverlayLayer
from platesolver.core.settings import CHOICE, FLOAT, STR, SettingField


class CompassOverlay(OverlayLayer):
    plugin_id = "compass"
    name = "Compass (North / East)"
    description = "Arrows showing which way north and east point in the solved image."
    priority = 90

    def settings_schema(self):
        return [
            SettingField("corner", "Position", CHOICE, "top-left", choices=[
                ("top-left", "Top left"), ("top-right", "Top right"),
                ("bottom-left", "Bottom left"), ("bottom-right", "Bottom right"), ("center", "Centre")]),
            SettingField("size", "Arrow length", FLOAT, 0.08, minimum=0.02, maximum=0.4, step=0.01,
                         decimals=2, help="As a fraction of the image's shorter side."),
            SettingField("color", "Colour", STR, "#ffb347", help="Any colour name or #rrggbb."),
        ]

    def render(self, painter, image, solution, objects):
        w, h = image.width, image.height
        length = min(w, h) * float(self.setting("size"))
        margin = length * 1.5
        corner = self.setting("corner")
        x = margin if "left" in corner else (w - margin if "right" in corner else w / 2)
        y = margin if "top" in corner else (h - margin if "bottom" in corner else h / 2)
        ra, dec = solution.pixel_to_radec(x, y)
        step = max(solution.pixel_scale_arcsec or 1.0, 0.01) * length / 3600.0
        color = self.setting("color")
        for label, (ra2, dec2) in (
            ("N", (ra, min(dec + step, 89.9999))),
            ("E", (ra + step / max(math.cos(math.radians(dec)), 1e-3), dec)),
        ):
            x2, y2 = solution.radec_to_pixel(ra2, dec2)
            dx, dy = x2 - x, y2 - y
            norm = math.hypot(dx, dy) or 1.0
            ex, ey = x + dx / norm * length, y + dy / norm * length
            painter.arrow(x, y, ex, ey, color, 2.0)
            painter.text(x + dx / norm * length * 1.18, y + dy / norm * length * 1.18, label, color,
                         size=13, anchor="center")
