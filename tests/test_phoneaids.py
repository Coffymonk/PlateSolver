# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Phone and wide-field aids: foreground mask, centre crops, mapping the solution back, and the flow."""
import math
from pathlib import Path

import numpy as np
import pytest
from astropy.wcs import WCS, Sip

from platesolver.core import phoneaids as pa
from platesolver.core.general import GeneralSettings
from platesolver.core.models import ImageData, SolveHints, SolveResult
from platesolver.core.pipeline import Pipeline
from platesolver.core.plugin import TaskContext
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import SettingsStore

SAMPLES = Path(__file__).resolve().parent.parent / "Samples"
W, H = 1200, 1600


def star_field(h=H, w=W, n=900, seed=1, sky=0.12):
    rng = np.random.default_rng(seed)
    img = np.full((h, w), sky, np.float32) + rng.normal(0, 0.004, (h, w)).astype(np.float32)
    ys, xs = rng.integers(3, h - 3, n), rng.integers(3, w - 3, n)
    amp = rng.uniform(0.05, 0.6, n)
    for y, x, a in zip(ys, xs, amp):
        img[y - 2:y + 3, x - 2:x + 3] += a * np.array([[.05, .2, .3, .2, .05], [.2, .6, .8, .6, .2],
                                                      [.3, .8, 1, .8, .3], [.2, .6, .8, .6, .2],
                                                      [.05, .2, .3, .2, .05]], np.float32)
    return img


def true_wcs(w=W, h=H, scale=60.0, ra=310.0, dec=45.0, rot=20.0, sip=False):
    s = scale / 3600
    t = math.radians(rot)
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"] if sip else ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crpix = [(w + 1) / 2, (h + 1) / 2]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.cd = [[-s * math.cos(t), s * math.sin(t)], [-s * math.sin(t), -s * math.cos(t)]]
    if sip:
        a = np.zeros((4, 4))
        b = np.zeros((4, 4))
        a[2, 0], a[0, 2], a[3, 0], b[0, 2], b[1, 1], b[0, 3] = 2e-5, 1e-5, 3e-8, 2e-5, 1e-5, 3e-8
        wcs.sip = Sip(a, b, None, None, wcs.wcs.crpix)
    wcs.wcs.set()
    return wcs


# --------------------------------------------------------------------------- mapping back
@pytest.mark.parametrize("factor", [1, 2])
@pytest.mark.parametrize("sip", [False, True])
def test_wcs_to_full_matches_pixel_mapping(factor, sip):
    part = true_wcs(400, 500, sip=sip)
    x0, y0 = 300, 550
    full = pa.wcs_to_full(part, x0, y0, factor)
    rng = np.random.default_rng(0)
    for px, py in rng.uniform(0, 400, (20, 2)):
        ra1, dec1 = part.pixel_to_world_values(px, py)
        fx, fy = x0 + factor * px + (factor - 1) / 2, y0 + factor * py + (factor - 1) / 2
        ra2, dec2 = full.pixel_to_world_values(fx, fy)
        assert ra2 == pytest.approx(ra1, abs=1e-7) and dec2 == pytest.approx(dec1, abs=1e-7)


def test_hints_follow_binning_not_cropping():
    h = SolveHints(focal_length_mm=4.2, pixel_size_um=1.4, source={"scale": "Settings › Equipment"})
    assert pa.scaled_hints(h, 1).scale_arcsec() == pytest.approx(h.scale_arcsec())
    assert pa.scaled_hints(h, 2).scale_arcsec() == pytest.approx(2 * h.scale_arcsec())
    assert pa.scaled_hints(SolveHints(pixel_scale_arcsec=30.0), 2).pixel_scale_arcsec == 60.0


# --------------------------------------------------------------------------- foreground
def test_tree_silhouette_is_masked_and_sky_is_not():
    img = star_field()
    yy, xx = np.mgrid[0:H, 0:W]
    ground = yy > 1150 + 120 * np.sin(xx / 70.0) - 200 * np.exp(-((xx - 400) / 60.0) ** 2)   # hills + a tree
    img[ground] = 0.015
    mask = pa.foreground_mask(img)
    assert mask[ground].mean() > 0.9           # the silhouette is covered
    assert mask[yy < 800].mean() < 0.01        # the sky is left alone


