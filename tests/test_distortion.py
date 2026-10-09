# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Lens distortion: star catalogue, star detection, the TAN-SIP fit and the ring-by-ring matching."""
import math
import os

import numpy as np
import pytest
from astropy.wcs import WCS, Sip

from platesolver.core import distortion as ds
from platesolver.core import starcatalog as sc
from platesolver.core.general import GeneralSettings
from platesolver.core.models import ImageData, SolveResult
from platesolver.core.pipeline import Pipeline
from platesolver.core.plugin import TaskContext
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import SettingsStore

W, H, SCALE = 1500, 2000, 120.0          # a phone-like field: 50° × 67°


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path / "home"))
    sc.reset_cache()
    yield
    sc.reset_cache()


def linear(scale=SCALE, rot=25.0, ra=300.0, dec=40.0, w=W, h=H):
    s, t = scale / 3600, math.radians(rot)
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crpix = [(w + 1) / 2, (h + 1) / 2]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.cd = [[-s * math.cos(t), s * math.sin(t)], [-s * math.sin(t), -s * math.cos(t)]]
    wcs.wcs.set()
    return wcs


def distorted(barrel=-0.03, w=W, h=H):
    true = linear(w=w, h=h)
    true.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
    k = barrel / (math.hypot(w / 2, h / 2) ** 2)
    a, b = np.zeros((4, 4)), np.zeros((4, 4))
    a[3, 0] = a[1, 2] = b[2, 1] = b[0, 3] = k
    true.sip = Sip(a, b, None, None, true.wcs.crpix)
    true.wcs.set()
    return true


def render(wcs, w=W, h=H, seed=1):
    cat = sc.load()
    x, y = wcs.all_world2pix(cat["ra"], cat["dec"], 0, quiet=True)
    r0, d0 = map(math.radians, wcs.wcs.crval)
    centre = np.array([math.cos(d0) * math.cos(r0), math.cos(d0) * math.sin(r0), math.sin(d0)])
    ok = (cat["xyz"] @ centre > 0.3) & np.isfinite(x) & (x > 3) & (x < w - 4) & (y > 3) & (y < h - 4)
    img = np.full((h, w), 0.1, np.float32) + np.random.default_rng(seed).normal(0, 0.003, (h, w)).astype(np.float32)
    yy, xx = np.mgrid[-3:4, -3:4]
    for px, py, m in zip(x[ok], y[ok], cat["mag"][ok]):
        amp = min(0.9, 0.9 * 10 ** (-0.4 * (m - 2)))
        xi, yi = int(round(px)), int(round(py))
        img[yi - 3:yi + 4, xi - 3:xi + 4] += np.exp(-((xx + xi - px) ** 2 + (yy + yi - py) ** 2) / (2 * 1.3 ** 2)) * amp
    return img


def sep_px(wcs1, wcs2, x, y, scale=SCALE):
    a = wcs1.pixel_to_world_values(x, y)
    b = wcs2.pixel_to_world_values(x, y)
    d1, d2 = math.radians(a[1]), math.radians(b[1])
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(math.radians(a[0] - b[0]))
    return math.degrees(math.acos(min(1.0, c))) * 3600 / scale


def test_builtin_catalogue():
    cat = sc.load()
    assert len(cat["ra"]) > 40000 and cat["mag"].max() <= 8.01 and "Hipparcos" in sc.describe()
    ra, dec, mag, x, y = sc.in_field(linear(), W, H)
    assert len(ra) > 500 and np.all(np.diff(mag) >= 0)            # brightest first
    assert np.all((x > -21) & (x < W + 20) & (y > -21) & (y < H + 20))


def test_tan_fit_matches_astropy_convention():
    lin = linear()
    rng = np.random.default_rng(0)
    px, py = rng.uniform(0, W, 100), rng.uniform(0, H, 100)
    ra, dec = lin.pixel_to_world_values(px, py)
    fit, resid = ds.fit_tan_sip(px, py, ra, dec, W, H, 1, 301.0, 41.0)
    assert resid.max() < 0.05 and fit.wcs.crval[0] == pytest.approx(300.0, abs=1e-4)


