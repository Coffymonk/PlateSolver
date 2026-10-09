# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Size/light-travel, merging, export, batch, astrometry.net, overlays."""
import csv
import io
import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from PIL import Image

from platesolver.core.batch import BatchOptions, find_images, run_batch, write_summary_csv
from platesolver.core.export import write_objects_csv, write_solution_into_fits, write_wcs_file
from platesolver.core.general import GeneralSettings
from platesolver.core.identifiers import canonical, merge_objects
from platesolver.core.models import Distance, Link, SkyObject, SolveResult, light_left_text
from platesolver.core.pipeline import Pipeline
from platesolver.core.plugin import TaskContext
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import SettingsStore


def make_wcs(ra=83.82, dec=-5.39, scale=1.709, w=400, h=300, rot=0.0):
    s = scale / 3600
    r = np.radians(rot)
    cd = np.array([[np.cos(r), -np.sin(r)], [np.sin(r), np.cos(r)]]) @ np.array([[-s, 0], [0, -s]])
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crpix = [(w + 1) / 2, (h + 1) / 2]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.cd = cd
    wcs.wcs.set()
    return wcs


@pytest.fixture
def pipeline(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    reg = PluginRegistry(store).discover()
    assert not reg.errors, reg.errors
    for sid in ("catalog.simbad", "link.wikipedia", "solver.astap", "solver.astrometry_net"):
        reg.get(sid).settings.set("_enabled", False)
    return Pipeline(reg, store.section(GeneralSettings()))


# --------------------------------------------------------------------------- size and light
def test_true_size_and_light_travel():
    m42 = SkyObject("M 42", 83.8, -5.4, category="nebula", size_arcmin=65.0,
                    distance=Distance(1344.0))
    # 1344 ly × 65′ ≈ 25.4 ly (Wikipedia: about 24 ly across)
    assert m42.physical_size_ly() == pytest.approx(25.4, abs=0.1)
    assert light_left_text(1344, 2026) == "around the year 680"
    assert light_left_text(2.537e6) == "about 2.5 million years ago"
    assert light_left_text(4000, 2026) == "around 2,000 BC"
    assert light_left_text(444, 2026) == "around the year 1580"
    star = SkyObject("x", 0, 0, category="star", size_arcmin=1.0, distance=Distance(100.0))
    assert star.physical_size_ly() is None
    # cosmological: size uses the angular-diameter distance, light uses the look-back time
    g = SkyObject("g", 0, 0, category="galaxy", size_arcmin=1.0,
                  distance=Distance(1.3e9, angular_diameter_ly=1.2e9, lookback_years=1.3e9))
    assert g.physical_size_ly() == pytest.approx(1.2e9 * np.radians(1 / 60), rel=1e-3)
    assert "1.3 billion" in light_left_text(g.light_travel_years())


def test_redshift_gives_size_distance():
    from platesolver.plugins.distances.redshift import RedshiftDistance
    d = RedshiftDistance().resolve(SkyObject("g", 0, 0, category="galaxy", extra={"redshift": 0.1}), TaskContext())
    assert d.angular_diameter_ly < d.light_years      # D_A < light-travel distance
    assert d.lookback_years == pytest.approx(d.light_years)


# --------------------------------------------------------------------------- merging
def test_canonical_and_merge():
    assert canonical("NGC  1976") == canonical("NGC1976") == "NGC1976"
    assert canonical("Cl Melotte 22") == canonical("Mel022") == "MEL22"
    assert canonical("Barnard 33") == canonical("B033") == "B33"
    simbad = SkyObject("M 42", 83.822, -5.391, category="nebula", size_arcmin=66, catalog="SIMBAD",
                       extra={"identifiers": ["M 42", "NGC 1976"], "plx_mas": None})
    openngc = SkyObject("M 42", 83.818, -5.390, category="nebula", common_name="Orion Nebula", magnitude=4.0,
                        catalog="OpenNGC", extra={"identifiers": ["M 42", "NGC 1976", "LBN 974"], "plx_mas": 2.4})
    other = SkyObject("NGC 1977", 83.85, -4.83, category="nebula", catalog="OpenNGC",
                      extra={"identifiers": ["NGC 1977"]})
    far_namesake = SkyObject("NGC 1976", 200.0, 10.0, catalog="X", extra={"identifiers": ["NGC 1976"]})
    merged = merge_objects([simbad, openngc, other, far_namesake])
    assert [m.name for m in merged] == ["M 42", "NGC 1977", "M 42" if False else "NGC 1976"]
    m = merged[0]
    assert m.catalog == "SIMBAD + OpenNGC"
    assert m.common_name == "Orion Nebula" and m.magnitude == 4.0 and m.size_arcmin == 66
    assert m.extra["plx_mas"] == 2.4 and "LBN 974" in m.extra["identifiers"]


def test_openngc_works_offline(pipeline):
    from platesolver.core.models import ImageData
    sol = SolveResult.from_wcs(make_wcs(w=2000, h=1400), 2000, 1400, "t", "t")
    img = ImageData(Path("x.fits"), "FITS", np.zeros((1400, 2000), np.float32))
    objs = pipeline.find_objects(img, sol, TaskContext())
    m42 = next(o for o in objs if o.name == "M 42")
    assert m42.common_name == "Orion Nebula" and m42.catalog == "OpenNGC"
    assert "NGC 1976" in m42.extra["identifiers"]
    assert m42.links and "simbad" in m42.links[-1].url


# --------------------------------------------------------------------------- export
def test_objects_csv(tmp_path):
    o = SkyObject("M 42", 83.82, -5.39, object_type="Emission nebula", category="nebula", common_name="Orion Nebula",
                  size_arcmin=65.0, distance=Distance(1344.04, 20.0, "published", "SIMBAD"),
                  links=[Link("Wikipedia", "https://en.wikipedia.org/wiki/Orion_Nebula", "Wikipedia")])
    write_objects_csv(tmp_path / "a.csv", [o], 1, "comma")
    rows = list(csv.DictReader(open(tmp_path / "a.csv", encoding="utf-8-sig")))
    assert rows[0]["distance_ly"] == "1344.0" and rows[0]["true_size_ly"] == "25.4"
    assert rows[0]["wikipedia"].endswith("Orion_Nebula") and rows[0]["light_left"] == light_left_text(1344.04)
    write_objects_csv(tmp_path / "b.csv", [o], 2, "semicolon")
    rows = list(csv.DictReader(open(tmp_path / "b.csv", encoding="utf-8-sig"), delimiter=";"))
    assert rows[0]["distance_ly"] == "1344,04"


def test_wcs_file_and_fits_header_round_trip(tmp_path, pipeline):
    """Writing the solution into a bottom-up FITS and reopening it must give the same sky positions."""
    w, h = 64, 48
    truth = make_wcs(w=w, h=h, rot=20)          # in FILE orientation
    fits.PrimaryHDU(np.random.default_rng(0).integers(0, 60000, (h, w), dtype=np.uint16)).writeto(tmp_path / "a.fits")
    img = pipeline.load(tmp_path / "a.fits")
    assert img.rows_flipped and img.header_wcs is None
    # the solution a solver would find, in display orientation
    from platesolver.plugins.loaders.fits_loader import flip_wcs_vertically
    sol = SolveResult.from_wcs(flip_wcs_vertically(truth, h), w, h, "astap", "ASTAP")
    write_wcs_file(tmp_path / "a.wcs", sol, img)
    side = WCS(fits.getheader(tmp_path / "a.wcs"))
    assert side.pixel_to_world_values(10, 5) == pytest.approx(truth.pixel_to_world_values(10, 5), abs=1e-9)
    write_solution_into_fits(tmp_path / "a.fits", sol, img)
    again = pipeline.load(tmp_path / "a.fits")
    assert again.header_wcs is not None
    assert fits.getheader(tmp_path / "a.fits")["PLTSOLVD"] is True
    x, y = 12.3, 40.1
    assert again.header_wcs.pixel_to_world_values(x, y) == pytest.approx(sol.wcs.pixel_to_world_values(x, y), abs=1e-9)
    assert np.array_equal(fits.getdata(tmp_path / "a.fits").shape, (h, w))


# --------------------------------------------------------------------------- batch
def test_batch_folder(tmp_path, pipeline):
    folder = tmp_path / "night"
    (folder / "sub").mkdir(parents=True)
    solved_hdr = make_wcs(w=40, h=30).to_header()
    fits.PrimaryHDU(np.zeros((30, 40), np.uint16), header=solved_hdr).writeto(folder / "solved.fits")
    fits.PrimaryHDU(np.zeros((30, 40), np.uint16)).writeto(folder / "unsolved.fits")
    Image.fromarray(np.zeros((30, 40), np.uint8)).save(folder / "sub" / "x.jpg")
    (folder / "notes.txt").write_text("hi")
    exts = {e for l in pipeline.loaders() for e in l.extensions}
    assert [p.name for p in find_images(folder, exts, False)] == ["solved.fits", "unsolved.fits"]
    files = find_images(folder, exts, True)
    assert len(files) == 3
    seen = []
    items = run_batch(pipeline, files, BatchOptions(write_wcs=True, objects_csv=True), TaskContext(),
                      on_item=lambda i, it: seen.append(it.status))
    by = {it.path.name: it for it in items}
    assert by["solved.fits"].status == "already solved" and by["solved.fits"].ra_deg == pytest.approx(83.82)
    assert by["unsolved.fits"].status == "skipped"           # blank image: no stars, skipped in batch
    assert by["x.jpg"].status == "skipped"
    assert (folder / "solved_objects.csv").exists()
    assert not (folder / "solved.wcs").exists()              # it wasn't newly solved
    write_summary_csv(tmp_path / "summary.csv", items)
    assert len(list(csv.reader(open(tmp_path / "summary.csv", encoding="utf-8-sig")))) == 4
    assert seen == ["already solved", "skipped", "skipped"]


# --------------------------------------------------------------------------- astrometry.net
def test_astrometry_net_flow(monkeypatch, tmp_path):
    from platesolver.core import net
    from platesolver.core.models import ImageData
    from platesolver.plugins.solvers import astrometry_net as an

    W, H = 5000, 3000
    truth = make_wcs(w=W, h=H, rot=30, scale=1.709)
    img = ImageData(Path("big.fits"), "FITS", np.random.default_rng(1).random((H, W)).astype(np.float32))
    img.hints.ra_deg, img.hints.dec_deg = 83.8, -5.4
    img.hints.focal_length_mm, img.hints.pixel_size_um = 350, 2.9
    solver = an.AstrometryNetSolver()
    solver.settings = SettingsStore(None).section(solver)
    assert not solver.is_available()[0]
    solver.settings.set("api_key", "abc123")
    assert solver.is_available()[0]
    calls = {"polls": 0}

    def post_form(url, data, timeout=0):
        assert url == "https://nova.astrometry.net/api/login"
        assert json.loads(data["request-json"]) == {"apikey": "abc123"}
        return b'{"status": "success", "session": "S1"}'

    def post_multipart(url, fields, files, timeout=0):
        req = json.loads(fields["request-json"])
        calls["upload"] = req
        content = files["file"][1]
        calls["shape"] = fits.getdata(io.BytesIO(content)).shape
        return b'{"status": "success", "subid": 77}'

    def get(url, params=None, timeout=0):
        if url.endswith("/api/submissions/77"):
            calls["polls"] += 1
            return b'{"jobs": [null]}' if calls["polls"] < 2 else b'{"jobs": [555]}'
        if url.endswith("/api/jobs/555"):
            return b'{"status": "success"}'
        if url.endswith("/wcs_file/555"):
            b = 3   # server solved the 3x binned upload: give the binned WCS
            bw = truth.deepcopy()
            bw.wcs.crpix = [(c + (b - 1) / 2) / b for c in truth.wcs.crpix]
            bw.wcs.cd = truth.wcs.cd * b
            return fits.PrimaryHDU(header=bw.to_header()).header.tostring().encode()
        raise AssertionError(url)
    monkeypatch.setattr(net, "post_form", post_form)
    monkeypatch.setattr(net, "post_multipart", post_multipart)
    monkeypatch.setattr(net, "get", get)
    monkeypatch.setattr(an.AstrometryNetSolver, "_sleep", staticmethod(lambda ctx, s: None))
    res = solver.solve(img, TaskContext())
    assert res.success
    assert calls["shape"] == (1000, 1666)                       # 5000 px -> binned 3x (max 2000)
    up = calls["upload"]
    assert up["publicly_visible"] == "n" and up["session"] == "S1"
    assert up["scale_est"] == pytest.approx(1.709 * 3, rel=1e-3)
    assert up["center_ra"] == 83.8 and up["radius"] == 10.0
    for px in [(0, 0), (4999, 2999), (1234.5, 2222.2)]:
        got = [float(v) for v in res.wcs.pixel_to_world_values(*px)]
        want = [float(v) for v in truth.pixel_to_world_values(*px)]
        assert got == pytest.approx(want, abs=1e-7), (px, got, want)


# --------------------------------------------------------------------------- overlays
class Rec:
    ly_decimals = 1
    screen_px = 1.0

    def __init__(self):
        self.calls = []

    def text_extent(self, text, size=10):
        return len(text) * size * 0.6, size * 1.3

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


def settings_for(plugin):
    plugin.settings = SettingsStore(None).section(plugin)
    return plugin


def test_grid_labels_and_lines():
    from platesolver.plugins.overlays.coordinate_grid import CoordinateGrid, format_dec_label, format_ra_label
    from platesolver.core.models import ImageData
    assert format_dec_label(-5.5, 30) == "-5°30′" and format_dec_label(10, 60) == "+10°"
    assert format_ra_label(83.75, 60) == "5h35m" and format_ra_label(90, 3600) == "6h"
    W, H = 2400, 1600
    sol = SolveResult.from_wcs(make_wcs(ra=83.8, dec=0, scale=30, w=W, h=H, rot=-10), W, H, "t", "t")
    img = ImageData(Path("x"), "FITS", np.zeros((H, W), np.float32))
    rec = Rec()
    settings_for(CoordinateGrid()).render(rec, img, sol, [])
    texts = [a[2] for n, a in rec.calls if n == "text"]
    assert "0h" not in texts and any(t.startswith("5h") for t in texts) and "+0°" in texts
    assert sum(1 for n, _ in rec.calls if n == "polyline") >= 8


def test_constellations_in_orion():
    from platesolver.plugins.overlays.constellations import ConstellationsOverlay
    from platesolver.core.models import ImageData
    W, H = 2400, 1600
    sol = SolveResult.from_wcs(make_wcs(ra=83.8, dec=0, scale=30, w=W, h=H), W, H, "t", "t")
    img = ImageData(Path("x"), "FITS", np.zeros((H, W), np.float32))
    rec = Rec()
    settings_for(ConstellationsOverlay()).render(rec, img, sol, [])
    texts = [a[2] for n, a in rec.calls if n == "text"]
    assert "ORION" in texts
    lines = [a[0] for n, a in rec.calls if n == "polyline"]
    # Alnilam, the middle star of the belt (RA 84.05, Dec -1.20), is a corner of the figure
    bx, by = sol.radec_to_pixel(84.0534, -1.2019)
    assert min(min(np.hypot(x - bx, y - by) for x, y in line) for line in lines) < 2.0


def test_labels_do_not_overlap():
    from platesolver.plugins.overlays.object_labels import ObjectLabelsOverlay
    from platesolver.core.models import ImageData
    sol = SolveResult.from_wcs(make_wcs(w=1000, h=800), 1000, 800, "t", "t")
    img = ImageData(Path("x"), "FITS", np.zeros((800, 1000), np.float32))
    objs = [SkyObject(f"NGC {i}", 0, 0, category="galaxy", x=500 + i * 2, y=400) for i in range(10)]
    rec = Rec()
    ov = settings_for(ObjectLabelsOverlay())
    ov.render(rec, img, sol, objs)
    n_labels = sum(1 for n, _ in rec.calls if n == "text")
    assert 1 <= n_labels < 10                                   # crowded: only some fit
    assert sum(1 for n, _ in rec.calls if n == "ellipse") == 10  # but every object is marked
    ov.settings.set("hide_overlaps", False)
    rec2 = Rec()
    ov.render(rec2, img, sol, objs)
    assert sum(1 for n, _ in rec2.calls if n == "text") == 10


def test_manual_links_and_settings_reference():
    """Every link in the manual points to a heading, including each settings page's generated section."""
    import re
    from platesolver.core.batch import BatchSettings
    from platesolver.core.equipment import EquipmentSettings
    from platesolver.core.export import ExportSettings
    from platesolver.ui.help_window import manual_html
    store = SettingsStore(None)
    reg = PluginRegistry(store).discover()
    from platesolver.core.preprocess import SolvePrepSettings
    from platesolver.core.stars import StarCheckSettings
    from platesolver.core.quiz import QuizSettings
    from platesolver.core.objecthint import HintSettings
    from platesolver.core.profiles import ProfileSettings
    from platesolver.core.phoneaids import PhoneAidSettings
    from platesolver.core.distortion import DistortionSettings
    from platesolver.core.visibility import VisibilitySettings
    sections = [GeneralSettings(), EquipmentSettings(), StarCheckSettings(), SolvePrepSettings(), PhoneAidSettings(),
                DistortionSettings(), VisibilitySettings(),
                ExportSettings(),
                BatchSettings(), HintSettings(), ProfileSettings(), QuizSettings()] + reg.plugins
    html = manual_html(sections)
    anchors = set(re.findall(r'id="([^"]+)"', html))
    links = set(re.findall(r'href="#([^"]+)"', html))
    assert not links - anchors, links - anchors
    for sec in sections:   # the Settings dialog's Help button jumps to these
        assert "settings-" + sec.section_id.replace(".", "-") in anchors
    assert "{{" not in html


def test_astrometry_net_asks_before_uploading(monkeypatch):
    from platesolver.core import net
    from platesolver.core.models import ImageData
    from platesolver.plugins.solvers import astrometry_net as an

    solver = an.AstrometryNetSolver()
    solver.settings = SettingsStore(None).section(solver)
    solver.settings.set("api_key", "abc")
    img = ImageData(Path("m31.jpg"), "JPEG", np.zeros((100, 120), np.float32))
    asked = []

    def no_network(*a, **k):
        raise AssertionError("nothing may be sent when the user says no")
    monkeypatch.setattr(net, "post_form", no_network)
    monkeypatch.setattr(net, "post_multipart", no_network)
    ctx = TaskContext(confirm=lambda title, text, key="", **kw: asked.append((title, text, key)) or False)
    res = solver.solve(img, ctx)
    assert not res.success and res.message == "upload declined"
    title, text, key = asked[0]
    assert "m31.jpg" in text and "not</b> deleted" in text and key == "astrometry_upload"
    # with asking switched off, no question is asked
    solver.settings.set("ask_before_upload", False)
    asked.clear()
    with pytest.raises(AssertionError, match="nothing may be sent"):
        solver.solve(img, ctx)
    assert asked == []



def _synthetic(h=600, w=900, stars=150, nebula=True, hot=0, seed=1):
    rng = np.random.default_rng(seed)
    img = rng.normal(800, 25, (h, w))
    yy, xx = np.mgrid[0:h, 0:w]
    if nebula:
        for _ in range(5):
            cx, cy, r = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(60, 200)
            img += rng.uniform(300, 1500) * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r))
        for _ in range(12):
            x0 = rng.uniform(0, w)
            img += 300 * np.exp(-((xx - x0 - 0.3 * yy) ** 2) / (2 * 6 ** 2))
    for _ in range(stars):
        x, y = rng.uniform(8, w - 8), rng.uniform(8, h - 8)
        img += (rng.pareto(1.5) * 400 + 200) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.1 ** 2))
    for _ in range(hot):
        img[rng.integers(0, h), rng.integers(0, w)] += 5000
    return img.astype(np.float32)