def test_lit_ground_is_masked():
    img = star_field()
    img[1350:] = 0.7                           # bright, starless ground (street lights)
    mask = pa.foreground_mask(img)
    assert mask[1400:].mean() > 0.9 and mask[:1200].mean() < 0.01


def test_star_field_and_milky_way_are_not_masked():
    img = star_field(n=1500)
    assert not pa.foreground_mask(img).any()
    band = star_field(n=4000, seed=3)
    band[:, 400:800] += 0.15                   # a bright band full of stars
    assert not pa.foreground_mask(band).any()


# --------------------------------------------------------------------------- variants
def test_variants_crop_the_centre_and_bin_large_parts(tmp_path):
    big = ImageData(tmp_path / "x.fits", "FITS", star_field(4800, 3600, n=200),
                    hints=SolveHints(pixel_scale_arcsec=20.0))
    base, notes = pa.prepare_base(big)
    v = pa.make_variant(big, base, notes, 0.5)
    assert (v.x0, v.y0, v.factor) == (900, 1200, 2) and v.image.data.shape == (1200, 900)
    assert v.label == "phone aids, middle 50 %, 2×2 binned"
    v = pa.make_variant(big, base, notes, 0.3)
    assert v.factor == 1 and v.image.data.shape == (1440, 1080) and v.image.hints.pixel_scale_arcsec == 20.0
    v = pa.make_variant(big, base, notes, 1.0)
    assert v.factor == 2 and v.image.data.shape == (2400, 1800) and v.image.hints.pixel_scale_arcsec == 40.0
    assert big.data.shape == (4800, 3600)      # the original is untouched