def test_detect_stars_finds_centroids():
    img = np.full((300, 300), 0.1, np.float32)
    yy, xx = np.mgrid[0:300, 0:300]
    for x, y in ((50.3, 60.7), (200.5, 120.2), (150.0, 250.8)):
        img += 0.5 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.5 ** 2))
    x, y, _ = ds.detect_stars(img)
    found = sorted(zip(np.round(x, 1), np.round(y, 1)))
    assert len(found) == 3
    for (fx, fy), (tx, ty) in zip(found, sorted([(50.3, 60.7), (150.0, 250.8), (200.5, 120.2)])):
        assert abs(fx - tx) < 0.15 and abs(fy - ty) < 0.15


def test_distortion_is_measured_and_corrected():
    true = distorted(-0.03)
    img = render(true)
    start = linear()
    start.wcs.cd = np.array(start.wcs.cd) * 1.006           # small scale error from a centre-only solve
    start.wcs.set()
    fit = ds.correct(start, img, 0.5)
    assert fit is not None and fit.matched > 100 and fit.rms_px < 1.0
    assert sep_px(true, start, 0, 0) > 10                     # labels were well off in the corners
    for x, y in ((0, 0), (W - 1, H - 1), (W - 1, 0), (W / 2, H / 2)):
        assert sep_px(true, fit.wcs, x, y) < 1.0              # and now sit on their stars
    assert "Lens distortion corrected" in fit.summary() and fit.edge_after_px < fit.edge_before_px


def test_unmatched_field_is_left_alone():
    rng = np.random.default_rng(5)
    img = np.full((H, W), 0.1, np.float32) + rng.normal(0, 0.003, (H, W)).astype(np.float32)
    for x, y in rng.uniform(10, 1400, (400, 2)):
        img[int(y), int(x)] += 0.5
        img[int(y) + 1, int(x)] += 0.3
    logs = []
    assert ds.correct(linear(), img, 0.5, logs.append) is None and logs