def test_star_count_tells_starless_from_starry():
    from platesolver.core.stars import count_stars
    assert count_stars(_synthetic(stars=150)).count > 50              # the faintest are below the limit
    assert count_stars(_synthetic(stars=0)).count < 5                 # nebula only
    assert count_stars(_synthetic(stars=0, hot=300)).count < 5        # hot pixels are not stars
    assert count_stars(np.zeros((400, 600), np.float32)).count == 0


def test_starless_warning_and_choices(pipeline, tmp_path):
    from platesolver.core.models import ImageData
    img = ImageData(Path("starless.tif"), "TIFF", _synthetic(stars=0) / 4000.0)
    asked = []
    ctx = TaskContext(confirm=lambda title, text, key="", **kw: asked.append((title, kw)) or False)
    res = pipeline.solve(img, ctx)
    assert not res.success and "starless" in res.message and img.star_count < 15
    assert asked and asked[0][1] == {"yes": "Solve anyway", "no": "Don't solve"}
    # "Solve anyway" goes on to the solvers
    res = pipeline.solve(img, TaskContext(confirm=lambda *a, **k: True))
    assert "starless" not in res.message and res.attempts
    # a normal image is not questioned
    asked.clear()
    starry = ImageData(Path("stars.tif"), "TIFF", _synthetic(stars=150) / 4000.0)
    pipeline.solve(starry, ctx)
    assert asked == [] and starry.star_count > 50
    # switched off: no check at all
    pipeline.star_check.set("enabled", False)
    img.star_count = None
    pipeline.solve(img, ctx)
    assert asked == [] and img.star_count is None


