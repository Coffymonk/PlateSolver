# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Filtering the object list: magnitude, distance, size, type, catalogue and 'show only' choices.

Kept separate from the user interface so it can be tested and saved with the settings.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields

from platesolver.core.models import SkyObject

_MESSIER = re.compile(r"^M\s*\d+$", re.IGNORECASE)
WELL_KNOWN = re.compile(r"^(M|NGC|IC|C|CALDWELL|SH\s*2-|SH2-|B|BARNARD|MEL|CR|ABELL|ARP|HCG|LBN|LDN|VDB|CED|"
                        r"UGC|PGC|HD|HIP)\s*\d", re.IGNORECASE)


def identifiers(obj: SkyObject) -> list[str]:
    return [obj.name] + list(obj.aliases) + [str(i) for i in obj.extra.get("identifiers") or []]


def is_messier(obj: SkyObject) -> bool:
    return bool(obj.extra.get("messier")) or any(_MESSIER.match(i.strip()) for i in identifiers(obj))


def is_well_known(obj: SkyObject) -> bool:
    """Has a designation in one of the classic catalogues (Messier, NGC, IC, Caldwell, Sharpless, …)."""
    if obj.extra.get("famous") or is_messier(obj):
        return True
    return any(WELL_KNOWN.match(i.strip()) and not i.upper().startswith(("UGC", "PGC", "HD", "HIP"))
               for i in identifiers(obj))


@dataclass
class ObjectFilter:
    mag_min: float | None = None          # brightest magnitude shown (smaller number = brighter)
    mag_max: float | None = None          # faintest magnitude shown
    include_no_magnitude: bool = True
    dist_min_ly: float | None = None
    dist_max_ly: float | None = None
    only_with_distance: bool = False
    size_min_arcmin: float | None = None
    size_max_arcmin: float | None = None
    only_messier: bool = False
    only_named: bool = False              # has a common name, e.g. "Orion Nebula"
    only_well_known: bool = False
    only_visible: bool = False            # seen in the image (Settings › Visible stars and label order)
    hidden_types: set[str] = field(default_factory=set)      # e.g. {"Star", "Galaxy in cluster"}
    hidden_catalogs: set[str] = field(default_factory=set)   # e.g. {"HyperLeda"}

    # ------------------------------------------------------------------ testing an object
    def matches(self, obj: SkyObject) -> bool:
        if (obj.object_type or "Object") in self.hidden_types or (obj.catalog or "Other") in self.hidden_catalogs:
            return False
        m = obj.magnitude
        if m is None:
            if not self.include_no_magnitude and (self.mag_min is not None or self.mag_max is not None):
                return False
        else:
            if self.mag_min is not None and m < self.mag_min:
                return False
            if self.mag_max is not None and m > self.mag_max:
                return False
        d = obj.distance.light_years if obj.distance else None
        if d is None:
            if self.only_with_distance:
                return False
            if self.dist_min_ly is not None or self.dist_max_ly is not None:
                return False
        else:
            if self.dist_min_ly is not None and d < self.dist_min_ly:
                return False
            if self.dist_max_ly is not None and d > self.dist_max_ly:
                return False
        s = obj.size_arcmin
        if self.size_min_arcmin is not None and (s is None or s < self.size_min_arcmin):
            return False
        if self.size_max_arcmin is not None and s is not None and s > self.size_max_arcmin:
            return False
        if self.only_messier and not is_messier(obj):
            return False
        if self.only_named and not obj.common_name:
            return False
        if self.only_well_known and not is_well_known(obj):
            return False
        if self.only_visible and obj.extra.get("visible") is not True:
            return False
        return True

    def active_count(self) -> int:
        """How many filter choices differ from 'show everything' (for the badge on the filter button)."""
        n = sum(v is not None for v in (self.mag_min, self.mag_max, self.dist_min_ly, self.dist_max_ly,
                                         self.size_min_arcmin, self.size_max_arcmin))
        n += self.only_with_distance + self.only_messier + self.only_named + self.only_well_known + self.only_visible
        n += bool(self.hidden_types) + bool(self.hidden_catalogs)
        return int(n)

    def describe(self) -> str:
        """Short text for the summary line, e.g. 'mag 6–12, Messier only'."""
        parts = []
        if self.mag_min is not None or self.mag_max is not None:
            parts.append(f"mag {_g(self.mag_min, '…')}–{_g(self.mag_max, '…')}")
        if self.dist_min_ly is not None or self.dist_max_ly is not None:
            parts.append("distance limits")
        elif self.only_with_distance:
            parts.append("with distance")
        if self.size_min_arcmin is not None or self.size_max_arcmin is not None:
            parts.append(f"size {_g(self.size_min_arcmin, '…')}–{_g(self.size_max_arcmin, '…')}′")
        for flag, text in ((self.only_messier, "Messier only"), (self.only_named, "named only"),
                           (self.only_well_known, "well-known only"), (self.only_visible, "visible only")):
            if flag:
                parts.append(text)
        if self.hidden_types:
            parts.append(f"{len(self.hidden_types)} type{'s' if len(self.hidden_types) > 1 else ''} hidden")
        if self.hidden_catalogs:
            parts.append(", ".join(sorted(self.hidden_catalogs)) + " hidden")
        return ", ".join(parts)

    # ------------------------------------------------------------------ saving
    def to_dict(self) -> dict:
        d = asdict(self)
        d["hidden_types"] = sorted(self.hidden_types)
        d["hidden_catalogs"] = sorted(self.hidden_catalogs)
        return d

    @classmethod
    def from_dict(cls, data: dict | None) -> "ObjectFilter":
        f = cls()
        if not isinstance(data, dict):
            return f
        for fld in fields(cls):
            if fld.name not in data:
                continue
            v = data[fld.name]
            if fld.name in ("hidden_types", "hidden_catalogs"):
                setattr(f, fld.name, {str(x) for x in (v or [])})
            elif isinstance(getattr(f, fld.name), bool):
                setattr(f, fld.name, bool(v))
            else:
                try:
                    setattr(f, fld.name, None if v is None else float(v))
                except (TypeError, ValueError):
                    pass
        return f


def _g(v: float | None, empty: str) -> str:
    return empty if v is None else f"{v:g}"
