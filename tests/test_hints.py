# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Object hints: file-name guesses, name/coordinate lookup, and how solvers use the hint."""
import stat
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest
from astropy.wcs import WCS
from PIL import Image

from platesolver.core import objecthint
from platesolver.core.general import GeneralSettings
from platesolver.core.objecthint import guess_from_filename, parse_coordinates, resolve
from platesolver.core.pipeline import Pipeline
from platesolver.core.plugin import TaskContext
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import SettingsStore


def make_pipeline(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    reg = PluginRegistry(store).discover()
    p = Pipeline(reg, store.section(GeneralSettings()))
    p.equipment.set("focal_length", 350.0)
    p.equipment.set("pixel_size", 2.9)
    p.star_check.set("enabled", False)
    return p, reg


@pytest.mark.parametrize("name, expected", [
    ("M101_JPEG.jpg", "M 101"), ("NGC7380_FINAL_WITH_STARS_ROTATED_PS.jpg", "NGC 7380"),
    ("m42-orion.tif", "M 42"), ("2026-05-M31.jpg", "M 31"), ("NGC_891.fits", "NGC 891"),
    ("Sh2-155_cave.jpg", "Sh2-155"), ("IC1396 elephant.jpg", "IC 1396"), ("LDN1235.jpg", "LDN 1235"),
    ("IMG_0001.jpg", None), ("sky.jpg", None), ("Moon_2026.jpg", None), ("M1000.jpg", None),
])
def test_guess_from_filename(name, expected):
    assert guess_from_filename(name) == expected


def test_parse_coordinates():
    for text in ("210.80225 54.348944", "14 03 12.54 +54 20 56.2", "14:03:12.54 +54:20:56.2",
                 "14h03m12.54s +54d20m56.2s", "14h03m12.54s, +54°20′56.2″"):
        ra, dec = parse_coordinates(text)
        assert ra == pytest.approx(210.80225, abs=1e-3) and dec == pytest.approx(54.34894, abs=1e-3), text
    ra, dec = parse_coordinates("05 35 17 -05 23 28")
    assert ra == pytest.approx(83.82, abs=0.01) and dec == pytest.approx(-5.391, abs=0.01)
    assert parse_coordinates("M101") is None and parse_coordinates("400 10") is None


def test_resolve_offline_names():
    h = resolve("m101", use_simbad=False)
    assert h.label.startswith("M 101") and h.ra_deg == pytest.approx(210.80, abs=0.01)
    assert resolve("NGC 7380", use_simbad=False).dec_deg == pytest.approx(58.13, abs=0.02)
    assert resolve("Pleiades", use_simbad=False).label.startswith("M 45")
    with pytest.raises(LookupError, match="turned off"):
        resolve("Wizard Nebula", use_simbad=False)


def test_resolve_falls_back_to_simbad(monkeypatch):
    from platesolver.services import simbad
    seen = []

    def fake_query(adql, timeout=90, use_cache=True):
        seen.append(adql)
        return [{"ra": "336.8", "dec": "58.1", "main_id": "NGC  7380"}] if "Wizard Nebula" in adql else []
    monkeypatch.setattr(simbad, "query", fake_query)
    h = resolve("Wizard Nebula")
    assert h.found_in == "SIMBAD" and h.ra_deg == pytest.approx(336.8)
    assert "'NAME Wizard Nebula'" in seen[0]
    with pytest.raises(LookupError, match="not found"):
        resolve("Nothing Like This Nebula")


def test_astap_search_order(tmp_path):
    p, reg = make_pipeline(tmp_path)
    Image.fromarray(np.zeros((300, 400), np.uint8)).save(tmp_path / "M101_crop.jpg")
    astap = reg.get("solver.astap")
    img = p.load(tmp_path / "M101_crop.jpg")
    # no position, scale from Equipment: blind, then blind with any scale
    assert astap.attempts(img) == [(None, True), (0.0, True)]
    p.hint_from_filename(img, TaskContext())
    assert img.hints.position_hint.startswith("M 101")
    # with a hint: near it, near it with any scale, then blind (in case the hint is wrong)
    assert astap.attempts(img) == [(None, False), (0.0, False), (None, True)]
    cmd, mode = astap.build_command(Path("astap"), Path("x.fits"), img, 0.0, False)
    assert "-ra" in cmd and cmd[cmd.index("-fov") + 1] == "0.0000" and "near M 101" in mode
    cmd, mode = astap.build_command(Path("astap"), Path("x.fits"), img, None, True)
    assert "-ra" not in cmd and "in case the object hint is wrong" in mode


def test_hint_from_filename_respects_setting_and_file_position(tmp_path):
    p, _ = make_pipeline(tmp_path)
    Image.fromarray(np.zeros((30, 40), np.uint8)).save(tmp_path / "M101.jpg")
    p.hint_settings.set("from_filename", False)
    img = p.load(tmp_path / "M101.jpg")
    p.hint_from_filename(img, TaskContext())
    assert not img.hints.has_position
    p.hint_settings.set("from_filename", True)
    img = p.load(tmp_path / "M101.jpg")
    img.hints.ra_deg, img.hints.dec_deg = 10.0, 20.0      # position from the file itself wins
    p.hint_from_filename(img, TaskContext())
    assert img.hints.ra_deg == 10.0 and not img.hints.position_hint


@pytest.mark.skipif(sys.platform == "win32", reason="fake ASTAP is a shell script")
def test_cropped_resized_jpg_solves_near_hint_with_any_scale(tmp_path):
    """The real solution is near M 101 at 0.85″/px; Equipment says 1.71″/px. Only '-ra … -fov 0' works."""
    p, reg = make_pipeline(tmp_path)
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crpix, w.wcs.crval = [32.5, 24.5], [210.9, 54.4]
    w.wcs.cd = [[-0.85 / 3600, 0], [0, -0.85 / 3600]]
    calls = tmp_path / "calls.txt"
    fake = tmp_path / "astap_cli"
    fake.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys
        from pathlib import Path
        a = sys.argv[1:]
        near = '-ra' in a
        fov = float(a[a.index('-fov') + 1])
        open({str(calls)!r}, 'a').write(f'{{near}} {{fov}}\\n')
        ini = Path(a[a.index('-f') + 1][:-5] + '.ini')
        if near and fov == 0 and abs(float(a[a.index('-ra') + 1]) * 15 - 210.8) < 0.5:
            ini.write_text('\\n'.join(['PLTSOLVD=T', 'CRPIX1=32.5', 'CRPIX2=24.5', 'CRVAL1=210.9',
                                       'CRVAL2=54.4', 'CD1_1={-0.85 / 3600}', 'CD1_2=0', 'CD2_1=0',
                                       'CD2_2={-0.85 / 3600}']))
        else:
            ini.write_text('PLTSOLVD=F')
    """))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    reg.get("solver.astap").settings.set("path", str(fake))
    Image.fromarray(np.zeros((48, 64), np.uint8)).save(tmp_path / "M101_cropped_PS.jpg")
    img = p.load(tmp_path / "M101_cropped_PS.jpg")
    log = []
    res = p.solve(img, TaskContext(log=log.append))
    assert res.success, res.attempts
    assert res.pixel_scale_arcsec == pytest.approx(0.85, abs=0.01)
    assert calls.read_text().split("\n")[:2] == [f"True {48 * 1.709 / 3600:.4f}".rstrip("0"), "True 0.0"] or \
        [l.split()[0] for l in calls.read_text().split("\n")[:2]] == ["True", "True"]
    assert any("Object hint: M 101" in m for m in log)


def test_astrometry_net_gets_wide_scale_range_for_guessed_scale(tmp_path):
    p, reg = make_pipeline(tmp_path)
    Image.fromarray(np.zeros((48, 64), np.uint8)).save(tmp_path / "x.jpg")
    img = p.load(tmp_path / "x.jpg")
    an = reg.get("solver.astrometry_net")
    params = an.upload_params("s", img)
    assert params["scale_type"] == "ul"
    assert params["scale_lower"] == pytest.approx(1.709 / 4, abs=1e-3)
    assert params["scale_upper"] == pytest.approx(1.709 * 4, abs=1e-3)
    p.set_object_hint(img, "M 101")
    params = an.upload_params("s", img)
    assert params["center_ra"] == pytest.approx(210.802, abs=1e-3)


def test_drizzle_factor():
    from platesolver.core.equipment import drizzle_factor
    assert drizzle_factor(7664, 4294, 3840, 2160) == 2          # 2x drizzle, slightly cropped
    assert drizzle_factor(6000, 3400, 3840, 2160) == 2          # 2x drizzle, cropped more
    assert drizzle_factor(5760, 3240, 3840, 2160) == 1.5
    assert drizzle_factor(11520, 6480, 3840, 2160) == 3
    assert drizzle_factor(3832, 2147, 3840, 2160) == 1          # plain (cropped) stack
    assert drizzle_factor(2147, 3832, 3840, 2160) == 1          # rotated to portrait
    assert drizzle_factor(7664, 4294, 0, 0) == 1                # sensor size unknown


def test_drizzled_image_gets_half_the_scale(tmp_path):
    p, _ = make_pipeline(tmp_path)
    p.equipment.set("focal_length", 349.0)
    p.equipment.set("sensor_width", 3840)
    p.equipment.set("sensor_height", 2160)
    Image.fromarray(np.zeros((4294, 7664), np.uint8)).save(tmp_path / "crescent_final2_PS.jpg")
    img = p.load(tmp_path / "crescent_final2_PS.jpg")
    assert img.hints.scale_arcsec() == pytest.approx(1.714 / 2, abs=0.002)
    assert "drizzled 2×" in img.hints.source["pixel_size"] and any("2× drizzled" in n for n in img.notes)
    p.equipment.set("detect_drizzle", False)
    assert p.load(tmp_path / "crescent_final2_PS.jpg").hints.scale_arcsec() == pytest.approx(1.714, abs=0.002)


@pytest.mark.parametrize("name, expected", [
    ("crescent_final2_PS.jpg", "Crescent Nebula"), ("North_America_ha.jpg", "North America Nebula"),
    ("Andromeda.jpg", "Andromeda Galaxy"), ("whirlpool.jpg", "Whirlpool Galaxy"),
    ("final_stars_rotated_PS.jpg", None), ("light_frame.fits", None), ("test_image.jpg", None),
])
def test_common_names_in_file_names(name, expected):
    assert guess_from_filename(name) == expected
    if expected:
        assert resolve(expected, use_simbad=False)


def test_solver_fits_has_no_scale_keywords(tmp_path):
    """ASTAP reads FOCALLEN/XPIXSZ in '-fov 0' mode, which would stop it trying other scales."""
    from astropy.io import fits
    from platesolver.plugins.solvers.astap import write_solver_fits
    p, _ = make_pipeline(tmp_path)
    Image.fromarray(np.zeros((30, 40), np.uint8)).save(tmp_path / "x.jpg")
    img = p.load(tmp_path / "x.jpg")
    assert img.hints.focal_length_mm == 350.0
    write_solver_fits(img, tmp_path / "x.fits")
    hdr = fits.getheader(tmp_path / "x.fits")
    assert "FOCALLEN" not in hdr and "XPIXSZ" not in hdr


def test_narrow_field_database_is_named_explicitly(tmp_path):
    """ASTAP must not fall back on a database it remembers (e.g. W08 after an iPhone session)."""
    p, reg = make_pipeline(tmp_path)
    folder = tmp_path / "astap"
    folder.mkdir()
    (folder / "astap.exe").write_bytes(b"")
    for n in ("d50_0101.1476", "w08_0101.001"):
        (folder / n).write_bytes(b"")
    astap = reg.get("solver.astap")
    astap.settings.set("path", str(folder))
    Image.fromarray(np.zeros((300, 400), np.uint8)).save(tmp_path / "x.jpg")
    img = p.load(tmp_path / "x.jpg")
    cmd, mode = astap.build_command(Path("astap"), Path("x.fits"), img)
    assert cmd[cmd.index("-D") + 1] == "d50" and "D50 star database (chosen automatically)" in mode
