# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Link each object to its Wikipedia article, in the language chosen in Settings."""
from __future__ import annotations

import re
from typing import Sequence

from platesolver.core.interfaces import LinkProvider
from platesolver.core.models import Link, SkyObject
from platesolver.core.plugin import TaskContext
from platesolver.core.settings import BOOL, CHOICE, SettingField
from platesolver.services import wikipedia

LANGUAGES = [("en", "English"), ("sv", "Svenska"), ("de", "Deutsch"), ("fr", "Français"), ("es", "Español"),
             ("it", "Italiano"), ("nl", "Nederlands"), ("pl", "Polski"), ("pt", "Português"), ("fi", "Suomi"),
             ("da", "Dansk"), ("no", "Norsk (bokmål)"), ("hu", "Magyar"), ("ru", "Русский"), ("ja", "日本語")]

GENITIVE = {
    "And": "Andromedae", "Ant": "Antliae", "Aps": "Apodis", "Aqr": "Aquarii", "Aql": "Aquilae", "Ara": "Arae",
    "Ari": "Arietis", "Aur": "Aurigae", "Boo": "Boötis", "Cae": "Caeli", "Cam": "Camelopardalis",
    "Cnc": "Cancri", "CVn": "Canum Venaticorum", "CMa": "Canis Majoris", "CMi": "Canis Minoris",
    "Cap": "Capricorni", "Car": "Carinae", "Cas": "Cassiopeiae", "Cen": "Centauri", "Cep": "Cephei",
    "Cet": "Ceti", "Cha": "Chamaeleontis", "Cir": "Circini", "Col": "Columbae", "Com": "Comae Berenices",
    "CrA": "Coronae Australis", "CrB": "Coronae Borealis", "Crv": "Corvi", "Crt": "Crateris", "Cru": "Crucis",
    "Cyg": "Cygni", "Del": "Delphini", "Dor": "Doradus", "Dra": "Draconis", "Equ": "Equulei", "Eri": "Eridani",
    "For": "Fornacis", "Gem": "Geminorum", "Gru": "Gruis", "Her": "Herculis", "Hor": "Horologii",
    "Hya": "Hydrae", "Hyi": "Hydri", "Ind": "Indi", "Lac": "Lacertae", "Leo": "Leonis", "LMi": "Leonis Minoris",
    "Lep": "Leporis", "Lib": "Librae", "Lup": "Lupi", "Lyn": "Lyncis", "Lyr": "Lyrae", "Men": "Mensae",
    "Mic": "Microscopii", "Mon": "Monocerotis", "Mus": "Muscae", "Nor": "Normae", "Oct": "Octantis",
    "Oph": "Ophiuchi", "Ori": "Orionis", "Pav": "Pavonis", "Peg": "Pegasi", "Per": "Persei", "Phe": "Phoenicis",
    "Pic": "Pictoris", "Psc": "Piscium", "PsA": "Piscis Austrini", "Pup": "Puppis", "Pyx": "Pyxidis",
    "Ret": "Reticuli", "Sge": "Sagittae", "Sgr": "Sagittarii", "Sco": "Scorpii", "Scl": "Sculptoris",
    "Sct": "Scuti", "Ser": "Serpentis", "Sex": "Sextantis", "Tau": "Tauri", "Tel": "Telescopii",
    "Tri": "Trianguli", "TrA": "Trianguli Australis", "Tuc": "Tucanae", "UMa": "Ursae Majoris",
    "UMi": "Ursae Minoris", "Vel": "Velorum", "Vir": "Virginis", "Vol": "Volantis", "Vul": "Vulpeculae",
}
GREEK_NAMES = {
    "alf": "Alpha", "bet": "Beta", "gam": "Gamma", "del": "Delta", "eps": "Epsilon", "zet": "Zeta", "eta": "Eta",
    "tet": "Theta", "iot": "Iota", "kap": "Kappa", "lam": "Lambda", "mu.": "Mu", "nu.": "Nu", "ksi": "Xi",
    "omi": "Omicron", "pi.": "Pi", "rho": "Rho", "sig": "Sigma", "tau": "Tau", "ups": "Upsilon", "phi": "Phi",
    "chi": "Chi", "psi": "Psi", "ome": "Omega",
}
_BAYER = re.compile(r"^\*\s+([a-z]{2,3}\.?)(\d{0,2})\s+([A-Za-z]{3})$")
_FLAMSTEED = re.compile(r"^\*\s+(\d+)\s+([A-Za-z]{3})$")


