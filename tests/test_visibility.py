# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Visible stars and label order: which objects can be seen, adding unmarked stars, ordering, dim/hide."""
import math

import numpy as np
import pytest
from astropy.wcs import WCS

from platesolver.core import starcatalog as sc
from platesolver.core import visibility as V
from platesolver.core.models import ImageData, SkyObject, SolveResult
from platesolver.core.objectfilter import ObjectFilter
from platesolver.core.settings import SettingsStore

W, H, SCALE = 1200, 900, 60.0           # 20° × 15°, like a camera lens


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path / "home"))
    sc.reset_cache()


def make_wcs(ra=84.0, dec=0.0):
    s = SCALE / 3600
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crpix = [(W + 1) / 2, (H + 1) / 2]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.cd = [[-s, 0], [0, -s]]
    wcs.wcs.set()
    return wcs


def scene(blob_at=(300.0, 650.0), blob_r=30.0, mag_limit=6.5):
    """Stars from the built-in catalogue down to mag_limit, plus one soft 'nebula'."""
    wcs = make_wcs()
    ra, dec, mag, x, y = sc.in_field(wcs, W, H, max_stars=100_000, margin=-5)
    img = np.full((H, W), 0.1, np.float32) + np.random.default_rng(3).normal(0, 0.004, (H, W)).astype(np.float32)
    yy, xx = np.mgrid[-4:5, -4:5]
    shown = mag <= mag_limit
    for px, py, m in zip(x[shown], y[shown], mag[shown]):
        amp = min(0.85, 0.85 * 10 ** (-0.4 * (m - 1.5)))
        ix, iy = int(round(px)), int(round(py))
        g = amp * np.exp(-((xx + ix - px) ** 2 + (yy + iy - py) ** 2) / (2 * 1.2 ** 2))
        img[iy - 4: iy + 5, ix - 4: ix + 5] += g.astype(np.float32)
    by, bx = np.mgrid[0:H, 0:W]
    img += (0.05 * np.exp(-((bx - blob_at[0]) ** 2 + (by - blob_at[1]) ** 2) / (2 * (blob_r / 2) ** 2))).astype(np.float32)
    sol = SolveResult.from_wcs(wcs, W, H, "t", "t")
    return ImageData(__import__("pathlib").Path("x.fits"), "FITS", img), sol, (ra, dec, mag, x, y, shown)


def star(name, x, y, mag=5.0):
    return SkyObject(name=name, ra_deg=0, dec_deg=0, x=x, y=y, category="star", magnitude=mag, catalog="SIMBAD")


def test_stars_galaxies_and_nebulae_are_judged():
    img, sol, (ra, dec, mag, x, y, shown) = scene()
    (xs, ys, fl), lum = V.detect(img)
    bright = np.nonzero(shown & (mag < 4.5) & (x > 20) & (x < W - 20) & (y > 20) & (y < H - 20))[0][0]
    faint = np.nonzero(~shown & (x > 50) & (x < W - 50) & (y > 50) & (y < H - 50))[0][0]
    objs = [
        star("bright", x[bright], y[bright], float(mag[bright])),
        star("faint", x[faint], y[faint], float(mag[faint])),
        SkyObject(name="Blob", ra_deg=0, dec_deg=0, x=300.0, y=650.0, category="nebula", size_arcmin=60.0),
        SkyObject(name="Empty", ra_deg=0, dec_deg=0, x=900.0, y=200.0, category="galaxy", size_arcmin=60.0),
        SkyObject(name="Outside", ra_deg=0, dec_deg=0, x=-200.0, y=200.0, category="nebula", size_arcmin=600.0),
        SkyObject(name="Huge", ra_deg=0, dec_deg=0, x=600.0, y=450.0, category="nebula", size_arcmin=900.0),
    ]
    V.classify_objects(objs, sol, xs, ys, lum, W, H)
    seen = {o.name: o.extra["visible"] for o in objs}
    assert seen == {"bright": True, "faint": False, "Blob": True, "Empty": False, "Outside": None, "Huge": None}
    order = [o.name for o in V.reorder(objs)]
    assert order == ["bright", "Blob", "Outside", "Huge", "faint", "Empty"]   # catalogue order kept per group


