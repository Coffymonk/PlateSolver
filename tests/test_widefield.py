# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Wide-field (camera lens / Milky Way) images: EXIF scale, no mixing of equipment, ASTAP W08."""
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from platesolver.core.general import GeneralSettings
from platesolver.core.pipeline import Pipeline
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import SettingsStore
from platesolver.plugins.loaders.jpeg_loader import camera_hints


def make_pipeline(tmp_path, fl=350.0, px=2.9):
    store = SettingsStore(tmp_path / "s.json")
    reg = PluginRegistry(store).discover()
    p = Pipeline(reg, store.section(GeneralSettings()))
    p.equipment.set("focal_length", fl)
    p.equipment.set("pixel_size", px)
    return p, reg


def save_jpg_with_exif(path, w=600, h=400, **tags):
    exif = Image.Exif()
    ifd = exif.get_ifd(0x8769)
    for tag, val in tags.items():
        ifd[int(tag, 16)] = val
    Image.fromarray(np.zeros((h, w), np.uint8)).save(path, exif=exif)


def test_camera_hints_from_35mm_equivalent():
    # 3:2 full frame, 24 mm: 36 mm over 6000 px = 6 µm pixels
    h = camera_hints({0x920A: 16.0, 0xA405: 24}, 6000, 4000)
    assert h.focal_length_mm == 16.0
    assert h.pixel_scale_arcsec == pytest.approx(206.265 * 6.0 / 24, rel=2e-3)
    assert "24 mm" in h.source["scale"]
    # iPhone main camera: 4:3, 26 mm equivalent -> about 69° x 54° diagonal 84°
    h = camera_hints({0x920A: 6.86, 0xA405: 26}, 4032, 3024)
    diag = 2 * math.degrees(math.atan(h.pixel_scale_arcsec / 206264.8 * 5040 / 2))
    assert diag == pytest.approx(2 * math.degrees(math.atan(43.27 / 52)), rel=1e-3)


def test_camera_hints_from_sensor_resolution_and_resize():
    # 24 mm lens, 4.0 µm pixels (250 px/mm, unit 4 = mm), picture shrunk from 6000 to 3000 px wide
    h = camera_hints({0x920A: 24.0, 0xA20E: 250.0, 0xA210: 4, 0xA002: 6000, 0xA003: 4000}, 3000, 2000)
    assert h.pixel_size_um == pytest.approx(8.0)
    assert h.scale_arcsec() == pytest.approx(206.265 * 8.0 / 24.0, rel=1e-3)


def test_jpeg_exif_scale_is_not_mixed_with_telescope(tmp_path):
    p, _ = make_pipeline(tmp_path)
    save_jpg_with_exif(tmp_path / "mw.jpg", **{"0x920A": 24.0, "0xA405": 36})
    img = p.load(tmp_path / "mw.jpg")
    assert img.hints.focal_length_mm == 24.0
    assert img.hints.pixel_size_um is None                    # not the ASI camera's 2.9 µm
    assert img.hints.scale_arcsec() > 100                      # tens of degrees across, not 0.2°
    assert img.hints.source["scale"].startswith("EXIF")


def test_focal_length_only_gets_pixel_size_when_it_matches(tmp_path):
    p, _ = make_pipeline(tmp_path)
    save_jpg_with_exif(tmp_path / "lens.jpg", **{"0x920A": 24.0})
    img = p.load(tmp_path / "lens.jpg")
    assert img.hints.pixel_size_um is None
    assert any("differs from your telescope" in n for n in img.notes)

    save_jpg_with_exif(tmp_path / "scope.jpg", **{"0x920A": 352.0})
    img = p.load(tmp_path / "scope.jpg")
    assert img.hints.pixel_size_um == 2.9                      # within 5 %: same telescope