def _vignetted(h=600, w=900, seed=4):
    img = _synthetic(h=h, w=w, stars=200, nebula=False, seed=seed) - 800
    yy, xx = np.mgrid[0:h, 0:w]
    r2 = ((xx - w / 2) ** 2 + (yy - h / 2) ** 2) / ((w / 2) ** 2 + (h / 2) ** 2)
    return ((1000 + img) * (1 - 0.55 * r2) + 600 * xx / w).astype(np.float32)


def test_flatten_and_hot_pixels():
    from platesolver.core.preprocess import background_model, flatten, remove_hot_pixels
    img = _vignetted()
    assert background_model(img)[1] > 0.4
    flat, before = flatten(img)
    assert before > 0.4 and background_model(flat)[1] < 0.08
    noisy = img.copy()
    rng = np.random.default_rng(0)
    idx = rng.integers(0, img.size, 500)
    noisy.flat[idx] += 8000
    cleaned, n = remove_hot_pixels(noisy)
    assert n >= 480
    assert remove_hot_pixels(img)[1] == 0          # real stars are left alone


def test_solving_aids_modes(tmp_path):
    from platesolver.core.interfaces import Solver
    from platesolver.core.models import ImageData
    from platesolver.core.preprocess import background_model

    calls = []

    class PickySolver(Solver):
        """Solves only evenly lit images, like a star detector confused by vignetting."""
        plugin_id, name, retry_prepared = "picky", "Picky", True

        def solve(self, image, ctx):
            even = background_model(image.luminance())[1] < 0.1
            calls.append(even)
            if not even:
                return SolveResult.failed("no stars matched", self.plugin_id, self.name)
            return SolveResult.from_wcs(make_wcs(w=image.width, h=image.height), image.width, image.height,
                                        self.plugin_id, self.name)

    store = SettingsStore(None)
    reg = PluginRegistry(store).discover()
    for p in list(reg.plugins):
        if p.kind == "solver":
            p.settings.set("_enabled", False)
    picky = PickySolver()
    picky.settings = store.section(picky)
    reg.plugins.append(picky)
    pipe = Pipeline(reg, store.section(GeneralSettings()))
    pipe.phone_aids.set("mode", "never")      # tested in test_phoneaids
    uneven = lambda: ImageData(Path("vignetted.fits"), "FITS", _vignetted() / 5000.0, is_linear=True)

    # automatic + clearly uneven raw data: the cleaned copy is used straight away
    res = pipe.solve(uneven(), TaskContext())
    assert res.success and calls == [True] and "cleaned copy" in res.message
    assert res.attempts == ["Picky (cleaned copy): solved in %.1f s" % res.elapsed_s]
    # a processed (stretched) image is tried as it is first, then cleaned
    calls.clear()
    img = uneven()
    img.is_linear = False
    res = pipe.solve(img, TaskContext())
    assert res.success and calls == [False, True]
    # only as a second try: original first, then the cleaned copy
    calls.clear()
    pipe.solve_prep.set("flatten", "retry")
    res = pipe.solve(uneven(), TaskContext())
    assert res.success and calls == [False, True]
    assert res.attempts[0] == "Picky: no stars matched"
    # never: one attempt, fails
    calls.clear()
    pipe.solve_prep.set("flatten", "never")
    img = uneven()
    res = pipe.solve(img, TaskContext())
    assert not res.success and calls == [False] and img.background_unevenness is None
    # the measured unevenness is kept for the Solution tab
    pipe.solve_prep.set("flatten", "auto")
    img = uneven()
    pipe.solve(img, TaskContext())
    assert img.background_unevenness > 0.4