def candidate_titles(obj: SkyObject) -> list[str]:
    """Likely article titles for an object, best first (English naming, which other wikis redirect from)."""
    ids = obj.extra.get("identifiers") or []
    out: list[str] = []

    def add(t):
        if t and t not in out:
            out.append(t)

    def ids_with(prefix):
        return [i for i in ids if i.startswith(prefix)]

    star = obj.category == "star"
    if star and obj.common_name and obj.name:
        add(obj.name)  # for named stars the proper name is the display name
    for i in ids_with("M "):
        add("Messier " + i[2:].strip())
    for p in ("NGC ", "IC "):
        for i in ids_with(p):
            add(i)
    for i in ids:
        if i.startswith("NAME "):
            add(i[5:])
    for i in ids:
        m = _BAYER.match(i)
        if m and m.group(1) in GREEK_NAMES and m.group(3) in GENITIVE:
            num = m.group(2).lstrip("0")          # "* tet01 Ori" -> "Theta1 Orionis"
            if num:
                add(f"{GREEK_NAMES[m.group(1)]}{num} {GENITIVE[m.group(3)]}")
            add(f"{GREEK_NAMES[m.group(1)]} {GENITIVE[m.group(3)]}")
        m = _FLAMSTEED.match(i)
        if m and m.group(2) in GENITIVE:
            add(f"{m.group(1)} {GENITIVE[m.group(2)]}")
    for p in ("Sh2-", "Sh 2-", "LBN ", "LDN ", "Barnard ", "VdB ", "UGC ", "PGC ", "HD ", "HIP ", "Abell "):
        for i in ids_with(p):
            add(i.replace("Sh 2-", "Sh2-"))
    if not out:
        add(obj.name)
    return out[:8]


class WikipediaLinks(LinkProvider):
    plugin_id = "wikipedia"
    name = "Wikipedia"
    description = ("Finds the Wikipedia article for each object. Double-clicking an object opens it. "
                   "Needs an internet connection; results are cached for a month.")
    priority = 10

    def settings_schema(self):
        return [
            SettingField("language", "Wikipedia language", CHOICE, "en", choices=LANGUAGES),
            SettingField("fallback_en", "Use English Wikipedia when there is no article in that language",
                         BOOL, True),
        ]

    def _languages(self) -> list[str]:
        lang = self.setting("language") or "en"
        return [lang] + (["en"] if lang != "en" and self.setting("fallback_en") else [])

    def prepare(self, objects: Sequence[SkyObject], ctx: TaskContext) -> None:
        cands = {id(o): candidate_titles(o) for o in objects}
        pending = list(objects)
        for lang in self._languages():
            if not pending:
                break
            titles = [t for o in pending for t in cands[id(o)]]
            ctx.log(f"Wikipedia ({lang}): checking {len(titles)} possible articles…")
            found = wikipedia.resolve_titles(lang, titles, ctx.check_cancel)
            still = []
            for o in pending:
                article = next((found.get(t) for t in cands[id(o)] if found.get(t)), "")
                if article:
                    o.extra["wikipedia"] = (lang, article)
                else:
                    still.append(o)
            pending = still

    def links_for(self, obj: SkyObject, ctx: TaskContext) -> list[Link]:
        hit = obj.extra.get("wikipedia")
        if not hit:
            return []
        lang, article = hit
        return [Link(f"Wikipedia ({lang}): {article.split('#')[0]}", wikipedia.article_url(lang, article),
                     "Wikipedia")]
