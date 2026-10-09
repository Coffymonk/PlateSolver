# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Recognising the same object across catalogues, and merging duplicates."""
from __future__ import annotations

import math
import re

from platesolver.core.models import SkyObject

_PREFIX_ALIASES = [
    (re.compile(r"^(NAME|\*|V\*)\s+"), ""),
    (re.compile(r"^CL\s+MELOTTE\s*"), "MEL"),
    (re.compile(r"^CL\s+COLLINDER\s*"), "CR"),
    (re.compile(r"^CL\s+TRUMPLER\s*"), "TR"),
    (re.compile(r"^CL\s+"), ""),
    (re.compile(r"^BARNARD\s*"), "B"),
    (re.compile(r"^MESSIER\s*"), "M"),
    (re.compile(r"^SH\s*2-"), "SH2-"),
    (re.compile(r"^LEDA\s*"), "PGC"),
]


_NUMBERED = re.compile(r"^(SH2-|ABELL|NGC|MEL|LBN|LDN|UGCA|UGC|PGC|ESO|VDB|CED|HIP|IC|CR|TR|HD|M|B|C)\s*0*(\d+)(.*)$")


def canonical(ident: str) -> str:
    """'NGC  1976', 'NGC1976', 'NGC 01976' -> 'NGC1976'; 'Cl Melotte 22' / 'Mel022' -> 'MEL22'."""
    s = " ".join(str(ident).upper().split())
    for rx, rep in _PREFIX_ALIASES:
        s = rx.sub(rep, s)
    m = _NUMBERED.match(s)
    if m:
        return f"{m.group(1)}{int(m.group(2))}{m.group(3).replace(' ', '')}"
    return s.replace(" ", "")


def object_keys(obj: SkyObject) -> set[str]:
    ids = set(obj.extra.get("identifiers") or []) | {obj.name} | set(obj.aliases)
    return {canonical(i) for i in ids if i and not str(i).upper().startswith(("NAME ",))} - {""}


def _sep_arcmin(a: SkyObject, b: SkyObject) -> float:
    r1, d1, r2, d2 = map(math.radians, (a.ra_deg, a.dec_deg, b.ra_deg, b.dec_deg))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 60.0


def merge_objects(objects: list[SkyObject]) -> list[SkyObject]:
    """Keep the first occurrence of each object and fill its gaps from later duplicates.

    Two entries are the same object when they share a catalogue designation
    (e.g. both are NGC 1976) and lie within a few arc-minutes of each other.
    """
    kept: list[SkyObject] = []
    index: dict[str, SkyObject] = {}
    for obj in objects:
        match = None
        for key in object_keys(obj):
            cand = index.get(key)
            if cand is not None and _sep_arcmin(cand, obj) < max(5.0, (cand.size_arcmin or 0) / 2):
                match = cand
                break
        if match is None:
            kept.append(obj)
            for key in object_keys(obj):
                index.setdefault(key, obj)
            continue
        # fill in what the first entry lacks
        for attr in ("common_name", "magnitude", "size_arcmin", "size_minor_arcmin", "position_angle_deg"):
            if not getattr(match, attr) and getattr(obj, attr):
                setattr(match, attr, getattr(obj, attr))
        for k, v in obj.extra.items():
            if k == "identifiers":
                merged = list(dict.fromkeys(list(match.extra.get("identifiers") or []) + list(v or [])))
                match.extra["identifiers"] = merged
            elif match.extra.get(k) in (None, "", []):
                match.extra[k] = v
        match.aliases = list(dict.fromkeys(match.aliases + [a for a in obj.aliases if a != match.name]))[:12]
        if obj.catalog and obj.catalog not in match.catalog:
            match.catalog = f"{match.catalog} + {obj.catalog}" if match.catalog else obj.catalog
        for key in object_keys(obj):
            index.setdefault(key, match)
    return kept
