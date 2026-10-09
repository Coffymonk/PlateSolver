# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""SIMBAD (CDS, Strasbourg) through its TAP/ADQL interface.

Docs: https://simbad.cds.unistra.fr/simbad/tap/  - tables used: basic, allfluxes,
ident and mesDistance.
"""
from __future__ import annotations

import csv
import html
import io
import logging
import re
from typing import Iterable

from platesolver.core import net
from platesolver.core.cache import JsonCache

log = logging.getLogger(__name__)

TAP_URL = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"
PAGE_URL = "https://simbad.cds.unistra.fr/simbad/sim-id?Ident={ident}"

_cache: JsonCache | None = None


def _get_cache() -> JsonCache:
    global _cache
    if _cache is None:
        _cache = JsonCache("simbad", max_age_days=14)
    return _cache


def query(adql: str, timeout: float = 90, use_cache: bool = True) -> list[dict[str, str]]:
    """Run an ADQL query; returns rows as dicts of strings ('' for empty)."""
    key = " ".join(adql.split())
    if use_cache:
        cached = _get_cache().get(key)
        if cached is not None:
            return cached
    log.info("SIMBAD query: %s", key[:400])
    try:
        raw = net.post_form(TAP_URL, {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                      "MAXREC": "50000", "QUERY": adql}, timeout=timeout)
    except net.NetworkError as exc:
        if "HTTP 400" in str(exc):   # TAP answers a bad query with 400 and the reason in a VOTable
            body = getattr(exc, "body", "") or str(exc)
            m = re.search(r"QUERY_STATUS[^>]*>(.*?)(</INFO>|$)", body, re.S)
            reason = " ".join(html.unescape(m.group(1)).split())[:400] if m else str(exc)
            raise net.NetworkError("SIMBAD rejected the query: " + reason) from exc
        raise
    text = raw.decode("utf-8", errors="replace")
    if text.lstrip().startswith("<"):
        # TAP errors come back as a VOTable document
        m = re.search(r"<INFO[^>]*name=\"QUERY_STATUS\"[^>]*>(.*?)</INFO>", text, re.S)
        raise net.NetworkError("SIMBAD rejected the query: " + (m.group(1).strip() if m else text[:200]))
    rows = list(csv.DictReader(io.StringIO(text)))
    if use_cache:
        _get_cache().set(key, rows)
    return rows


def num(value) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN -> None


def chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def in_list(values: Iterable) -> str:
    return ",".join(str(int(v)) for v in values)


def page_url(identifier: str) -> str:
    from urllib.parse import quote_plus
    return PAGE_URL.format(ident=quote_plus(identifier))


# --------------------------------------------------------------------------- object types
# SIMBAD's short object-type codes -> (category, readable name). A trailing '?' marks a candidate.
GALAXY_TYPES = {
    "G": "Galaxy", "GiG": "Galaxy in group", "GiC": "Galaxy in cluster", "BiC": "Brightest galaxy in cluster",
    "GiP": "Galaxy in pair", "IG": "Interacting galaxies", "PaG": "Pair of galaxies",
    "GrG": "Group of galaxies", "CGG": "Compact group of galaxies", "ClG": "Cluster of galaxies",
    "PCG": "Proto-cluster of galaxies", "SCG": "Supercluster of galaxies", "LSB": "Low-surface-brightness galaxy",
    "bCG": "Blue compact galaxy", "SBG": "Starburst galaxy", "H2G": "HII galaxy", "EmG": "Emission-line galaxy",
    "AGN": "Active galaxy", "SyG": "Seyfert galaxy", "Sy1": "Seyfert 1 galaxy", "Sy2": "Seyfert 2 galaxy",
    "rG": "Radio galaxy", "LIN": "LINER galaxy", "QSO": "Quasar", "Bla": "Blazar", "BLL": "BL Lac object",
    "LeG": "Lensed galaxy", "LeQ": "Lensed quasar", "Galaxy": "Galaxy",
}
NEBULA_TYPES = {
    "HII": "Emission nebula (HII region)", "PN": "Planetary nebula", "RNe": "Reflection nebula",
    "DNe": "Dark nebula", "SNR": "Supernova remnant", "GNe": "Nebula", "ISM": "Interstellar matter",
    "SFR": "Star-forming region", "Cld": "Cloud", "MoC": "Molecular cloud", "glb": "Globule",
    "CGb": "Cometary globule", "cor": "Dense core", "bub": "Bubble", "sh": "Shell", "flt": "Filament",
    "HVC": "High-velocity cloud", "EmO": "Emission object", "EmObj": "Emission object", "MolCld": "Molecular cloud",
}
CLUSTER_TYPES = {
    "Cl*": "Star cluster", "GlC": "Globular cluster", "OpC": "Open cluster", "As*": "Stellar association",
    "St*": "Stellar stream", "MGr": "Moving group", "GlCl": "Globular cluster", "OpCl": "Open cluster",
    "Assoc*": "Stellar association",
}
STAR_TYPES = {
    "*": "Star", "**": "Double star", "V*": "Variable star", "PM*": "High proper-motion star",
    "EB*": "Eclipsing binary", "SB*": "Spectroscopic binary", "Be*": "Be star", "C*": "Carbon star",
    "WR*": "Wolf-Rayet star", "RG*": "Red giant", "s*r": "Red supergiant", "s*b": "Blue supergiant",
    "s*y": "Yellow supergiant", "sg*": "Supergiant", "Ce*": "Cepheid variable", "RR*": "RR Lyrae variable",
    "Mi*": "Mira variable", "LP*": "Long-period variable", "Y*O": "Young stellar object", "TT*": "T Tauri star",
    "Em*": "Emission-line star", "WD*": "White dwarf", "HB*": "Horizontal-branch star", "Ma*": "Massive star",
    "bC*": "Beta Cephei variable", "dS*": "Delta Scuti variable", "Or*": "Orion variable",
    "BS*": "Blue straggler", "HS*": "Hot subdwarf", "Ae*": "Herbig Ae/Be star", "AB*": "AGB star",
    "S*": "S-type star", "N*": "Neutron star", "Psr": "Pulsar", "Ro*": "Rotating variable",
    "El*": "Ellipsoidal variable", "Ir*": "Irregular variable", "Er*": "Eruptive variable",
    "Pu*": "Pulsating variable", "RS*": "RS CVn variable", "BY*": "BY Dra variable", "gD*": "Gamma Doradus variable",
    "a2*": "Alpha2 CVn variable", "SX*": "SX Phe variable", "LM*": "Low-mass star", "BD*": "Brown dwarf",
}


def classify(otype: str) -> tuple[str, str]:
    """SIMBAD otype -> (category, readable type)."""
    code = (otype or "").strip()
    candidate = code.endswith("?")
    base = code.rstrip("?") if candidate else code
    for cat, table in (("galaxy", GALAXY_TYPES), ("nebula", NEBULA_TYPES),
                       ("cluster", CLUSTER_TYPES), ("star", STAR_TYPES)):
        if base in table:
            label = table[base]
            return cat, (label + " (candidate)" if candidate else label)
    if base.endswith("*") or base in ("Y*O",):
        return "star", "Star" + (" (candidate)" if candidate else "")
    return "other", base or "Object"


# --------------------------------------------------------------------------- names
GREEK = {
    "alf": "α", "bet": "β", "gam": "γ", "del": "δ", "eps": "ε", "zet": "ζ", "eta": "η", "tet": "θ",
    "iot": "ι", "kap": "κ", "lam": "λ", "mu.": "μ", "mu": "μ", "nu.": "ν", "nu": "ν", "ksi": "ξ", "omi": "ο",
    "pi.": "π", "pi": "π", "rho": "ρ", "sig": "σ", "tau": "τ", "ups": "υ", "phi": "φ", "chi": "χ",
    "psi": "ψ", "ome": "ω",
}
_BAYER = re.compile(r"^\*\s+([a-z]{2,3}\.?)(\d{0,2})\s+([A-Z][a-z]{2})(.*)$")
_FLAMSTEED = re.compile(r"^\*\s+(\d+)\s+([A-Z][a-z]{2})(.*)$")


def clean_id(ident: str) -> str:
    """SIMBAD pads identifiers ('M  42', 'NGC  1976'); collapse the spaces and tidy stars."""
    s = " ".join((ident or "").split())
    m = _BAYER.match(s)
    if m and m.group(1) in GREEK:
        letter = GREEK[m.group(1)]
        digits = m.group(2).lstrip("0")
        if digits:
            letter += digits.translate(str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"))
        return f"{letter} {m.group(3)}{m.group(4)}".strip()
    m = _FLAMSTEED.match(s)
    if m:
        return f"{m.group(1)} {m.group(2)}{m.group(3)}".strip()
    if s.startswith("NAME "):
        return s[5:]
    if s.startswith("V* "):
        return s[3:]
    if s.startswith("* "):
        return s[2:]
    return s


# Preferred designations, best first. (prefix as stored in SIMBAD after collapsing spaces)
CATALOG_PREFIXES = ("M ", "NGC ", "IC ", "Sh2-", "Sh 2-", "LBN ", "LDN ", "Barnard ", "VdB ", "Ced ",
                    "Cl Collinder ", "Cl Melotte ", "Cl Trumpler ", "Cl Stock ", "Cl ", "Abell ", "ACO ",
                    "UGC ", "PGC ", "MCG ", "PN ")
STAR_PREFIXES = ("* ", "V* ", "HD ", "HIP ", "HR ", "TYC ")
FAMOUS_PREFIXES = ("M ", "NGC ", "IC ")

CONSTELLATIONS = set("""And Ant Aps Aqr Aql Ara Ari Aur Boo Cae Cam Cnc CVn CMa CMi Cap Car Cas Cen Cep Cet Cha Cir
Col Com CrA CrB Crv Crt Cru Cyg Del Dor Dra Equ Eri For Gem Gru Her Hor Hya Hyi Ind Lac Leo LMi Lep Lib Lup Lyn
Lyr Men Mic Mon Mus Nor Oct Oph Ori Pav Peg Per Phe Pic Psc PsA Pup Pyx Ret Sge Sgr Sco Scl Sct Ser Sex Tau Tel
Tri TrA Tuc UMa UMi Vel Vir Vol Vul""".split())


def best_common_name(names: list[str]) -> str:
    """Pick the most readable of several SIMBAD 'NAME …' entries (e.g. 'Orion Nebula' over 'Ori Neb')."""
    cleaned = [clean_id(n) for n in names if n]
    if not cleaned:
        return ""

    def score(n: str):
        words = n.replace("-", " ").split()
        abbreviated = any(w in CONSTELLATIONS or w.endswith(".") for w in words)
        return (abbreviated, any(ch.isdigit() for ch in n), len(n))
    return sorted(set(cleaned), key=score)[0]
