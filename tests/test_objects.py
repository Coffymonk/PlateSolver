# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Object lookup, distances and links - with SIMBAD and Wikipedia replaced by recorded-style answers."""
import re

import numpy as np
import pytest
from astropy.wcs import WCS

from platesolver.core.cache import JsonCache
from platesolver.core.formatting import format_ly
from platesolver.core.general import GeneralSettings
from platesolver.core.models import ImageData, SkyObject, SolveResult
from platesolver.core.pipeline import Pipeline
from platesolver.core.plugin import TaskContext
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import SettingsStore
from platesolver.plugins.links.wikipedia_links import candidate_titles
from platesolver.services import simbad, wikipedia

W, H, SCALE = 2000, 1400, 1.709


def solution_m42():
    s = SCALE / 3600
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crpix = [(W + 1) / 2, (H + 1) / 2]
    w.wcs.crval = [83.82, -5.39]
    w.wcs.cd = [[-s, 0], [0, -s]]
    w.wcs.set()
    return SolveResult.from_wcs(w, W, H, "astap", "ASTAP")


def row(oid, main_id, ra, dec, otype, maj="", minor="", ang="", plx="", plx_err="", z="", v=""):
    return {"oid": str(oid), "main_id": main_id, "ra": str(ra), "dec": str(dec), "otype": otype,
            "galdim_majaxis": str(maj), "galdim_minaxis": str(minor), "galdim_angle": str(ang),
            "plx_value": str(plx), "plx_err": str(plx_err), "rvz_redshift": str(z), "vmag": str(v)}


BASIC = [
    row(1, "M  42", 83.8221, -5.3911, "HII", 66, 60, 0),
    row(2, "M  43", 83.8792, -5.2700, "HII", 20, 15, 0),
    row(3, "* tet01 Ori C", 83.8186, -5.3897, "Y*O", plx=2.4, plx_err=0.1, v=5.13),
    row(4, "NGC  1977", 83.8500, -5.1000, "RNe", 20, 10, 0),
    row(5, "M  78", 86.6910, 0.0790, "RNe", 8, 6, 0),                         # far outside
    row(6, "Gaia DR3 123", 83.70, -5.30, "*", v=12.5),                          # faint, unnamed star
    row(7, "M  31", 10.68, 41.27, "G", 190, 60, 35, z=-0.001),                 # other side of the sky
    row(8, "[XYZ2000] 17", 83.75, -5.45, "HII", 0.2, 0.2),                       # tiny, uncatalogued
    row(9, "NGC  1980", 83.8500, -5.8000, "OpC", 40, 40),                       # centre below frame, reaches in
]
IDENTS = [
    (1, "M  42"), (1, "NGC  1976"), (1, "NAME Ori Nebula"), (1, "NAME Orion Nebula"), (1, "LBN 974"),
    (2, "M  43"), (2, "NGC  1982"), (2, "NAME De Mairan's Nebula"),
    (3, "* tet01 Ori C"), (3, "HD  37022"), (3, "HIP 26221"),
    (4, "NGC  1977"), (4, "NAME Running Man Nebula"),
    (5, "M  78"), (5, "NGC  2068"),
    (9, "NGC  1980"),
]
MESDIST = [
    {"oidref": "1", "dist": "414", "unit": "pc", "minus_err": "7", "plus_err": "7", "method": "plx", "bibcode": "2007A&A...474..515M"},
    {"oidref": "1", "dist": "389", "unit": "pc", "minus_err": "", "plus_err": "", "method": "", "bibcode": "2008PASJ...60..991K"},
    {"oidref": "1", "dist": "0.400", "unit": "kpc", "minus_err": "", "plus_err": "", "method": "", "bibcode": "2018AJ....156...84K"},
    {"oidref": "3", "dist": "1000", "unit": "pc", "minus_err": "", "plus_err": "", "method": "", "bibcode": "1990ApJ...1..1X"},
]