# --------------------------------------------------------------------------- the whole flow
class FakeSolver:
    """Solves only parts small enough (like a solver that fails on distorted edges)."""
    plugin_id, name, uses_pixels = "fake", "FakeSolver", True

    def __init__(self, truth, full_w, full_h, max_width, refine_ok=True):
        self.truth, self.W, self.H, self.max_width, self.refine_ok = truth, full_w, full_h, max_width, refine_ok
        self.calls = []

    def is_available(self):
        return True, ""

    def solve(self, image, ctx):
        self.calls.append((image.width, image.height, image.hints.has_position))
        near = image.hints.has_position and self.refine_ok
        if image.width > self.max_width and not near:
            return SolveResult.failed("No solution found", self.plugin_id, self.name)
        # which part of the whole photo is this? (centred; binned if 2x smaller than the crop)
        for f, frac in [(f, fr) for f in (1, 2) for fr in (1.0, 0.75, 0.6, 0.5, 0.4, 0.3)]:
            cw, ch = max(32, int(round(self.W * frac))), max(32, int(round(self.H * frac)))
            x0, y0 = (self.W - cw) // 2, (self.H - ch) // 2
            if True:
                if (cw // f, ch // f) == (image.width, image.height):
                    w = self.truth.deepcopy()
                    w.wcs.crpix = (np.array(w.wcs.crpix) - 1 - [x0 + (f - 1) / 2, y0 + (f - 1) / 2]) / f + 1
                    w.wcs.cd = np.array(w.wcs.cd) * f
                    w.wcs.set()
                    return SolveResult.from_wcs(w, image.width, image.height, self.plugin_id, self.name)
        raise AssertionError(f"unexpected size {image.width}")


@pytest.fixture
def setup(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    reg = PluginRegistry(store).discover()
    p = Pipeline(reg, store.section(GeneralSettings()))
    p.star_check.set("enabled", False)
    p.solve_prep.set("flatten", "never")
    img = ImageData(tmp_path / "phone.jpg", "JPEG", star_field(), hints=SolveHints(pixel_scale_arcsec=60.0))
    truth = true_wcs()
    return p, img, truth


def test_phone_aids_solve_the_centre_and_map_it_back(setup):
    p, img, truth = setup
    fake = FakeSolver(truth, W, H, max_width=700, refine_ok=False)
    p.solvers = lambda: [fake]
    asked = []
    ctx = TaskContext(confirm=lambda title, text, key="", **kw: asked.append(text) or True)
    res = p.solve(img, ctx)
    assert res.success and asked and "mobile phone" in asked[0]
    assert "phone and wide-field aids" in res.message and "extended to the whole photo" in res.message
    expected = SolveResult.from_wcs(truth, W, H, "t", "t")
    assert res.center_ra_deg == pytest.approx(expected.center_ra_deg, abs=1e-6)
    assert res.pixel_scale_arcsec == pytest.approx(60.0, rel=1e-4)
    for x, y in ((0, 0), (W - 1, H - 1), (100, 1500)):
        assert res.wcs.pixel_to_world_values(x, y) == pytest.approx(truth.pixel_to_world_values(x, y), abs=1e-7)
    assert fake.calls[0] == (W, H, False)                  # the normal attempt
    assert fake.calls[1] == (600, 800, False)              # then the middle 50 %
    assert any("middle 50 %" in a for a in res.attempts)


def test_refine_solves_the_whole_photo_near_the_position(setup):
    p, img, truth = setup
    fake = FakeSolver(truth, W, H, max_width=700, refine_ok=True)
    p.solvers = lambda: [fake]
    res = p.solve(img, TaskContext())
    assert res.success and "whole photo was then solved" in res.message
    assert fake.calls[-1] == (W, H, True)


def test_no_means_no_and_batch_mode_only_with_auto(setup):
    p, img, truth = setup
    fake = FakeSolver(truth, W, H, max_width=700)
    p.solvers = lambda: [fake]
    res = p.solve(img, TaskContext(confirm=lambda *a, **k: False))
    assert not res.success and any("not used" in a for a in res.attempts) and len(fake.calls) == 1
    fake.calls.clear()
    assert not p.solve(img, TaskContext(), interactive=False).success and len(fake.calls) == 1
    p.phone_aids.set("mode", "auto")
    assert p.solve(img, TaskContext(confirm=lambda *a, **k: False), interactive=False).success
    p.phone_aids.set("mode", "never")
    fake.calls.clear()
    assert not p.solve(img, TaskContext()).success and len(fake.calls) == 1


def test_phone_clue():
    img = ImageData(Path("IMG_1.jpg"), "JPEG", np.zeros((10, 10), np.float32), header={"Make": "Apple",
                                                                                     "Camera": "iPhone 15 Pro"})
    assert "Apple iPhone 15 Pro" in pa.phone_clue(img)
    assert "HEIC" in pa.phone_clue(ImageData(Path("a.heic"), "HEIC", np.zeros((10, 10), np.float32)))
    assert pa.phone_clue(ImageData(Path("a.fits"), "FITS", np.zeros((10, 10), np.float32))) == ""


@pytest.mark.skipif(not (SAMPLES / "phone_photo_1.jpg").exists(), reason="sample photos not present")
def test_real_phone_samples(setup):
    p, _, _ = setup
    for name in ("phone_photo_1.jpg", "phone_photo_2.jpg"):
        img = p.load(SAMPLES / name)
        base, notes = pa.prepare_base(img)
        assert "foreground" not in notes            # open sky: nothing to black out
        v = pa.make_variant(img, base, notes, 0.5)
        assert v.image.width < img.width


def test_not_offered_for_telescope_fits(setup, tmp_path):
    p, _, truth = setup
    fake = FakeSolver(truth, W, H, max_width=700)
    p.solvers = lambda: [fake]
    asked = []
    fits = ImageData(tmp_path / "light_001.fits", "FITS", star_field(), hints=SolveHints(pixel_scale_arcsec=1.5))
    assert not p.solve(fits, TaskContext(confirm=lambda *a, **k: asked.append(1) or True)).success
    assert not asked and len(fake.calls) == 1


def test_refine_is_one_quick_try_and_falls_back_to_75_percent(setup):
    p, img, truth = setup

    class EdgeShy(FakeSolver):
        """Solves the centre blind, and near the position only parts up to 75 %."""
        def solve(self, image, ctx):
            self.calls.append((image.width, image.height, image.hints.has_position))
            if image.hints.has_position:
                assert image.hints.position_exact
                if image.width > 0.8 * self.W:
                    return SolveResult.failed("No solution found", self.plugin_id, self.name)
            elif image.width > self.max_width:
                return SolveResult.failed("No solution found", self.plugin_id, self.name)
            self.max_width, saved = 10**6, self.max_width
            try:
                return FakeSolver.solve(self, image, ctx)
            finally:
                self.max_width = saved
                self.calls.pop()

    fake = EdgeShy(truth, W, H, max_width=700)
    p.solvers = lambda: [fake]
    res = p.solve(img, TaskContext())
    assert res.success and "middle 75 % was then solved" in res.message
    assert [c[:2] for c in fake.calls[-2:]] == [(W, H), (900, 1200)]
    assert res.wcs.pixel_to_world_values(5, 5) == pytest.approx(truth.pixel_to_world_values(5, 5), abs=1e-7)


def test_astap_makes_a_single_narrow_try_when_refining(tmp_path):
    from platesolver.plugins.solvers.astap import AstapSolver
    store = SettingsStore(tmp_path / "s.json")
    solver = AstapSolver(store.section(AstapSolver()))
    img = ImageData(tmp_path / "x.jpg", "JPEG", np.zeros((1000, 800), np.float32),
                    hints=SolveHints(ra_deg=300.0, dec_deg=40.0, pixel_scale_arcsec=60.0,
                                     source={"focal_length": "Settings › Equipment"}, position_exact=True))
    assert solver.attempts(img) == [(None, False)]
    exe = tmp_path / "astap_cli.exe"
    exe.write_text("")
    cmd, _ = solver.build_command(exe, tmp_path / "x.fits", img)
    assert float(cmd[cmd.index("-r") + 1]) == pytest.approx(1000 * 60 / 3600 * 0.25, abs=0.1)


def test_no_binning_below_600_pixels(tmp_path):
    small = tmp_path / "tiny.jpg"
    small.write_bytes(b"x" * 1000)                 # a tiny file: counts as strongly compressed
    img = ImageData(small, "JPEG", star_field(2048, 1536, n=100), hints=SolveHints(pixel_scale_arcsec=60.0))
    assert pa.heavily_compressed(img)
    base, notes = pa.prepare_base(img)
    assert pa.make_variant(img, base, notes, 0.5).factor == 1          # 768 px wide: stays as it is
    assert pa.make_variant(img, base, notes, 1.0).factor == 2          # 1536 px wide: binned to 768


def test_each_question_is_asked_once_per_image(setup, tmp_path):
    p, img, truth = setup

    class Online(FakeSolver):
        plugin_id, name = "online", "Online"

        def solve(self, image, ctx):
            if not ctx.confirm("Upload?", "upload it?", "astrometry_upload", yes="Upload", no="Don't"):
                return SolveResult.failed("upload declined", self.plugin_id, self.name)
            return SolveResult.failed("no match", self.plugin_id, self.name)

    online = Online(truth, W, H, max_width=0)
    p.solvers = lambda: [online]
    asked = []
    ctx = TaskContext(confirm=lambda title, text, key="", **kw: asked.append(title) or True)
    assert not p.solve(img, ctx).success
    # the normal try plus the phone aids' two parts all use the online solver: still one upload question
    assert asked.count("Upload?") == 1 and asked.count("Not solved – a phone photo?") == 1
    assert not p.solve(img, ctx).success                     # solving the same image again (F5, hint)
    assert len(asked) == 2
    other = ImageData(tmp_path / "other.jpg", "JPEG", star_field(seed=4), hints=SolveHints(pixel_scale_arcsec=60.0))
    p.solve(other, ctx)                                      # a new image: asked again
    assert asked.count("Upload?") == 2
