# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""The General page of the Settings dialog (settings that belong to no single plugin)."""
from __future__ import annotations

from platesolver.core.settings import BOOL, FLOAT, INT, SettingField, SettingsSection


class GeneralSettings(SettingsSection):
    section_id = "general"
    name = "General"
    description = "Display settings. Telescope and camera values are on the Equipment page."

    @staticmethod
    def migrate(store) -> None:
        """0.9.7: images are no longer solved on opening by default (crop first, then Solve). Done once,
        so a later choice in Settings › General is kept."""
        if not store.get("general", "auto_solve_097"):
            store.set("general", "auto_solve", False)
            store.set("general", "auto_solve_097", True)

    def settings_schema(self) -> list[SettingField]:
        return [
            SettingField("auto_solve", "Solve automatically when an image is opened", BOOL, False,
                         help="Off: the image is shown and you start solving with Solve (F5), so you can "
                              "crop or rotate it first. On: solving starts as soon as the image is open."),
            SettingField("ly_decimals", "Decimals for light-years", INT, 1, minimum=0, maximum=6,
                         help="Number of decimals shown for distances in light-years."),
            SettingField("auto_stretch", "Auto-stretch linear images", BOOL, True,
                         help="Brighten unstretched FITS/TIFF data for display. Solving always uses the original data."),
            SettingField("stretch_background", "Stretch target background", FLOAT, 0.20,
                         minimum=0.05, maximum=0.5, step=0.01, decimals=2,
                         help="Brightness of the sky background after auto-stretch (0.05 dark – 0.5 bright)."),
            SettingField("overlay_font_size", "Overlay text size", INT, 11, minimum=6, maximum=32, suffix=" px",
                         help="Grid, compass and constellation text. Object names have their own size: "
                              "Overlays › Object labels › Name text size."),
            SettingField("hover_card", "Show an information card when hovering over an object", BOOL, True,
                         help="Point at an object on the image to see its distance, size and more."),
            SettingField("hover_size", "Card: size in light-years", BOOL, True,
                         help="Distance × angular size. Approximate, since catalogued sizes depend on how "
                              "faint an edge was measured."),
            SettingField("hover_light", "Card: when the light left the object", BOOL, True,
                         help="E.g. 'The light in your image left it around the year 680'."),
            SettingField("size_column", "Show a Size column in the object list", BOOL, True),
            SettingField("overlay_follows_filter", "Only mark objects that pass the list's search and filter",
                         BOOL, True, help="When off, all objects stay marked on the image while you filter the list."),
            SettingField("click_opens", "Double-clicking an object on the image opens its information", BOOL, True,
                         help="When off, double-clicking the image always fits it to the window."),
        ]