def fake_query(adql, **_):
    """Answer the ADQL the modules send, checking it looks like valid SIMBAD ADQL on the way."""
    assert "CONTAINS(POINT('ICRS'" in adql or "IN (" in adql, adql
    if "FROM mesDistance" in adql:
        ids = set(re.search(r"IN \(([^)]*)\)", adql).group(1).split(","))
        return [r for r in MESDIST if r["oidref"] in ids]
    if adql.startswith("SELECT oidref, id FROM ident"):
        ids = {int(i) for i in re.search(r"IN \(([^)]*)\)", adql).group(1).split(",")}
        return [{"oidref": str(o), "id": i} for o, i in IDENTS if o in ids]
    assert "LEFT JOIN allfluxes AS f ON f.oidref = b.oid" in adql and 'f."V" AS vmag' in adql
    m = re.search(r"CIRCLE\('ICRS', ([\d.+-]+), ([\d.+-]+), ([\d.]+)\)", adql)
    ra0, dec0, r = map(float, m.groups())
    out = []
    for b in BASIC:
        d = np.degrees(np.arccos(np.clip(
            np.sin(np.radians(dec0)) * np.sin(np.radians(float(b["dec"]))) +
            np.cos(np.radians(dec0)) * np.cos(np.radians(float(b["dec"]))) *
            np.cos(np.radians(ra0 - float(b["ra"]))), -1, 1)))
        if d <= r:
            out.append(dict(b))
    return out


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(simbad, "query", fake_query)
    store = SettingsStore(tmp_path / "s.json")
    reg = PluginRegistry(store).discover()
    assert not reg.errors, reg.errors
    reg.get("link.wikipedia").settings.set("_enabled", False)   # tested separately below
    return Pipeline(reg, store.section(GeneralSettings()))


def image():
    return ImageData(path=__import__("pathlib").Path("m42.fits"), format="FITS",
                     data=np.zeros((H, W), np.float32))


def test_simbad_helpers():
    assert simbad.clean_id("M  42") == "M 42"
    assert simbad.clean_id("* alf Ori") == "α Ori"
    assert simbad.clean_id("* tet01 Ori C") == "θ¹ Ori C"
    assert simbad.clean_id("NAME Orion Nebula") == "Orion Nebula"
    assert simbad.clean_id("* 41 Tau") == "41 Tau"
    assert simbad.classify("HII") == ("nebula", "Emission nebula (HII region)")
    assert simbad.classify("G?")[0] == "galaxy" and "candidate" in simbad.classify("G?")[1]
    assert simbad.classify("Y*O")[0] == "star"
    assert simbad.classify("s*r")[0] == "star"
    assert simbad.best_common_name(["NAME Ori Nebula", "NAME Orion Nebula", "NAME Great Orion Nebula"]) == "Orion Nebula"


def test_catalog_finds_and_filters(pipeline):
    sol = solution_m42()
    objs = pipeline.find_objects(image(), sol, TaskContext())
    names = [o.name for o in objs]
    assert pipeline.object_errors == []
    assert names[0] == "M 42"                                  # Messier first
    assert {"M 42", "M 43", "NGC 1977", "NGC 1980", "θ¹ Ori C"} <= set(names)
    assert "M 78" not in names and "M 31" not in names          # outside the field
    assert all("Gaia" not in n and "XYZ" not in n for n in names)  # faint star and tiny object dropped
    m42 = objs[0]
    assert m42.common_name == "Orion Nebula" and m42.category == "nebula"
    assert m42.display_name == "M 42 (Orion Nebula)"
    assert abs(m42.x - (W - 1) / 2) < 10 and abs(m42.y - (H - 1) / 2) < 10
    # NGC 1980 centre is below the frame but its 40' extent reaches in
    ngc1980 = next(o for o in objs if o.name == "NGC 1980")
    assert ngc1980.y > H - 1


def test_distances(pipeline):
    objs = {o.name: o for o in pipeline.find_objects(image(), solution_m42(), TaskContext())}
    m42 = objs["M 42"]
    # median of 414, 389, 400 pc = 400 pc
    assert m42.distance.light_years == pytest.approx(400 * 3.261563777, rel=1e-6)
    assert "median of 3" in m42.distance.source
    assert format_ly(m42.distance.light_years, 1) == "1,304.6 ly"
    star = objs["θ¹ Ori C"]
    # the parallax (2.4 mas -> 416.7 pc) wins over the published 1000 pc
    assert star.distance.method == "parallax"
    assert star.distance.light_years == pytest.approx(1000 / 2.4 * 3.261563777, rel=1e-6)
    assert objs["M 43"].distance is None
    # every object gets at least the SIMBAD page link
    assert all(o.links and "simbad.cds.unistra.fr" in o.links[-1].url for o in objs.values())
    assert m42.links[0].url.endswith("Ident=M+42")