def test_bright_saturated_star_is_found():
    img = np.full((800, 800), 0.1, np.float32) + np.random.default_rng(2).normal(0, 0.004, (800, 800)).astype(np.float32)
    yy, xx = np.mgrid[0:800, 0:800]
    img += (0.6 * np.exp(-((xx - 400) ** 2 + (yy - 300) ** 2) / (2 * 6 ** 2))).astype(np.float32)
    img[np.hypot(xx - 400, yy - 300) < 10] = 1.0                 # a big flat-topped (saturated) star
    (xs, ys, fl), _ = V.detect(ImageData(__import__("pathlib").Path("x.jpg"), "PNG", img))
    assert len(xs) and math.hypot(xs[0] - 400, ys[0] - 300) < 4


def test_cluster_needs_more_stars_than_around_it():
    rng = np.random.default_rng(1)
    xs, ys = rng.uniform(0, 1000, 300), rng.uniform(0, 1000, 300)
    assert V.cluster_visible(xs, ys, 500, 500, 40, 1000, 1000) is False
    cx, cy = rng.normal(500, 12, 40), rng.normal(500, 12, 40)
    assert V.cluster_visible(np.r_[xs, cx], np.r_[ys, cy], 500, 500, 40, 1000, 1000) is True


def test_unmarked_stars_are_added_offline_brightest_first():
    img, sol, _ = scene()
    (xs, ys, fl), lum = V.detect(img)
    marked = [star("A", xs[0], ys[0])]                          # the brightest one already has a marker
    used = V.classify_objects(marked, sol, xs, ys, lum, W, H)
    tol = V.tolerance_px(sol, W, H)
    cand = V.unmarked_stars(marked, xs, ys, fl, used, tol, 10)
    assert 0 not in cand and len(cand) >= 10
    added = V.identify_offline(sol, xs[cand], ys[cand], tol, 10)
    assert 5 <= len(added) <= 10
    assert all(o.category == "star" and o.extra["visible"] and o.extra["added_visible"] for o in added)
    assert added[0].name.startswith("Star mag ") and added[0].catalog == "Star catalogue"
    mags = [o.magnitude for o in added]
    assert mags[0] <= np.median(mags)                            # brightest detected first


def _settings(tmp_path, **values):
    store = SettingsStore(tmp_path / "s.json")
    sec = store.section(V.VisibilitySettings())
    for k, v in values.items():
        sec.set(k, v)
    return sec


def test_defaults_and_switched_off(tmp_path):
    d = _settings(tmp_path)
    assert d.get("enabled") is True and d.get("order") == "visible" and d.get("add_stars") == 50
    assert d.get("others") == "dim"
    img, sol, _ = scene()
    objs = [star("A", 10, 10), star("B", 20, 20)]
    assert V.apply(objs, img, sol, _settings(tmp_path, enabled=False), print) is objs   # off: untouched
    assert "visible" not in objs[0].extra


