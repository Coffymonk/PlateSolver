# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Formatting and parsing of coordinates, angles and distances."""
from __future__ import annotations

import math
import re

LY_PER_PARSEC = 3.261563777


def parsec_to_ly(pc: float) -> float:
    return pc * LY_PER_PARSEC


def format_ly(ly: float | None, decimals: int = 1) -> str:
    """Light-years with thousands separators, e.g. 1,344.0 ly or 2.54 million ly."""
    if ly is None or not math.isfinite(ly):
        return "–"
    decimals = max(0, int(decimals))
    if ly >= 1e9:
        return f"{ly / 1e9:,.{decimals}f} billion ly"
    if ly >= 1e6:
        return f"{ly / 1e6:,.{decimals}f} million ly"
    return f"{ly:,.{decimals}f} ly"


def format_ra(ra_deg: float | None, decimals: int = 1) -> str:
    if ra_deg is None:
        return "–"
    total = (ra_deg % 360.0) / 15.0 * 3600.0
    total = round(total, decimals)
    h = int(total // 3600) % 24
    m = int((total % 3600) // 60)
    s = total % 60
    return f"{h:02d}h {m:02d}m {s:0{3 + decimals if decimals else 2}.{decimals}f}s"


def format_dec(dec_deg: float | None, decimals: int = 0) -> str:
    if dec_deg is None:
        return "–"
    sign = "-" if dec_deg < 0 else "+"
    total = round(abs(dec_deg) * 3600.0, decimals)
    d = int(total // 3600)
    m = int((total % 3600) // 60)
    s = total % 60
    return f"{sign}{d:02d}° {m:02d}′ {s:0{3 + decimals if decimals else 2}.{decimals}f}″"


def format_angle(deg: float | None) -> str:
    """A size or field of view in the most readable unit."""
    if deg is None:
        return "–"
    if deg >= 1.0:
        return f"{deg:.2f}°"
    if deg * 60 >= 1.0:
        return f"{deg * 60:.1f}′"
    return f"{deg * 3600:.1f}″"


_SEXA = re.compile(r"^\s*([+-]?)\s*(\d+(?:\.\d*)?)(?:[\s:hd°]+(\d+(?:\.\d*)?))?(?:[\s:m′']+(\d+(?:\.\d*)?))?")


def parse_sexagesimal(text: str) -> float | None:
    """'05 35 17.3', '05:35:17.3', '-05 23 28' -> decimal units (hours or degrees)."""
    if text is None:
        return None
    m = _SEXA.match(str(text))
    if not m:
        return None
    sign = -1.0 if m.group(1) == "-" else 1.0
    a = float(m.group(2))
    b = float(m.group(3) or 0)
    c = float(m.group(4) or 0)
    return sign * (a + b / 60.0 + c / 3600.0)
