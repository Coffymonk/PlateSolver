# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Camera catalogue for profiles: astro cameras by manufacturer, DSLR/mirrorless bodies, phone lenses.

Astro cameras of different brands mostly use the same Sony sensors, so each model refers to a sensor,
and the sensor gives pixel size and resolution. Brands sometimes crop a few rows or columns
(e.g. 3856 instead of 3840 pixels); that makes no difference to plate solving.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# sensor: (pixel size µm, width px, height px, short description)
SENSORS = {
    "IMX585": (2.9, 3840, 2160, "8.3 MP, 1/1.2\""),
    "IMX485": (2.9, 3840, 2160, "8.3 MP, 1/1.2\""),
    "IMX678": (2.0, 3840, 2160, "8.3 MP, 1/1.8\""),
    "IMX462": (2.9, 1920, 1080, "2.1 MP, 1/2.8\""),
    "IMX290": (2.9, 1936, 1096, "2.1 MP, 1/2.8\""),
    "IMX662": (2.9, 1920, 1080, "2.1 MP, 1/2.8\""),
    "IMX224": (3.75, 1304, 976, "1.3 MP, 1/3\""),
    "IMX174": (5.86, 1936, 1216, "2.3 MP, 1/1.2\""),
    "IMX178": (2.4, 3096, 2080, "6.4 MP, 1/1.8\""),
    "IMX183": (2.4, 5496, 3672, "20 MP, 1\""),
    "IMX294": (4.63, 4144, 2822, "11.7 MP, 4/3\""),
    "IMX533": (3.76, 3008, 3008, "9 MP square, 1\""),
    "IMX571": (3.76, 6248, 4176, "26 MP, APS-C"),
    "IMX455": (3.76, 9576, 6388, "61 MP, full frame"),
    "IMX410": (5.94, 6072, 4042, "24 MP, full frame"),
    "IMX071": (4.78, 4944, 3284, "16 MP, APS-C"),
    "IMX482": (5.8, 1920, 1080, "2.1 MP, 1/1.2\""),
    "IMX432": (9.0, 1608, 1104, "1.8 MP, 1.1\""),
    "MN34230": (3.8, 4656, 3520, "16 MP, 4/3\" (Panasonic)"),
    "AR0130": (3.75, 1280, 960, "1.2 MP, 1/3\" (onsemi)"),
}

ASTRO_GROUP = "Astro cameras"
DSLR_GROUP = "DSLR / mirrorless"
PHONE_GROUP = "Smartphones"

# (manufacturer, model, sensor)
ASTRO_MODELS = [
    ("ZWO", "ASI120MM / MC", "AR0130"), ("ZWO", "ASI174MM / MC", "IMX174"),
    ("ZWO", "ASI178MM / MC", "IMX178"), ("ZWO", "ASI183MM / MC Pro", "IMX183"),
    ("ZWO", "ASI224MC", "IMX224"), ("ZWO", "ASI290MM / MC", "IMX290"),
    ("ZWO", "ASI294MC Pro", "IMX294"), ("ZWO", "ASI432MM", "IMX432"),
    ("ZWO", "ASI462MC", "IMX462"), ("ZWO", "ASI482MC", "IMX482"),
    ("ZWO", "ASI485MC", "IMX485"), ("ZWO", "ASI533MC / MM Pro", "IMX533"),
    ("ZWO", "ASI585MC / MM Pro", "IMX585"), ("ZWO", "ASI662MC", "IMX662"),
    ("ZWO", "ASI678MC / MM", "IMX678"), ("ZWO", "ASI071MC Pro", "IMX071"),
    ("ZWO", "ASI1600MM / MC Pro", "MN34230"), ("ZWO", "ASI2400MC Pro", "IMX410"),
    ("ZWO", "ASI2600MC / MM Pro", "IMX571"), ("ZWO", "ASI6200MC / MM Pro", "IMX455"),
    ("Player One", "Mars-C", "IMX462"), ("Player One", "Uranus-C / M Pro", "IMX585"),
    ("Player One", "Ares-C / M Pro", "IMX533"), ("Player One", "Poseidon-C / M Pro", "IMX571"),
    ("Player One", "Zeus-455M Pro", "IMX455"), ("Player One", "Apollo-M Max", "IMX432"),
    ("QHYCCD", "QHY5III462C", "IMX462"), ("QHYCCD", "QHY5III585C", "IMX585"),
    ("QHYCCD", "QHY163M / C", "MN34230"), ("QHYCCD", "QHY183M / C", "IMX183"),
    ("QHYCCD", "QHY294M / C Pro", "IMX294"), ("QHYCCD", "QHY533M / C", "IMX533"),
    ("QHYCCD", "QHY268M / C", "IMX571"), ("QHYCCD", "QHY410C", "IMX410"),
    ("QHYCCD", "QHY600M / C", "IMX455"),
    ("Svbony", "SV305", "IMX290"), ("Svbony", "SV405CC", "IMX294"),
    ("Svbony", "SV605CC", "IMX533"), ("Svbony", "SV705C", "IMX585"),
    ("Altair", "Hypercam 294C Pro", "IMX294"), ("Altair", "Hypercam 26C", "IMX571"),
    ("Atik", "Horizon", "MN34230"), ("Atik", "Apx26", "IMX571"), ("Atik", "Apx60", "IMX455"),
    ("Moravian", "C3-26000", "IMX571"), ("Moravian", "C3-61000", "IMX455"),
]
# ToupTek (and its OEM brands) name models in many ways; listed by sensor, which is what matters.
for _s in ("IMX585", "IMX678", "IMX462", "IMX183", "IMX294", "IMX533", "IMX571", "IMX410", "IMX455"):
    ASTRO_MODELS.append(("ToupTek", f"{_s} camera ({SENSORS[_s][3]})", _s))
