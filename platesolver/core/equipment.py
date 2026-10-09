# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""The Equipment page: telescope focal length and camera pixel size.

Plate solvers are much faster when they know the image scale (arc-seconds per
pixel), which follows from these two numbers. Many files (JPG, TIFF, stacked
FITS) don't record them, so the values set here are used instead.
"""
from __future__ import annotations

from platesolver.core.models import ARCSEC_PER_RAD
from platesolver.core.settings import BOOL, FLOAT, INT, SettingField, SettingsSection


def image_scale(focal_length_mm: float, pixel_size_um: float) -> float | None:
    """Arc-seconds per pixel: 206.265 × pixel size [µm] / focal length [mm]."""
    if focal_length_mm and pixel_size_um and focal_length_mm > 0 and pixel_size_um > 0:
        return ARCSEC_PER_RAD * pixel_size_um * 1e-3 / focal_length_mm
    return None


class EquipmentSettings(SettingsSection):
    section_id = "equipment"
    name = "Equipment"
    description = ("Your telescope and camera. Telling the plate solver the image scale up front makes "
                   "solving much faster and avoids 'scale was inaccurate' warnings. Used for images that "
                   "don't record these values themselves.")

    def settings_schema(self) -> list[SettingField]:
        return [
            SettingField("focal_length", "Telescope focal length", FLOAT, 0.0,
                         minimum=0, maximum=20000, step=1, decimals=1, suffix=" mm",
                         help="Effective focal length, including any reducer or Barlow. 0 = unknown."),
            SettingField("pixel_size", "Camera pixel size", FLOAT, 0.0,
                         minimum=0, maximum=50, step=0.01, decimals=2, suffix=" µm",
                         help="From the camera's specifications. Multiply by the binning if you bin. 0 = unknown."),
            SettingField("sensor_width", "Camera sensor width", INT, 0, minimum=0, maximum=20000, step=1,
                         suffix=" px", help="Full sensor resolution, e.g. 3840 for an ASI585MC. 0 = unknown."),
            SettingField("sensor_height", "Camera sensor height", INT, 0, minimum=0, maximum=20000, step=1,
                         suffix=" px", help="E.g. 2160 for an ASI585MC. 0 = unknown."),
            SettingField("detect_drizzle", "Recognise drizzled images by their size", BOOL, True,
                         help="An image clearly larger than the sensor was drizzled (or enlarged): its pixels "
                              "cover less sky, so the scale is divided by 1.5, 2, 3 or 4. Needs the sensor size."),
            SettingField("lens_35mm", "Lens for photos without EXIF (35 mm equivalent)", FLOAT, 0.0,
                         minimum=0, maximum=2000, step=1, decimals=0, suffix=" mm",
                         help="For phone and camera-lens profiles: used when a photo has lost its EXIF data, "
                              "e.g. 26 for a phone's main camera. 0 = off. Only used when focal length and "
                              "pixel size above are 0."),
            SettingField("override_file", "Always use these values, even when the file has its own", BOOL, False,
                         help="Turn on if your capture software writes wrong focal length or pixel size values."),
        ]

    def describe(self, values: dict) -> str:
        """Live summary shown under the fields in the Settings dialog."""
        scale = image_scale(float(values.get("focal_length") or 0), float(values.get("pixel_size") or 0))
        if scale is None:
            return "Enter both values to see the image scale."
        text = f"Image scale: {scale:.2f}″ per pixel  (206.265 × pixel size ÷ focal length)"
        w, h = int(values.get("sensor_width") or 0), int(values.get("sensor_height") or 0)
        if w and h:
            text += (f"\nField of view: {w * scale / 3600:.2f}° × {h * scale / 3600:.2f}°"
                     f"  ·  2× drizzled: {scale / 2:.2f}″ per pixel")
        return text

    @staticmethod
    def migrate(store) -> None:
        """Values used to live on the General page; carry them over once."""
        for old, new in (("default_focal_length", "focal_length"), ("default_pixel_size", "pixel_size")):
            if store.has("general", old) and not store.has("equipment", new):
                store.set("equipment", new, store.get("general", old))


DRIZZLE_FACTORS = (1.5, 2.0, 3.0, 4.0)


def drizzle_factor(image_w: int, image_h: int, sensor_w: int, sensor_h: int) -> float:
    """1.0, or the drizzle/enlargement factor of an image clearly larger than the sensor.

    Cropping only makes an image smaller, so the factor is the smallest usual drizzle factor that
    is at least the size ratio: a cropped 2× drizzle (ratio 1.6-2.0) gives 2.
    """
    if not (sensor_w and sensor_h and image_w and image_h):
        return 1.0
    ratio = max(image_w, image_h) / max(sensor_w, sensor_h)
    if ratio < 1.15:
        return 1.0
    for f in DRIZZLE_FACTORS:
        if ratio <= f + 0.02:
            return f
    return round(ratio)