def test_apply_falls_back_without_internet(tmp_path, monkeypatch):
    img, sol, _ = scene()

    def offline(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(V, "identify_with_simbad", offline)
    logs, errors = [], []
    objs = [SkyObject(name="Empty", ra_deg=0, dec_deg=0, x=900.0, y=200.0, category="galaxy", size_arcmin=60.0)]
    out = V.apply(objs, img, sol, _settings(tmp_path, enabled=True, add_stars=5), logs.append, errors)
    assert len(out) == 6 and out[-1].name == "Empty"                # invisible galaxy moved after the stars
    assert any("without names" in m for m in logs) and errors
    out2 = V.apply(objs, img, sol, _settings(tmp_path, enabled=True, add_stars=0, order="catalogue"), print)
    assert out2 == objs                                           # nothing added, order unchanged


def test_identify_with_simbad_names_and_parallax(monkeypatch):
    from platesolver.services import simbad
    img, sol, (ra, dec, mag, x, y, shown) = scene()
    i = int(np.nonzero(shown & (x > 50) & (x < W - 50) & (y > 50) & (y < H - 50))[0][0])
    rows = [{"oid": "42", "main_id": "* alf Ori", "ra": str(ra[i]), "dec": str(dec[i]), "otype": "*",
             "galdim_majaxis": "", "galdim_minaxis": "", "galdim_angle": "", "plx_value": "6.5",
             "plx_err": "0.8", "rvz_redshift": "", "vmag": str(mag[i])}]
    ids = [{"oidref": "42", "id": "NAME Betelgeuse"}, {"oidref": "42", "id": "HD 39801"}]
    monkeypatch.setattr(simbad, "query", lambda q, **k: ids if "FROM ident" in q else rows)
    out = V.identify_with_simbad(sol, np.array([x[i] + 1.0]), np.array([y[i]]), 4.0, 5, print)
    assert len(out) == 1
    o = out[0]
    assert o.name == "Betelgeuse" and o.common_name == "α Ori" and o.extra["plx_mas"] == 6.5
    assert o.catalog == "SIMBAD" and o.extra["simbad_oid"] == 42 and o.extra["visible"]


def test_filter_only_visible():
    a, b, c = star("a", 1, 1), star("b", 2, 2), star("c", 3, 3)
    a.extra["visible"], b.extra["visible"] = True, False
    f = ObjectFilter(only_visible=True)
    assert [o.name for o in (a, b, c) if f.matches(o)] == ["a"]
    assert f.active_count() == 1 and "visible only" in f.describe()
    assert ObjectFilter.from_dict(f.to_dict()).only_visible


class _Rec:
    ly_decimals = 1
    screen_px = 1.0

    def __init__(self):
        self.calls = []

    def text_extent(self, text, size=10):
        return len(text) * size * 0.6, size * 1.3

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


def test_overlay_dims_or_hides_invisible_objects():
    from platesolver.plugins.overlays.object_labels import ObjectLabelsOverlay, dim
    store = SettingsStore(None)
    ov = ObjectLabelsOverlay()
    ov.settings = store.section(ov)
    img, sol, _ = scene()
    seen, faint = star("Seen", 100, 100), star("Faint", 400, 400)
    seen.extra["visible"], faint.extra["visible"] = True, False

    def draw():
        rec = _Rec()
        ov.render(rec, img, sol, [seen, faint])
        return [a for n, a in rec.calls if n == "ellipse"]

    assert draw()[1][5] == dim(ov.color_for("star")) and draw()[0][5] == ov.color_for("star")  # default: dimmed
    store.set("visibility", "enabled", False)
    assert len(draw()) == 2 and draw()[1][5] == ov.color_for("star")       # switched off: plain markers
    store.set("visibility", "enabled", True)
    store.set("visibility", "others", "show")
    assert draw()[1][5] == ov.color_for("star")
    store.set("visibility", "others", "hide")
    assert len(draw()) == 1
    assert dim("#e8e8e8") == "#66e8e8e8"


def test_card_says_whether_visible():
    from platesolver.ui.object_info import card_html
    a, b, c = star("a", 1, 1), star("b", 2, 2), star("c", 3, 3)
    a.extra.update(visible=True, added_visible=True)
    b.extra["visible"] = False
    assert "visible" in card_html(a) and "no catalogue had marked it" in card_html(a)
    assert "too faint to be seen" in card_html(b)
    assert "In this image" not in card_html(c)


def test_simbad_lookup_uses_small_circles_in_batches(monkeypatch):
    """One question per 25 stars, small circles only, and no ORDER BY (SIMBAD rejected a qualified sort key)."""
    from platesolver.services import simbad
    queries = []
    monkeypatch.setattr(simbad, "query", lambda q, **k: (queries.append(q) or []))
    img, sol, _ = scene()
    out = V.identify_with_simbad(sol, np.linspace(100, 1000, 60), np.linspace(100, 800, 60), 4.0, 50, print)
    assert out == [] and len(queries) == 3
    for q in queries:
        assert "ORDER BY" not in q.upper() and q.count("CIRCLE(") <= V.BATCH and q.count("(") == q.count(")")
        assert "JOIN allfluxes" in q and 'f."V" <=' in q


def test_simbad_error_reason_is_kept(monkeypatch):
    """A rejected query reports SIMBAD's own reason, even when it is far into the error document."""
    from platesolver.core import net
    from platesolver.services import simbad
    body = ('<?xml version="1.0"?><VOTABLE>' + " " * 600 +
            '<INFO name="QUERY_STATUS" value="ERROR">Incorrect ADQL query: something specific</INFO></VOTABLE>')

    def bad(*a, **k):
        raise net.NetworkError("https://simbad/sync answered HTTP 400: " + body[:200], body)
    monkeypatch.setattr(net, "post_form", bad)
    with pytest.raises(net.NetworkError, match="Incorrect ADQL query: something specific"):
        simbad.query("SELECT 1", use_cache=False)