for _s in SENSORS:
    ASTRO_MODELS.append(("Other brand – by sensor", f"Sony/other {_s} ({SENSORS[_s][3]})", _s))

# DSLR / mirrorless: (brand, model, pixel µm, width, height)
DSLR_MODELS = [
    ("Canon", "EOS 550D / 600D / 650D / 700D", 4.3, 5184, 3456),
    ("Canon", "EOS 6D", 6.54, 5472, 3648),
    ("Canon", "EOS R / Ra", 5.36, 6720, 4480),
    ("Canon", "EOS R6", 6.56, 5472, 3648),
    ("Nikon", "D5300 / D5600", 3.92, 6000, 4000),
    ("Nikon", "D810A", 4.88, 7360, 4912),
    ("Nikon", "Z6 / Z6 II", 5.94, 6048, 4024),
    ("Sony", "α7 III", 5.93, 6000, 4000),
    ("Sony", "α7S / α7S II", 8.4, 4240, 2832),
    ("Fujifilm", "X-T3 / X-T4 / X-T30 (26 MP)", 3.76, 6240, 4160),
]

# Phones: (model, 35 mm-equivalent focal length)
PHONE_LENSES = [
    ("Main camera, most iPhones and Android phones (26 mm equivalent)", 26.0),
    ("Main camera, iPhone 14 Pro and newer (24 mm equivalent)", 24.0),
    ("Ultra-wide camera, 0.5× (13 mm equivalent)", 13.0),
    ("Telephoto 3× (77 mm equivalent)", 77.0),
]


@dataclass(frozen=True)
class Camera:
    id: str
    group: str            # ASTRO_GROUP / DSLR_GROUP / PHONE_GROUP
    maker: str
    model: str
    pixel_um: float = 0.0
    width: int = 0
    height: int = 0
    lens_35mm: float = 0.0
    sensor: str = ""

    @property
    def label(self) -> str:
        return f"{self.maker} {self.model}" if self.maker else self.model

    def describe(self) -> str:
        if self.lens_35mm:
            return f"scale from the photo's EXIF, or {self.lens_35mm:g} mm full-frame equivalent without it"
        s = f"{self.pixel_um:g} µm pixels, {self.width} × {self.height}"
        return s + (f" ({self.sensor})" if self.sensor else "")


def _slug(*parts: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", " ".join(parts).lower()).strip("-")


def all_cameras() -> list[Camera]:
    out = []
    for maker, model, sensor in ASTRO_MODELS:
        px, w, h, _ = SENSORS[sensor]
        out.append(Camera(_slug(maker, model), ASTRO_GROUP, maker, model, px, w, h, sensor=sensor))
    for maker, model, px, w, h in DSLR_MODELS:
        out.append(Camera(_slug(maker, model), DSLR_GROUP, maker, model, px, w, h))
    for model, f35 in PHONE_LENSES:
        out.append(Camera(_slug("phone", str(f35)), PHONE_GROUP, "", model, lens_35mm=f35))
    return out


def get(camera_id: str) -> Camera | None:
    return next((c for c in all_cameras() if c.id == camera_id), None)


def grouped() -> list[tuple[str, list[tuple[str, list[Camera]]]]]:
    """[(group, [(maker, [cameras])])] in display order."""
    out: list = []
    for group in (ASTRO_GROUP, DSLR_GROUP, PHONE_GROUP):
        makers: dict[str, list[Camera]] = {}
        for c in all_cameras():
            if c.group == group:
                makers.setdefault(c.maker, []).append(c)
        out.append((group, list(makers.items())))
    return out


_MODEL_TOKEN = re.compile(r"(ASI\s?\d{3,4}|QHY\s?5?I*\d{3,4}|SV\s?\d{3}|IMX\s?\d{3}|"
                          r"URANUS|ARES|POSEIDON|MARS|ZEUS|APOLLO|C3-\d{5}|APX\d{2}|HORIZON)", re.I)


def match_instrument(text: str) -> Camera | None:
    """Camera from a FITS INSTRUME value, e.g. 'ZWO ASI585MC Pro' -> ASI585MC / MM Pro."""
    if not text:
        return None
    t = text.upper().replace(" ", "")
    for c in all_cameras():
        if c.group != ASTRO_GROUP or c.maker.startswith("Other"):
            continue
        key = c.model.upper().replace(" ", "").split("/")[0]
        key = re.sub(r"(PRO|MM|MC|M|C)$", "", key) or key
        if len(key) >= 4 and key in t:
            return c
    return None
