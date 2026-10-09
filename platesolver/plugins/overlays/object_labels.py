# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Outlines and names of the catalogued objects, drawn on the image."""
from __future__ import annotations

import math

from platesolver.core.formatting import format_ly
from platesolver.core.interfaces import OverlayLayer
from platesolver.core.settings import BOOL, INT, STR, SettingField

DEFAULT_COLORS = {"galaxy": "#ffd166", "nebula": "#ff7aa8", "cluster": "#7bdff2", "star": "#e8e8e8",
                  "other": "#b9b4ff"}


def _num(painter, name: str, default: float) -> float:
    v = getattr(painter, name, default)
    return float(v) if isinstance(v, (int, float)) else default


class ObjectLabelsOverlay(OverlayLayer):
    plugin_id = "object_labels"
    name = "Object labels"
    description = "Marks each object found in the image with its outline and name."
    priority = 10

    def settings_schema(self):
        return [
            SettingField("outlines", "Draw object outlines", BOOL, True,
                         help="Galaxies and nebulae are drawn with their catalogued size and orientation."),
            SettingField("labels", "Show names", BOOL, True),
            SettingField("label_size", "Name text size", INT, 14, minimum=6, maximum=48, suffix=" px",
                         help="Size of the object names on screen. Exports scale it with the picture (Export "
                              "settings › Label size in exports). The other overlays (grid, compass, constellations) use "
                              "General › Overlay text size."),
            SettingField("common_names", "Use common names in labels (e.g. Orion Nebula)", BOOL, True),
            SettingField("distances", "Show distance in labels", BOOL, False),
            SettingField("stars", "Label stars", BOOL, True),
            SettingField("hide_overlaps", "Hide labels that would overlap", BOOL, True,
                         help="The most prominent objects keep their labels; zoom in to see the rest."),
            SettingField("color_galaxy", "Galaxy colour", STR, DEFAULT_COLORS["galaxy"]),
            SettingField("color_nebula", "Nebula colour", STR, DEFAULT_COLORS["nebula"]),
            SettingField("color_cluster", "Star cluster colour", STR, DEFAULT_COLORS["cluster"]),
            SettingField("color_star", "Star colour", STR, DEFAULT_COLORS["star"]),
            SettingField("color_other", "Other objects colour", STR, DEFAULT_COLORS["other"]),
        ]

    def color_for(self, category: str) -> str:
        return self.setting(f"color_{category}") or DEFAULT_COLORS.get(category, DEFAULT_COLORS["other"])

    def render(self, painter, image, solution, objects):
        if not objects:
            return
        scale = solution.pixel_scale_arcsec or 1.0
        spx = float(getattr(painter, "screen_px", 1.0))       # image pixels per screen pixel
        base = max(6.0 * spx, min(image.width, image.height) * 0.004)
        decimals = int(getattr(painter, "ly_decimals", 1))
        avoid = bool(self.setting("hide_overlaps"))
        placed: list[tuple[float, float, float, float]] = []   # label boxes already drawn
        # the painter scales all text by General › Overlay text size; object names have their own size
        font = (float(self.setting("label_size") or 14) * _num(painter, "size_scale", 1.0)
                / max(_num(painter, "font_scale", 1.0), 1e-6))

        others = self._others_mode()

        def extent(text):
            if hasattr(painter, "text_extent"):
                return painter.text_extent(text, font)
            return len(text) * font * 0.62 * spx, font * 1.3 * spx

        for obj in objects:   # most prominent first, so they win the space
            if obj.x is None or obj.y is None:
                continue
            if obj.category == "star" and not self.setting("stars"):
                continue
            color = self.color_for(obj.category)
            width = 1.4
            if others != "show" and obj.extra.get("visible") is False:
                if others == "hide":
                    continue
                color, width = dim(color), 1.0          # too faint to be seen in this image
            rx = ry = base * (0.8 if obj.category == "star" else 1.3)
            angle = 0.0
            if obj.size_arcmin and obj.category != "star":
                rx = max(rx, obj.size_arcmin * 60.0 / 2.0 / scale)
                ry = max(base, (obj.size_minor_arcmin or obj.size_arcmin) * 60.0 / 2.0 / scale)
                angle = self._major_axis_angle(obj, solution)
            if self.setting("outlines"):
                painter.ellipse(obj.x, obj.y, rx, ry, angle, color, width)
            if not self.setting("labels"):
                continue
            text = obj.display_name if self.setting("common_names") else obj.name
            if obj.category == "star" and not self.setting("common_names"):
                text = obj.common_name or obj.name
            if self.setting("distances") and obj.distance:
                text += f"  ·  {format_ly(obj.distance.light_years, decimals)}"
            tw, th = extent(text)
            r = max(rx, ry)
            huge = r > 0.35 * min(image.width, image.height)
            gap = 5 * spx
            off = (0.0 if huge else r * 0.72) + gap
            # candidate spots: right, left, below, above (a huge object gets its label near its centre)
            cands = [(obj.x + off, obj.y - (0 if huge or r < base * 2 else r * 0.72), "left"),
                     (obj.x - off, obj.y - (0 if huge or r < base * 2 else r * 0.72), "right"),
                     (obj.x, obj.y + (r if not huge else 0) + th, "center"),
                     (obj.x, obj.y - (r if not huge else 0) - th, "center")]
            if huge:
                cands = [(obj.x + gap, obj.y + 3 * th, "left")] + cands
            chosen = None
            for lx, ly, anchor in cands:
                lx = min(max(lx, gap), image.width - gap)
                ly = min(max(ly, th / 2), image.height - th / 2)
                left = lx if anchor == "left" else (lx - tw if anchor == "right" else lx - tw / 2)
                box = (left, ly - th / 2, left + tw, ly + th / 2)
                if box[0] < 0 or box[2] > image.width:
                    continue
                if not avoid or not any(_overlap(box, b) for b in placed):
                    chosen = (lx, ly, anchor, box)
                    break
            if chosen is None:
                continue   # no free spot at this zoom level; zooming in reveals it
            lx, ly, anchor, box = chosen
            placed.append(box)
            painter.text(lx, ly, text, color, size=font, anchor=anchor)

    def _others_mode(self) -> str:
        """Settings › Visible stars: 'show', 'dim' or 'hide' objects not visible in the image."""
        store = getattr(getattr(self, "settings", None), "store", None)
        if store is None:
            return "show"
        try:
            from platesolver.core.visibility import VisibilitySettings
            sec = store.section(VisibilitySettings())          # stored values, else the defaults
            if not sec.get("enabled"):
                return "show"
            return str(sec.get("others") or "show")
        except Exception:
            return "show"

    @staticmethod
    def _major_axis_angle(obj, solution) -> float:
        """Screen angle (degrees, clockwise from +x) of the object's major axis."""
        if obj.position_angle_deg is None:
            return 0.0
        pa = math.radians(obj.position_angle_deg)
        step = (obj.size_arcmin or 1.0) / 60.0 / 2.0
        dec = obj.dec_deg + step * math.cos(pa)
        ra = obj.ra_deg + step * math.sin(pa) / max(math.cos(math.radians(obj.dec_deg)), 1e-3)
        x2, y2 = solution.radec_to_pixel(ra, dec)
        return math.degrees(math.atan2(y2 - obj.y, x2 - obj.x))


def dim(color: str) -> str:
    """The same colour, mostly transparent (#AARRGGBB)."""
    c = (color or "").lstrip("#")
    if len(c) == 8:
        c = c[2:]
    return f"#66{c}" if len(c) == 6 else color


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
