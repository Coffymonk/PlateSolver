# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
import os
import stat
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from PIL import Image

from platesolver.core.formatting import format_dec, format_ly, format_ra, parse_sexagesimal
from platesolver.core.general import GeneralSettings
from platesolver.core.interfaces import ImageLoader, OverlayLayer, Solver
from platesolver.core.models import SolveResult
from platesolver.core.pipeline import Pipeline
from platesolver.core.plugin import TaskContext
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import SettingsStore
from platesolver.plugins.loaders.fits_loader import flip_wcs_vertically
from platesolver.plugins.solvers import astap


def make_wcs(ra=83.82, dec=-5.39, scale_arcsec=1.5, w=400, h=300, rot_deg=0.0, mirror=False):
    """WCS in the app's convention (row 0 at top) with north up, east left when rot=0."""
    s = scale_arcsec / 3600.0
    r = np.radians(rot_deg)
    # x right = west (RA decreases), y down = south (Dec decreases)
    cd = np.array([[-s, 0.0], [0.0, -s]])
    if mirror:
        cd[0, 0] = s
    rotm = np.array([[np.cos(r), -np.sin(r)], [np.sin(r), np.cos(r)]])
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crpix = [(w + 1) / 2, (h + 1) / 2]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.cd = rotm @ cd
    wcs.wcs.set()
    return wcs


