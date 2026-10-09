# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Data passed between the pipeline stages and plugins."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

ARCSEC_PER_RAD = 206264.806


@dataclass
class SolveHints:
    """What the file tells us about where the telescope was pointing.

    Any field may be None. Solvers use what is there to speed things up.
    """
    ra_deg: float | None = None
    dec_deg: float | None = None
    focal_length_mm: float | None = None
    pixel_size_um: float | None = None
    pixel_scale_arcsec: float | None = None   # explicit scale, if the file gives one
    source: dict[str, str] = field(default_factory=dict)  # which header key each hint came from
    position_hint: str = ""     # set when the position is a user/file-name hint, not from the file itself
    position_exact: bool = False   # position and scale known precisely (a refining step): one quick try only

    @property
    def has_position(self) -> bool:
        return self.ra_deg is not None and self.dec_deg is not None

    def scale_arcsec(self) -> float | None:
        if self.pixel_scale_arcsec:
            return self.pixel_scale_arcsec
        if self.focal_length_mm and self.pixel_size_um:
            return ARCSEC_PER_RAD * self.pixel_size_um * 1e-3 / self.focal_length_mm
        return None

    def scale_origin(self) -> str:
        """Plain-words origin of the expected image scale, for log messages."""
        if self.pixel_scale_arcsec:
            return self.source.get("scale", "the file")
        fl, px = self.source.get("focal_length"), self.source.get("pixel_size")
        if not (self.focal_length_mm and self.pixel_size_um):
            return ""
        what = f"{self.focal_length_mm:g} mm, {self.pixel_size_um:g} µm"
        return f"{fl}: {what}" if fl == px else f"focal length from {fl}, pixel size from {px}: {what}"

    def fov_height_deg(self, height_px: int) -> float | None:
        s = self.scale_arcsec()
        return s * height_px / 3600.0 if s else None


@dataclass
class ImageData:
    """A loaded image, already turned the right way up for display.

    `data` is float32, shape (H, W) for mono or (H, W, 3) for colour.
    Pixel (x, y) means column x, row y, with row 0 at the top of the screen.
    Every WCS in the program uses this same pixel convention.
    """
    path: Path
    format: str                                # "FITS", "TIFF", "JPEG", ...
    data: np.ndarray
    is_linear: bool = False                    # linear data gets auto-stretched for display
    bit_depth: str = ""
    header: dict[str, Any] = field(default_factory=dict)
    header_wcs: Any = None                     # astropy WCS already in the file, if any
    hints: SolveHints = field(default_factory=SolveHints)
    notes: list[str] = field(default_factory=list)
    rows_flipped: bool = False                 # True when the file stores rows bottom-up (FITS standard)
    star_count: int | None = None              # from the star check before solving
    background_unevenness: float | None = None # sky background variation, 0.3 = 30 % (vignetting, gradients)
    profile_note: str = ""                     # set when opening the image switched the equipment profile

    @property
    def height(self) -> int:
        return int(self.data.shape[0])

    @property
    def width(self) -> int:
        return int(self.data.shape[1])

    @property
    def is_color(self) -> bool:
        return self.data.ndim == 3

    def luminance(self) -> np.ndarray:
        if self.data.ndim == 3:
            return self.data[..., :3].mean(axis=2, dtype=np.float32)
        return self.data


