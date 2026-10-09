# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Constellation figures, names and boundaries.

Data: d3-celestial by Olaf Frohn (BSD-3-Clause), bundled in platesolver/data/constellations.json.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from platesolver.core import skygeom
from platesolver.core.interfaces import OverlayLayer
from platesolver.core.settings import BOOL, CHOICE, STR, SettingField

DATA = Path(__file__).resolve().parents[2] / "data" / "constellations.json"
_data: dict | None = None


def load() -> dict:
    global _data
    if _data is None:
        _data = json.loads(DATA.read_text(encoding="utf-8"))
    return _data


class ConstellationsOverlay(OverlayLayer):
    plugin_id = "constellations"
    name = "Constellations"
    description = ("Constellation figures, names and official boundaries. Most useful for wide-field images; "
                   "in a narrow field you may only see a boundary or nothing at all.")
    priority = 60
    visible_by_default = False

    def settings_schema(self):
        return [
            SettingField("lines", "Draw constellation figures", BOOL, True),
            SettingField("names", "Show constellation names", BOOL, True),
            SettingField("borders", "Draw constellation boundaries", BOOL, False),
            SettingField("language", "Names in", CHOICE, "la", choices=[("la", "Latin (Orion, Ursa Major)"),
                                                                       ("en", "English (Orion, Great Bear)")]),
            SettingField("line_color", "Figure colour", STR, "#8fb3d9"),
            SettingField("name_color", "Name colour", STR, "#b9d3ee"),
            SettingField("border_color", "Boundary colour", STR, "#6c6f9a"),
        ]

    def render(self, painter, image, solution, objects):
        data = load()
        w, h = image.width, image.height
        ra0, dec0 = solution.center_ra_deg, solution.center_dec_deg
        reach = math.hypot(solution.fov_width_deg, solution.fov_height_deg) / 2.0 + 25.0
        visible_by_const: dict[str, list[tuple[float, float]]] = {}

        if self.setting("borders"):
            color = self.setting("border_color")
            for seg in data["borders"]:
                pts = np.array(seg)
                if skygeom.angular_distance_deg(pts[:, 0], pts[:, 1], ra0, dec0).min() > reach + 20:
                    continue
                ra, dec = skygeom.radec_path(seg, step_deg=0.2)
                for line in skygeom.project(solution, ra, dec, margin=0.05):
                    painter.polyline(line, color, 1.0)

        line_color = self.setting("line_color")
        for abbr, segments in data["lines"].items():
            for seg in segments:
                pts = np.array(seg)
                if skygeom.angular_distance_deg(pts[:, 0], pts[:, 1], ra0, dec0).min() > reach:
                    continue
                ra, dec = skygeom.great_circle_path(seg, step_deg=0.2)
                lines = skygeom.project(solution, ra, dec, margin=0.05)
                if self.setting("lines"):
                    for line in lines:
                        painter.polyline(line, line_color, 1.3)
                visible_by_const.setdefault(abbr, []).extend(skygeom.visible_points(lines, w, h))

        if self.setting("names"):
            lang = self.setting("language")
            color = self.setting("name_color")
            for abbr, info in data["names"].items():
                label = info.get(lang) or info.get("la") or abbr
                x, y = solution.radec_to_pixel(info["ra"], info["dec"])
                on_image = math.isfinite(x) and 0 <= x < w and 0 <= y < h and \
                    skygeom.angular_distance_deg(np.array([info["ra"]]), np.array([info["dec"]]), ra0, dec0)[0] < 80
                if not on_image:
                    pts = visible_by_const.get(abbr)
                    if not pts:
                        continue
                    x = sum(p[0] for p in pts) / len(pts)
                    y = sum(p[1] for p in pts) / len(pts)
                painter.text(x, y, label.upper(), color, size=13, anchor="center")