@pytest.fixture
def pipeline(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    reg = PluginRegistry(store).discover()
    assert not reg.errors, reg.errors
    return Pipeline(reg, store.section(GeneralSettings()))


def test_registry_finds_builtin_plugins(pipeline):
    reg = pipeline.registry
    ids = {p.section_id for p in reg.plugins}
    assert {"loader.fits", "loader.tiff", "loader.jpeg", "solver.astap",
            "solver.header_wcs", "overlay.compass"} <= ids
    assert [s.plugin_id for s in reg.of_kind(Solver)] == ["header_wcs", "astap", "astrometry_net"]


def test_user_plugin_folder(tmp_path):
    plug = tmp_path / "plugins"
    plug.mkdir()
    (plug / "my_overlay.py").write_text(textwrap.dedent("""
        from platesolver.core.interfaces import OverlayLayer
        class Grid(OverlayLayer):
            plugin_id = "grid"; name = "Grid"
            def render(self, painter, image, solution, objects): pass
    """))
    reg = PluginRegistry(SettingsStore(None), extra_dirs=[plug]).discover()
    assert reg.get("overlay.grid") is not None


def test_settings_defaults_and_persistence(tmp_path):
    path = tmp_path / "s.json"
    store = SettingsStore(path)
    gen = store.section(GeneralSettings())
    assert gen.get("ly_decimals") == 1
    gen.set("ly_decimals", 3)
    store.save()
    assert SettingsStore(path).section(GeneralSettings()).get("ly_decimals") == 3


def test_formatting():
    assert format_ly(1344.04, 1) == "1,344.0 ly"
    assert format_ly(2_537_000, 2) == "2.54 million ly"
    assert format_ly(640.5, 0) == "640 ly" or format_ly(640.5, 0) == "641 ly"
    assert format_ra(83.8221) == "05h 35m 17.3s"
    assert format_dec(-5.3911) == "-05° 23′ 28″"
    assert parse_sexagesimal("05 35 17.3") == pytest.approx(5.588139, abs=1e-5)
    assert parse_sexagesimal("-05:23:28") == pytest.approx(-5.391111, abs=1e-5)


def test_solve_result_orientation():
    r = SolveResult.from_wcs(make_wcs(), 400, 300, "t", "t")
    assert r.center_ra_deg == pytest.approx(83.82, abs=1e-6)
    assert r.pixel_scale_arcsec == pytest.approx(1.5, rel=1e-3)
    assert r.rotation_deg == pytest.approx(0.0, abs=0.05) or r.rotation_deg == pytest.approx(360.0, abs=0.05)
    assert not r.mirrored
    assert r.fov_width_deg == pytest.approx(400 * 1.5 / 3600, rel=1e-3)
    r90 = SolveResult.from_wcs(make_wcs(rot_deg=90), 400, 300, "t", "t")
    assert r90.rotation_deg % 180 == pytest.approx(90, abs=0.05)
    assert SolveResult.from_wcs(make_wcs(mirror=True), 400, 300, "t", "t").mirrored


def test_fits_loader_flip_keeps_sky_positions(tmp_path, pipeline):
    w, h = 64, 48
    wcs = make_wcs(w=w, h=h)
    data = np.random.default_rng(1).integers(0, 65535, (h, w), dtype=np.uint16)
    hdr = wcs.to_header()
    hdr["FOCALLEN"] = 530.0
    hdr["XPIXSZ"] = 3.76
    hdr["OBJCTRA"] = "05 35 17"
    hdr["OBJCTDEC"] = "-05 23 28"
    p = tmp_path / "t.fits"
    fits.PrimaryHDU(data, header=hdr).writeto(p)
    img = pipeline.load(p)
    assert img.format == "FITS" and img.is_linear
    # bottom-up by default -> rows reversed
    assert img.data[0, 0] == pytest.approx(data[-1, 0] / 65535, abs=1e-4)
    # a sky position must land on the same pixel VALUE before and after the flip
    x, y = 10.0, 5.0
    ra, dec = wcs.pixel_to_world_values(x, y)
    x2, y2 = img.header_wcs.world_to_pixel_values(ra, dec)
    assert (x2, y2) == pytest.approx((x, h - 1 - y), abs=1e-6)
    assert img.hints.focal_length_mm == 530.0
    assert img.hints.scale_arcsec() == pytest.approx(1.4633, abs=1e-3)
    # header_wcs solver is used first
    res = pipeline.solve(img, TaskContext())
    assert res.success and res.solver_id == "header_wcs"


def test_fits_color_and_bayer(tmp_path, pipeline):
    rgb = np.random.default_rng(2).random((3, 20, 30)).astype(np.float32)
    p = tmp_path / "c.fits"
    fits.PrimaryHDU(rgb).writeto(p)
    img = pipeline.load(p)
    assert img.data.shape == (20, 30, 3)
    raw = np.zeros((20, 30), dtype=np.uint16)
    raw[0::2, 0::2] = 60000  # red sites for RGGB
    hdr = fits.Header()
    hdr["BAYERPAT"] = "RGGB"
    hdr["ROWORDER"] = "TOP-DOWN"
    p2 = tmp_path / "b.fits"
    fits.PrimaryHDU(raw, header=hdr).writeto(p2)
    img2 = pipeline.load(p2)
    assert img2.data.shape == (20, 30, 3)
    assert img2.data[..., 0].mean() > 0.8 and img2.data[..., 2].mean() < 0.05


def test_tiff_and_jpeg(tmp_path, pipeline):
    import tifffile
    arr = (np.random.default_rng(3).random((40, 50, 3)) * 65535).astype(np.uint16)
    tifffile.imwrite(tmp_path / "a.tif", arr)
    img = pipeline.load(tmp_path / "a.tif")
    assert img.data.shape == (40, 50, 3) and img.is_linear and img.bit_depth == "16-bit"
    Image.fromarray((arr // 256).astype(np.uint8)).save(tmp_path / "a.jpg")
    j = pipeline.load(tmp_path / "a.jpg")
    assert j.data.shape == (40, 50, 3) and not j.is_linear


def test_astap_ini_parsing():
    wcs = make_wcs(rot_deg=33)
    ini = "\n".join([
        "PLTSOLVD=T",
        f"CRPIX1={wcs.wcs.crpix[0]}", f"CRPIX2={wcs.wcs.crpix[1]}",
        f"CRVAL1={wcs.wcs.crval[0]}", f"CRVAL2={wcs.wcs.crval[1]}",
        f"CD1_1={wcs.wcs.cd[0,0]}", f"CD1_2={wcs.wcs.cd[0,1]}",
        f"CD2_1={wcs.wcs.cd[1,0]}", f"CD2_2={wcs.wcs.cd[1,1]}",
        "CMDLINE=astap -f x",
    ])
    w2 = astap.wcs_from_ini(astap.parse_ini(ini))
    for px in [(0, 0), (399, 299), (123.4, 56.7)]:
        assert w2.pixel_to_world_values(*px) == pytest.approx(wcs.pixel_to_world_values(*px), abs=1e-9)


def test_astap_database_detection(tmp_path):
    (tmp_path / "astap.exe").write_bytes(b"")
    for n in ("d50_0101.1476", "d50_0102.1476", "g05_0101.290", "readme.txt"):
        (tmp_path / n).write_bytes(b"")
    assert astap.find_databases(tmp_path) == ["d50", "g05"]
    assert astap.resolve_executable(str(tmp_path)) == tmp_path / "astap.exe"
    ok, msg = astap.validate_path(str(tmp_path))
    assert ok and "D50" in msg
    assert not astap.validate_path(str(tmp_path / "nothing"))[0]


@pytest.mark.skipif(sys.platform == "win32", reason="fake ASTAP is a shell script")
def test_astap_end_to_end_with_fake_program(tmp_path, pipeline):
    """A stand-in ASTAP that checks the arguments and writes a known solution."""
    wcs = make_wcs(w=64, h=48, rot_deg=12)
    fake = tmp_path / "astap_cli"
    fake.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys
        from pathlib import Path
        from astropy.io import fits
        a = sys.argv[1:]
        f = Path(a[a.index('-f') + 1])
        assert fits.getdata(f).shape == (48, 64)
        assert '-ra' in a and abs(float(a[a.index('-ra') + 1]) - 83.82 / 15) < 1e-4
        Path(str(f)[:-5] + '.ini').write_text('\\n'.join([
            'PLTSOLVD=T',
            'CRPIX1={wcs.wcs.crpix[0]}', 'CRPIX2={wcs.wcs.crpix[1]}',
            'CRVAL1={wcs.wcs.crval[0]}', 'CRVAL2={wcs.wcs.crval[1]}',
            'CD1_1={wcs.wcs.cd[0,0]}', 'CD1_2={wcs.wcs.cd[0,1]}',
            'CD2_1={wcs.wcs.cd[1,0]}', 'CD2_2={wcs.wcs.cd[1,1]}']))
    """))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    Image.fromarray(np.zeros((48, 64), np.uint8)).save(tmp_path / "x.png")
    img = pipeline.load(tmp_path / "x.png")
    img.hints.ra_deg, img.hints.dec_deg = 83.82, -5.39
    solver = pipeline.registry.get("solver.astap")
    solver.settings.set("path", str(fake))
    res = pipeline.solve(img, TaskContext())
    assert res.success and res.solver_id == "astap", res.attempts
    # make_wcs rotates "up" towards the west, i.e. position angle -12° = 348°
    assert res.rotation_deg == pytest.approx(348, abs=0.05)
    assert res.center_ra_deg == pytest.approx(83.82, abs=1e-6)


def test_solve_fails_cleanly_without_astap(tmp_path, pipeline):
    Image.fromarray(np.zeros((10, 10), np.uint8)).save(tmp_path / "x.png")
    img = pipeline.load(tmp_path / "x.png")
    pipeline.registry.get("solver.astap").settings.set("path", str(tmp_path / "missing"))
    res = pipeline.solve(img, TaskContext())
    assert not res.success
    assert any("ASTAP" in a and "skipped" in a for a in res.attempts)


def test_app_logging_setup_does_not_break_astropy(tmp_path, monkeypatch):
    """Regression: setting up logging before astropy was imported broke 'import astropy'."""
    import subprocess
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path))
    code = ("import sys; [sys.modules.pop(m) for m in list(sys.modules) if m.startswith('astropy')];"
            "from platesolver.app import setup_logging; setup_logging();"
            "from astropy.io import fits; from astropy.wcs import WCS; print('ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(Path(__file__).resolve().parents[1]))
    assert out.stdout.strip() == "ok", out.stderr


def test_scale_check_explains_mismatch(tmp_path):
    from platesolver.core.models import ImageData, SolveHints
    from platesolver.core.pipeline import scale_check
    res = SolveResult.from_wcs(make_wcs(scale_arcsec=1.709), 400, 300, "astap", "ASTAP")
    img = ImageData(tmp_path / "x.fits", "FITS", np.zeros((300, 400), np.float32))
    # no hints -> advice to set defaults
    assert "Settings › Equipment" in scale_check(img, res)
    # correct hints (RedCat 71 + ASI585: 350 mm, 2.9 µm) -> no note
    img.hints = SolveHints(focal_length_mm=350, pixel_size_um=2.9, source={"focal_length": "FOCALLEN", "pixel_size": "XPIXSZ"})
    assert scale_check(img, res) == ""
    # header pixel size ignores 2x2 binning
    img.hints = SolveHints(focal_length_mm=350, pixel_size_um=1.45, source={"focal_length": "FOCALLEN", "pixel_size": "XPIXSZ"})
    assert "binned 2×2" in scale_check(img, res)
    # plain wrong focal length
    img.hints = SolveHints(focal_length_mm=250, pixel_size_um=2.9, source={"focal_length": "FOCALLEN", "pixel_size": "XPIXSZ"})
    note = scale_check(img, res)
    assert "Expected 2.39″/px" in note and "capture software" in note



def test_equipment_settings_fill_missing_hints(tmp_path):
    from platesolver.core.equipment import EquipmentSettings, image_scale
    store = SettingsStore(tmp_path / "s.json")
    # values saved by the previous version on the General page are carried over
    store.set("general", "default_focal_length", 350.0)
    store.set("general", "default_pixel_size", 2.9)
    reg = PluginRegistry(store).discover()
    p = Pipeline(reg, store.section(GeneralSettings()))
    assert p.equipment.get("focal_length") == 350.0 and p.equipment.get("pixel_size") == 2.9
    assert image_scale(350, 2.9) == pytest.approx(1.709, abs=1e-3)
    assert "1.71″" in EquipmentSettings().describe({"focal_length": 350, "pixel_size": 2.9})
    assert "Enter both" in EquipmentSettings().describe({"focal_length": 350, "pixel_size": 0})

    # a JPG knows nothing -> Equipment values are used, and reach ASTAP as the field height
    Image.fromarray(np.zeros((2160, 3840), np.uint8)).save(tmp_path / "x.jpg")
    img = p.load(tmp_path / "x.jpg")
    assert img.hints.focal_length_mm == 350.0 and img.hints.pixel_size_um == 2.9
    assert img.hints.source["focal_length"] == "Settings › Equipment"
    cmd, _ = reg.get("solver.astap").build_command(Path("astap"), Path("x.fits"), img)
    assert float(cmd[cmd.index("-fov") + 1]) == pytest.approx(2160 * 1.709 / 3600, abs=1e-3)

    # a FITS with its own values keeps them ...
    hdr = fits.Header()
    hdr["FOCALLEN"] = 250.0
    hdr["XPIXSZ"] = 3.76
    fits.PrimaryHDU(np.zeros((20, 30), np.uint16), header=hdr).writeto(tmp_path / "f.fits")
    assert p.load(tmp_path / "f.fits").hints.focal_length_mm == 250.0
    # ... unless "always use these values" is on
    p.equipment.set("override_file", True)
    f = p.load(tmp_path / "f.fits")
    assert (f.hints.focal_length_mm, f.hints.pixel_size_um) == (350.0, 2.9)


@pytest.mark.skipif(sys.platform == "win32", reason="fake ASTAP is a shell script")
def test_astap_retries_with_any_scale_when_equipment_guess_is_wrong(tmp_path):
    """A drizzled/resized JPG: the Equipment scale is wrong, ASTAP's automatic scale mode finds it."""
    store = SettingsStore(tmp_path / "s.json")
    reg = PluginRegistry(store).discover()
    p = Pipeline(reg, store.section(GeneralSettings()))
    p.equipment.set("focal_length", 350.0)
    p.equipment.set("pixel_size", 2.9)
    wcs = make_wcs(w=64, h=48, scale_arcsec=0.85)
    log = tmp_path / "calls.txt"
    fake = tmp_path / "astap_cli"
    fake.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys
        from pathlib import Path
        a = sys.argv[1:]
        fov = float(a[a.index('-fov') + 1])
        with open({str(log)!r}, 'a') as fh:
            fh.write(f'{{fov}}\\n')
        f = Path(a[a.index('-f') + 1])
        ini = Path(str(f)[:-5] + '.ini')
        if fov != 0:
            ini.write_text('PLTSOLVD=F\\nERROR=No solution found!')
        else:
            ini.write_text('\\n'.join([
                'PLTSOLVD=T',
                'CRPIX1={wcs.wcs.crpix[0]}', 'CRPIX2={wcs.wcs.crpix[1]}',
                'CRVAL1={wcs.wcs.crval[0]}', 'CRVAL2={wcs.wcs.crval[1]}',
                'CD1_1={wcs.wcs.cd[0,0]}', 'CD1_2={wcs.wcs.cd[0,1]}',
                'CD2_1={wcs.wcs.cd[1,0]}', 'CD2_2={wcs.wcs.cd[1,1]}']))
    """))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    Image.fromarray(np.zeros((48, 64), np.uint8)).save(tmp_path / "x.jpg")
    solver = reg.get("solver.astap")
    solver.settings.set("path", str(fake))
    p.star_check.set("enabled", False)
    img = p.load(tmp_path / "x.jpg")
    messages = []
    res = p.solve(img, TaskContext(log=messages.append))
    assert res.success, res.attempts
    fovs = [float(x) for x in log.read_text().split()]
    assert fovs[0] == pytest.approx(48 * 1.709 / 3600, abs=1e-3) and fovs[-1] == 0
    assert any("any image scale" in m for m in messages)
    assert "0.85" in (res.scale_note or "") or res.pixel_scale_arcsec == pytest.approx(0.85, abs=0.01)

    # a scale from the image itself (or the Field of view setting) is trusted: no second try
    log.unlink()
    solver.settings.set("fov_override", 0.5)
    p.solve(p.load(tmp_path / "x.jpg"), TaskContext())
    assert set(float(x) for x in log.read_text().split()) == {0.5}


def test_selftest_passes(tmp_path, monkeypatch):
    """The check every packaged build runs (PlateSolver --selftest report.txt)."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from platesolver.app import selftest
    report = tmp_path / "selftest.txt"
    assert selftest(str(report)) == 0
    text = report.read_text()
    assert "RESULT: all checks passed" in text and ".xisf" in text