def test_pipeline_modes(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    p = Pipeline(PluginRegistry(store).discover(), store.section(GeneralSettings()))
    true = distorted(-0.03)
    img = ImageData(tmp_path / "phone.jpg", "JPEG", render(true))
    start = SolveResult.from_wcs(linear(), W, H, "astap", "ASTAP", message="Solved")
    out = p.correct_distortion(img, start, TaskContext())
    assert out is not start and "Lens distortion corrected" in out.message and out.wcs.sip is not None
    assert sep_px(true, out.wcs, 0, 0) < 1.5
    p.distortion.set("correct", "phone")
    assert p.correct_distortion(img, start, TaskContext()) is start
    assert p.correct_distortion(img, start, TaskContext(), after_aids=True) is not start
    p.distortion.set("correct", "never")
    assert p.correct_distortion(img, start, TaskContext(), after_aids=True) is start
    p.distortion.set("correct", "wide")
    narrow = SolveResult.from_wcs(linear(scale=2.0), W, H, "astap", "ASTAP")      # 0.8° telescope field
    assert p.correct_distortion(img, narrow, TaskContext()) is narrow
    stored = SolveResult.from_wcs(linear(), W, H, "header_wcs", "Existing solution in file")
    assert p.correct_distortion(img, stored, TaskContext()) is stored


def test_export_and_flip_keep_distortion(tmp_path):
    from platesolver.core.export import solution_header
    from platesolver.plugins.loaders.fits_loader import flip_wcs_vertically
    true = distorted(-0.03)
    res = SolveResult.from_wcs(true, W, H, "astap", "ASTAP")
    img = ImageData(tmp_path / "x.jpg", "JPEG", np.zeros((H, W), np.float32))
    hdr = solution_header(res, img)
    assert hdr["CTYPE1"] == "RA---TAN-SIP" and hdr["A_ORDER"] == 3 and "A_3_0" in hdr
    back = WCS(hdr)
    f = lambda w, x, y: [float(v) for v in w.pixel_to_world_values(x, y)]
    assert f(back, 10, 20) == pytest.approx(f(true, 10, 20), abs=1e-6)
    flipped = flip_wcs_vertically(true, H)
    for x, y in ((0, 0), (700, 1500), (W - 1, 10)):
        assert f(flipped, x, H - 1 - y) == pytest.approx(f(true, x, y), abs=1e-7)


SAMPLE_TSV = "#\n# VizieR Astronomical Server\n#RESOURCE=yCat_1259\n\nRAmdeg\tDEmdeg\tVTmag\ndeg\tdeg\tmag\n" \
             "----------\t----------\t------\n"


def test_update_and_builtin(monkeypatch):
    from platesolver.core import net
    rng = np.random.default_rng(2)
    rows = "".join(f"{a:.6f}\t{d:+.6f}\t{m:.3f}\n" for a, d, m in
                   zip(rng.uniform(0, 360, 1500), rng.uniform(-90, 90, 1500), rng.uniform(1, 9, 1500)))
    calls = []

    def fake_get(url, params=None, timeout=60):
        calls.append((url, params))
        return (SAMPLE_TSV + rows + "\t\t\n").encode("latin-1")
    monkeypatch.setattr(net, "get", fake_get)
    logs = []
    msg = sc.download(9, logs.append)
    assert "Tycho-2" in msg and "1 500 stars" in msg and calls[0][1]["VTmag"] == "<9"
    assert sc.user_file().exists() and "Downloaded" in sc.describe() and len(sc.load()["ra"]) == 1500
    assert "Hipparcos" in sc.use_builtin() and not sc.user_file().exists()

    def failing(url, params=None, timeout=60):
        raise net.NetworkError("offline")
    monkeypatch.setattr(net, "get", failing)
    with pytest.raises(RuntimeError, match="could not be downloaded"):
        sc.download(9)
    assert "Hipparcos" in sc.describe()                       # nothing changed


def test_settings_page_buttons(monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton
    from platesolver.core.distortion import DistortionSettings
    from platesolver.ui.settings_dialog import SectionPage

    app = QApplication.instance() or QApplication([])
    seen = []
    monkeypatch.setattr(sc, "download", lambda limit, log: seen.append(limit) or "Downloaded: test catalogue")
    store = SettingsStore(tmp_path / "s.json")
    sec = DistortionSettings()
    page = SectionPage(sec, store.section(sec))
    ed = next(e for e in page.editors if e.field.key == "update")
    depth = next(e for e in page.editors if e.field.key == "depth")
    assert isinstance(ed.widget, QPushButton) and "Hipparcos" in ed.status.text()
    depth.set_value("10")
    ed._run_action()
    ed._worker.wait(5000)
    app.processEvents()
    assert seen == [10.0] and "test catalogue" in ed.status.text()
    page.apply()
    assert not store.has("distortion", "update") and store.get("distortion", "depth") == "10"


def test_mirrored_images_are_recognised_and_corrected():
    """RASA/HyperStar, star diagonals, selfie cameras and software flips give mirrored images: the solution
    says so, and the distortion fit (which matches stars against the catalogue) still works."""
    flip = np.array([[-1.0, 0.0], [0.0, 1.0]])
    true = distorted(-0.03)
    true.wcs.cd = true.wcs.cd @ flip               # the barrel term is symmetric, so its SIP terms stay the same
    true.wcs.set()
    start = linear()
    start.wcs.cd = start.wcs.cd @ flip
    start.wcs.set()
    assert SolveResult.from_wcs(start, W, H, "t", "t").mirrored
    assert not SolveResult.from_wcs(linear(), W, H, "t", "t").mirrored
    fit = ds.correct(start, render(true), 1.0, lambda m: None)
    assert fit is not None and sep_px(true, fit.wcs, 0, 0) < 1.5 and sep_px(true, fit.wcs, W - 1, H - 1) < 1.5
    assert SolveResult.from_wcs(fit.wcs, W, H, "t", "t").mirrored