def test_parallax_too_uncertain_is_skipped(pipeline):
    from platesolver.plugins.distances.parallax import ParallaxDistance
    p = ParallaxDistance()
    o = SkyObject("x", 0, 0, extra={"plx_mas": 1.0, "plx_err_mas": 0.5})
    assert p.resolve(o, TaskContext()) is None
    o.extra["plx_err_mas"] = 0.05
    assert p.resolve(o, TaskContext()).light_years == pytest.approx(1000 * 3.261563777)


def test_redshift_distance():
    from platesolver.plugins.distances.redshift import RedshiftDistance
    r = RedshiftDistance()
    g = SkyObject("NGC 7331", 0, 0, category="galaxy", extra={"redshift": 0.01})
    d = r.resolve(g, TaskContext())
    # at z = 0.01 both measures are close to cz/H0 = 42.8 Mpc = 139.7 million ly
    assert 130e6 < d.light_years < 140e6
    assert r.resolve(SkyObject("M 31", 0, 0, category="galaxy", extra={"redshift": -0.001}), TaskContext()) is None
    assert r.resolve(SkyObject("star", 0, 0, category="star", extra={"redshift": 0.01}), TaskContext()) is None


def test_wikipedia_candidates():
    m42 = SkyObject("M 42", 0, 0, category="nebula", common_name="Orion Nebula",
                    extra={"identifiers": ["M 42", "NGC 1976", "NAME Orion Nebula", "LBN 974"]})
    assert candidate_titles(m42)[:3] == ["Messier 42", "NGC 1976", "Orion Nebula"]
    betel = SkyObject("Betelgeuse", 0, 0, category="star", common_name="α Ori",
                      extra={"identifiers": ["NAME Betelgeuse", "* alf Ori", "HD 39801"]})
    assert candidate_titles(betel)[:3] == ["Betelgeuse", "Alpha Orionis", "HD 39801"]
    th = SkyObject("θ¹ Ori C", 0, 0, category="star", extra={"identifiers": ["* tet01 Ori C", "HD 37022"]})
    assert "HD 37022" in candidate_titles(th)


def test_wikipedia_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(wikipedia, "_cache", JsonCache("wiki", folder=tmp_path))
    calls = []

    def fake_get_json(url, params=None, timeout=0):
        calls.append((url, params["titles"]))
        return {"query": {
            "normalized": [{"from": "orion nebula", "to": "Orion nebula"}],
            "redirects": [{"from": "Messier 42", "to": "Orion Nebula"},
                          {"from": "Orion nebula", "to": "Orion Nebula"},
                          {"from": "NGC 1982", "to": "Messier 43", "tofragment": "History"}],
            "pages": [{"pageid": 1, "title": "Orion Nebula"},
                      {"pageid": 2, "title": "Messier 43"},
                      {"title": "NGC 1977", "missing": True},
                      {"pageid": 3, "title": "Trapezium", "pageprops": {"disambiguation": ""}}]}}
    monkeypatch.setattr(wikipedia.net, "get_json", fake_get_json)
    got = wikipedia.resolve_titles("en", ["Messier 42", "orion nebula", "NGC 1982", "NGC 1977", "Trapezium"])
    assert got == {"Messier 42": "Orion Nebula", "orion nebula": "Orion Nebula",
                   "NGC 1982": "Messier 43#History", "NGC 1977": "", "Trapezium": ""}
    assert calls[0][0] == "https://en.wikipedia.org/w/api.php"
    # second time comes from the cache - no new request
    wikipedia.resolve_titles("en", ["Messier 42", "NGC 1977"])
    assert len(calls) == 1
    assert wikipedia.article_url("sv", "Orionnebulosan") == "https://sv.wikipedia.org/wiki/Orionnebulosan"
    assert wikipedia.article_url("en", "Messier 43#History") == "https://en.wikipedia.org/wiki/Messier_43#History"


def test_wikipedia_link_order_and_language_fallback(tmp_path, monkeypatch):
    from platesolver.plugins.links.wikipedia_links import WikipediaLinks
    store = SettingsStore(None)
    wl = WikipediaLinks()
    wl.settings = store.section(wl)
    wl.settings.set("language", "sv")
    asked = []

    def fake_resolve(lang, titles, check_cancel=None):
        asked.append(lang)
        table = {"sv": {"Messier 42": "Orionnebulosan"}, "en": {"NGC 1977": "Running Man Nebula"}}[lang]
        return {t: table.get(t, "") for t in titles}
    monkeypatch.setattr(wikipedia, "resolve_titles", fake_resolve)
    m42 = SkyObject("M 42", 0, 0, category="nebula", extra={"identifiers": ["M 42", "NGC 1976"]})
    n1977 = SkyObject("NGC 1977", 0, 0, category="nebula", extra={"identifiers": ["NGC 1977"]})
    wl.prepare([m42, n1977], TaskContext())
    assert asked == ["sv", "en"]
    assert wl.links_for(m42, TaskContext())[0].url == "https://sv.wikipedia.org/wiki/Orionnebulosan"
    assert wl.links_for(n1977, TaskContext())[0].url == "https://en.wikipedia.org/wiki/Running_Man_Nebula"