def test_astap_picks_wide_database_automatically(tmp_path):
    p, reg = make_pipeline(tmp_path)
    folder = tmp_path / "astap"
    folder.mkdir()
    (folder / "astap.exe").write_bytes(b"")
    (folder / "astap_cli.exe").write_bytes(b"")
    for n in ("d50_0101.1476", "d50_0102.1476"):
        (folder / n).write_bytes(b"")
    solver = reg.get("solver.astap")
    solver.settings.set("path", str(folder))

    assert solver.installed_databases() == ["d50"]
    assert solver.auto_database(40) == ""
    assert "W08" in solver.wide_field_advice(40)
    assert solver.wide_field_advice(2) == ""

    (folder / "w08_0101.001").write_bytes(b"")
    assert solver.auto_database(40) == "w08"
    assert solver.auto_database(2) == "d50"         # telescope field: named explicitly
    assert solver.wide_field_advice(40) == ""

    save_jpg_with_exif(tmp_path / "mw.jpg", **{"0x920A": 24.0, "0xA405": 36})
    img = p.load(tmp_path / "mw.jpg")
    cmd, mode = solver.build_command(Path("astap"), Path("x.fits"), img)
    assert cmd[cmd.index("-D") + 1] == "w08"
    assert float(cmd[cmd.index("-fov") + 1]) > 20

    # an explicit choice in Settings always wins
    solver.settings.set("database", "d50")
    cmd, _ = solver.build_command(Path("astap"), Path("x.fits"), img)
    assert cmd[cmd.index("-D") + 1] == "d50"


def test_tiff_keeps_camera_exif(tmp_path):
    p, _ = make_pipeline(tmp_path)
    from PIL import TiffImagePlugin
    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[0x920A], info[0xA405] = 6.86, 26
    Image.fromarray(np.zeros((300, 400, 3), np.uint8)).save(tmp_path / "phone.tif", tiffinfo=info)
    img = p.load(tmp_path / "phone.tif")
    assert img.hints.source["scale"].startswith("EXIF")
    assert img.hints.fov_height_deg(img.height) > 40


def test_log_says_where_field_size_came_from_and_spots_wrong_database(tmp_path):
    p, reg = make_pipeline(tmp_path)
    Image.fromarray(np.zeros((3024, 4032), np.uint8)).save(tmp_path / "noexif.jpg")
    img = p.load(tmp_path / "noexif.jpg")
    solver = reg.get("solver.astap")
    _, mode = solver.build_command(Path("astap"), Path("x.fits"), img)
    assert "from Settings › Equipment: 350 mm, 2.9 µm" in mode
    fov = img.hints.fov_height_deg(img.height)
    msg = solver.database_mismatch("w08", fov, img)
    assert "Field of view height" in msg and "Settings › Equipment" in msg
    assert "W08" in solver.database_mismatch("d50", 40, img)
    assert solver.database_mismatch("w08", 50, img) == ""

    solver.settings.set("fov_override", 52.0)
    _, mode = solver.build_command(Path("astap"), Path("x.fits"), img)
    assert "field height 52.000° (from Field of view height setting)" in mode


def test_heic_iphone_photo(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    p, _ = make_pipeline(tmp_path)
    exif = Image.Exif()
    exif[0x0110] = "iPhone 15 Pro"
    ifd = exif.get_ifd(0x8769)
    ifd[0x920A], ifd[0xA405] = 6.86, 24
    rng = np.random.default_rng(1)
    Image.fromarray(rng.integers(0, 40, (300, 400, 3), dtype=np.uint8)).save(tmp_path / "IMG_0001.HEIC", exif=exif)
    assert any(e == ".heic" for l in p.loaders() for e in l.extensions)
    img = p.load(tmp_path / "IMG_0001.HEIC")
    assert img.format == "HEIC" and img.header.get("Camera") == "iPhone 15 Pro"
    assert (img.width, img.height) == (400, 300)
    assert img.hints.source["scale"].startswith("EXIF (24 mm")
    assert img.hints.pixel_size_um is None             # telescope camera not mixed in