@dataclass
class SolveResult:
    """The outcome of plate solving. `wcs` maps pixel (x, y) <-> sky (RA, Dec)."""
    success: bool
    solver_id: str = ""
    solver_name: str = ""
    message: str = ""
    elapsed_s: float = 0.0
    wcs: Any = None
    width: int = 0
    height: int = 0
    center_ra_deg: float | None = None
    center_dec_deg: float | None = None
    pixel_scale_arcsec: float | None = None
    rotation_deg: float | None = None          # position angle of image "up", east of north
    mirrored: bool = False
    fov_width_deg: float | None = None
    fov_height_deg: float | None = None
    attempts: list[str] = field(default_factory=list)
    scale_note: str = ""                       # explanation when the expected image scale was off

    @classmethod
    def failed(cls, message: str, solver_id: str = "", solver_name: str = "") -> "SolveResult":
        return cls(False, solver_id, solver_name, message)

    @classmethod
    def from_wcs(cls, wcs, width: int, height: int, solver_id: str, solver_name: str,
                 elapsed_s: float = 0.0, message: str = "") -> "SolveResult":
        res = cls(True, solver_id, solver_name, message, elapsed_s, wcs, width, height)
        cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
        ra, dec = wcs.pixel_to_world_values(cx, cy)
        res.center_ra_deg, res.center_dec_deg = float(ra) % 360.0, float(dec)

        # Scale and orientation measured from small steps around the centre.
        # Using the image's own pixel directions keeps this right for any projection.
        step = 10.0
        ra_r, dec_r = wcs.pixel_to_world_values(cx + step, cy)      # one step right
        ra_u, dec_u = wcs.pixel_to_world_values(cx, cy - step)      # one step up (row - 1)
        sep_r = _separation_deg(ra, dec, ra_r, dec_r)
        sep_u = _separation_deg(ra, dec, ra_u, dec_u)
        res.pixel_scale_arcsec = (sep_r + sep_u) / 2.0 / step * 3600.0
        pa_up = _position_angle_deg(ra, dec, ra_u, dec_u)
        pa_right = _position_angle_deg(ra, dec, ra_r, dec_r)
        res.rotation_deg = pa_up % 360.0
        # Seen normally (not mirrored) with north up, east is left, so "right" is PA 270.
        res.mirrored = ((pa_right - pa_up) % 360.0) < 180.0
        res.fov_width_deg = res.pixel_scale_arcsec * width / 3600.0
        res.fov_height_deg = res.pixel_scale_arcsec * height / 3600.0
        return res

    def pixel_to_radec(self, x: float, y: float) -> tuple[float, float]:
        ra, dec = self.wcs.pixel_to_world_values(x, y)
        return float(ra) % 360.0, float(dec)

    def radec_to_pixel(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
        x, y = self.wcs.world_to_pixel_values(ra_deg, dec_deg)
        return float(x), float(y)

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return -margin <= x <= self.width - 1 + margin and -margin <= y <= self.height - 1 + margin


@dataclass
class Distance:
    """Distance to an object. Stored in light-years; formatted using the General settings."""
    light_years: float
    uncertainty_ly: float | None = None
    method: str = ""          # "parallax", "published", "redshift", ...
    source: str = ""          # where it came from, e.g. "SIMBAD, parallax 2.4 mas"
    # For cosmological distances these differ from light_years (see redshift module):
    angular_diameter_ly: float | None = None   # the distance to use for converting angles to sizes
    lookback_years: float | None = None        # how long the light has travelled


@dataclass
class Link:
    title: str
    url: str
    source: str = ""


@dataclass
class SkyObject:
    """An object found inside the solved field (filled in by catalogue plugins)."""
    name: str
    ra_deg: float
    dec_deg: float
    object_type: str = ""
    aliases: list[str] = field(default_factory=list)
    magnitude: float | None = None
    size_arcmin: float | None = None       # major axis
    x: float | None = None                 # pixel position in the image
    y: float | None = None
    distance: Distance | None = None
    links: list[Link] = field(default_factory=list)
    catalog: str = ""
    category: str = ""                     # "galaxy", "nebula", "cluster", "star" or "other"
    common_name: str = ""                  # e.g. "Orion Nebula"
    size_minor_arcmin: float | None = None
    position_angle_deg: float | None = None  # of the major axis, east of north
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        if self.common_name and self.common_name != self.name:
            return f"{self.name} ({self.common_name})"
        return self.name

    def physical_size_ly(self) -> float | None:
        """True size across (major axis) in light-years: distance × angular size.

        Approximate: catalogued angular sizes depend on how faint an edge was measured.
        """
        if not self.distance or not self.size_arcmin or self.category == "star":
            return None
        d = self.distance.angular_diameter_ly or self.distance.light_years
        theta = math.radians(self.size_arcmin / 60.0)
        return 2.0 * d * math.tan(theta / 2.0)

    def light_travel_years(self) -> float | None:
        if not self.distance:
            return None
        return self.distance.lookback_years or self.distance.light_years


def light_left_text(years: float | None, now_year: int | None = None) -> str:
    """'around the year 680', 'around 1,250 BC', 'about 2.5 million years ago'."""
    if years is None or not math.isfinite(years) or years <= 0:
        return ""
    import datetime
    now = now_year or datetime.date.today().year
    if years < 1.0:
        return "less than a year ago"
    if years < 10000:
        year = now - years
        rounding = 1 if years < 100 else (10 if years < 2000 else 100)
        y = int(round(year / rounding) * rounding)
        if y > 0:
            return f"around the year {y}"
        bc = int(round((1 - year) / rounding) * rounding)
        return f"around {bc:,} BC"
    for limit, div, word in ((1e9, 1e9, "billion"), (1e6, 1e6, "million"), (1e3, 1e3, "thousand")):
        if years >= limit:
            return f"about {years / div:,.1f} {word} years ago".replace(".0 ", " ")
    return f"about {years:,.0f} years ago"


def _separation_deg(ra1, dec1, ra2, dec2) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    s = (math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2)
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(s))))


def _position_angle_deg(ra1, dec1, ra2, dec2) -> float:
    """Position angle of point 2 as seen from point 1, east of north."""
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    dra = r2 - r1
    y = math.sin(dra) * math.cos(d2)
    x = math.cos(d1) * math.sin(d2) - math.sin(d1) * math.cos(d2) * math.cos(dra)
    return math.degrees(math.atan2(y, x)) % 360.0