def test_catalog_failure_is_reported(tmp_path, monkeypatch):
    """No internet: the error is reported and the built-in OpenNGC catalogue takes over."""
    from platesolver.core import net

    def offline(*a, **k):
        raise net.NetworkError("could not reach simbad.cds.unistra.fr (no network)")
    monkeypatch.setattr(simbad, "query", offline)
    store = SettingsStore(None)
    reg = PluginRegistry(store).discover()
    reg.get("link.wikipedia").settings.set("_enabled", False)
    p = Pipeline(reg, store.section(GeneralSettings()))
    objs = p.find_objects(image(), solution_m42(), TaskContext())
    assert "could not reach" in p.object_errors[0]
    assert objs and objs[0].name == "M 42" and objs[0].catalog == "OpenNGC"
    reg.get("catalog.openngc").settings.set("_enabled", False)
    assert p.find_objects(image(), solution_m42(), TaskContext()) == []


def test_simbad_query_parses_csv_and_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(simbad, "_cache", JsonCache("simbad", folder=tmp_path))
    sent = {}

    def fake_post(url, data, timeout=0):
        sent.update(data)
        if "bad" in data["QUERY"]:
            return (b'<?xml version="1.0"?><VOTABLE><RESOURCE type="results">'
                    b'<INFO name="QUERY_STATUS" value="ERROR">Incorrect ADQL query</INFO></RESOURCE></VOTABLE>')
        return b'oid,main_id,V\n1,"M  42",\n2,"* alf Ori",0.42\n'
    monkeypatch.setattr(simbad.net, "post_form", fake_post)
    rows = simbad.query("SELECT oid, main_id FROM basic")
    assert rows == [{"oid": "1", "main_id": "M  42", "V": ""}, {"oid": "2", "main_id": "* alf Ori", "V": "0.42"}]
    assert sent["FORMAT"] == "csv" and sent["LANG"] == "ADQL"
    with pytest.raises(simbad.net.NetworkError, match="Incorrect ADQL"):
        simbad.query("bad query")


def test_object_overlay_draws(pipeline):
    from platesolver.plugins.overlays.object_labels import ObjectLabelsOverlay
    objs = pipeline.find_objects(image(), solution_m42(), TaskContext())

    class Rec:
        ly_decimals = 0
        screen_px = 1.0

        def text_extent(self, text, size=10):
            return len(text) * size * 0.6, size * 1.3

        def __init__(self):
            self.calls = []

        def __getattr__(self, name):
            return lambda *a, **k: self.calls.append((name, a))
    ov = ObjectLabelsOverlay()
    ov.settings = SettingsStore(None).section(ov)
    ov.settings.set("distances", True)
    rec = Rec()
    ov.render(rec, image(), solution_m42(), objs)
    texts = [a[2] for n, a in rec.calls if n == "text"]
    assert "M 42 (Orion Nebula)  ·  1,305 ly" in texts
    ell = next(a for n, a in rec.calls if n == "ellipse")
    # M 42: 66' at 1.709"/px -> semi-major axis 1158.7 px
    assert ell[2] == pytest.approx(66 * 60 / 2 / SCALE, rel=0.01)


def test_distance_query_falls_back_to_basic_columns(monkeypatch):
    from platesolver.plugins.distances.simbad_measured import SimbadMeasuredDistance
    seen = []

    def picky(adql, **_):
        seen.append(adql)
        if "minus_err" in adql:
            raise simbad.net.NetworkError("SIMBAD rejected the query: Unknown column \"minus_err\"")
        return [{"oidref": "1", "dist": "400", "unit": "pc"}]
    monkeypatch.setattr(simbad, "query", picky)
    r = SimbadMeasuredDistance()
    r.settings = SettingsStore(None).section(r)
    o = SkyObject("M 42", 83.8, -5.4, extra={"simbad_oid": 1})
    r.prepare([o], TaskContext())
    assert len(seen) == 2
    assert r.resolve(o, TaskContext()).light_years == pytest.approx(400 * 3.261563777)
