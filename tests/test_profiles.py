# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Equipment profiles: templates, own profiles, automatic choice, phone photos without EXIF."""
import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from platesolver.core import cameras
from platesolver.core.general import GeneralSettings
from platesolver.core.pipeline import Pipeline
from platesolver.core.plugin import TaskContext
from platesolver.core.profiles import TEMPLATES, ProfileManager
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import SettingsStore


def setup(tmp_path, equipment=True):
    store = SettingsStore(tmp_path / "s.json")
    if equipment:
        for k, v in (("focal_length", 349.0), ("pixel_size", 2.9), ("sensor_width", 3840), ("sensor_height", 2160)):
            store.set("equipment", k, v)
    reg = PluginRegistry(store).discover()
    p = Pipeline(reg, store.section(GeneralSettings()))
    p.star_check.set("enabled", False)
    return p, store


def test_camera_catalogue_groups_brands_under_astro_cameras():
    groups = dict(cameras.grouped())
    makers = [m for m, _ in groups[cameras.ASTRO_GROUP]]
    for brand in ("ZWO", "ToupTek", "Player One", "QHYCCD", "Svbony", "Altair", "Atik", "Moravian"):
        assert brand in makers
    asi585 = cameras.get("zwo-asi585mc-mm-pro")
    assert (asi585.pixel_um, asi585.width, asi585.height, asi585.sensor) == (2.9, 3840, 2160, "IMX585")
    uranus = next(c for c in cameras.all_cameras() if c.maker == "Player One" and "Uranus" in c.model)
    assert uranus.sensor == "IMX585"          # same sensor, same numbers
    assert cameras.match_instrument("ZWO ASI585MC Pro").id == asi585.id
    assert cameras.match_instrument("QHY268C").sensor == "IMX571"
    assert cameras.match_instrument("Canon EOS 6D") is None


def test_existing_equipment_becomes_first_profile(tmp_path):
    p, store = setup(tmp_path)
    users = p.profiles.user_profiles()
    assert len(users) == 1 and users[0].name == "My telescope (349 mm, 2.9 µm)"
    assert p.profiles.active_id == p.profiles.chosen_id == users[0].id
    assert users[0].values["equipment"]["sensor_width"] == 3840
    p2, _ = setup(tmp_path / "empty", equipment=False)
    assert p2.profiles.user_profiles() == []


def test_own_profile_from_camera_catalogue(tmp_path):
    p, store = setup(tmp_path, equipment=False)
    mgr = p.profiles
    prof = mgr.save(mgr.new_profile("Redcat 71 + ASI585MC Pro", "astro", "zwo-asi585mc-mm-pro", 349.0))
    assert "1.71″/px" in prof.summary()
    mgr.choose(prof)
    assert (p.equipment.get("focal_length"), p.equipment.get("pixel_size"),
            p.equipment.get("sensor_width")) == (349.0, 2.9, 3840)
    # same name twice gets a number
    assert mgr.save(mgr.new_profile("Redcat 71 + ASI585MC Pro", "astro")).name.endswith("(2)")
    with pytest.raises(ValueError):
        mgr.save(TEMPLATES[0])


def test_phone_photo_without_exif_uses_profile_lens(tmp_path):
    """A phone photo of 3024 x 4032 whose EXIF was stripped (e.g. by Picasa) -> with the phone profile ~76 deg high."""
    p, _ = setup(tmp_path)
    p.profiles.choose(next(t for t in TEMPLATES if t.id == "tpl-phone"))
    Image.fromarray(np.zeros((4032, 3024), np.uint8)).save(tmp_path / "phone_photo.jpg")
    img = p.load(tmp_path / "phone_photo.jpg")
    assert img.hints.focal_length_mm is None
    assert img.hints.fov_height_deg(img.height) == pytest.approx(76.3, abs=0.5)
    assert "26 mm full-frame equivalent" in img.hints.source["scale"]
    astap = p.registry.get("solver.astap")
    assert astap.scale_is_a_guess(img)


def test_automatic_profile_choice_and_return(tmp_path):
    p, store = setup(tmp_path)
    mine = p.profiles.active()
    # an iPhone photo switches to the smartphone template ...
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 13"
    Image.fromarray(np.zeros((300, 400), np.uint8)).save(tmp_path / "phone.jpg", exif=exif)
    img = p.load(tmp_path / "phone.jpg")
    assert p.profiles.active().kind == "phone" and "Apple iPhone 13" in img.profile_note
    assert p.equipment.get("focal_length") == 0
    # ... and a JPG without any camera information goes back to the chosen profile
    Image.fromarray(np.zeros((300, 400), np.uint8)).save(tmp_path / "M101_PS.jpg")
    img = p.load(tmp_path / "M101_PS.jpg")
    assert p.profiles.active_id == mine.id and "your chosen profile" in img.profile_note
    assert img.hints.focal_length_mm == 349.0
    # a FITS from a ZWO camera picks the profile with that camera
    redcat = p.profiles.save(p.profiles.new_profile("Redcat", "astro", "zwo-asi585mc-mm-pro", 349.0))
    hdr = fits.Header()
    hdr["INSTRUME"] = "ZWO ASI585MC Pro"
    fits.PrimaryHDU(np.zeros((20, 30), np.uint16), header=hdr).writeto(tmp_path / "light.fits")
    p.load(tmp_path / "light.fits")
    assert p.profiles.active_id == redcat.id
    # turned off: nothing changes
    store.set("profiles", "auto_switch", False)
    p.load(tmp_path / "phone.jpg")
    assert p.profiles.active_id == redcat.id


def test_deleting_the_active_profile(tmp_path):
    p, _ = setup(tmp_path)
    pid = p.profiles.active_id
    p.profiles.delete(pid)
    assert p.profiles.active_id == "" and p.profiles.user_profiles() == []


def test_profiles_dialog(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from platesolver.ui.profiles_dialog import ProfilesDialog
    p, _ = setup(tmp_path, equipment=False)
    d = ProfilesDialog(p.profiles)
    rows = [d.list.item(i).text() for i in range(d.list.count())]
    assert "My profiles" in rows and "Templates" in rows
    tpl_row = next(i for i in range(d.list.count()) if "Astro camera" in d.list.item(i).text())
    d.list.setCurrentRow(tpl_row)
    assert d.current.builtin and d.btn_copy.isVisibleTo(d)
    d._copy_template()
    assert not d.current.builtin
    d.name.setText("Redcat 71 + ASI585MC Pro")
    d.focal.setValue(349)
    d.camera.setCurrentIndex(d.camera.findData("zwo-asi585mc-mm-pro"))
    assert d.pixel.value() == pytest.approx(2.9) and d.sw.value() == 3840
    d._use()
    assert p.profiles.chosen_id == p.profiles.active_id
    act = p.profiles.active()
    assert act.name == "Redcat 71 + ASI585MC Pro" and act.camera == "zwo-asi585mc-mm-pro"
    assert p.equipment.get("pixel_size") == pytest.approx(2.9)
    d.close()
    app.processEvents()


def test_checklist_reflects_installed_databases(tmp_path):
    p, store = setup(tmp_path)
    folder = tmp_path / "astap"
    folder.mkdir()
    (folder / "astap.exe").write_bytes(b"")
    (folder / "d50_0101.1476").write_bytes(b"")
    p.registry.get("solver.astap").settings.set("path", str(folder))
    mine = p.profiles.active()
    assert p.profiles.missing(mine) == []                       # 349 mm, 2.9 µm, D50 installed
    phone = next(t for t in TEMPLATES if t.id == "tpl-phone")
    assert any("W08" in m for m in p.profiles.missing(phone))
    (folder / "w08_0101.001").write_bytes(b"")
    assert p.profiles.missing(phone) == []
    uw = next(t for t in TEMPLATES if t.id == "tpl-phone-uw")
    assert any("astrometry.net" in m for m in p.profiles.missing(uw))
    astro_tpl = TEMPLATES[0]
    texts = " ".join(p.profiles.missing(astro_tpl))
    assert "focal length" in texts and "Pixel size" in texts


# ---------------------------------------------------------------- photos of a screen or print
def _phone_jpg(path, size=(300, 400)):
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 13"
    exif[0x8769] = {0x920A: 5.7, 0xA405: 26}      # focal length and 35 mm equivalent
    Image.fromarray(np.zeros(size, np.uint8)).save(path, exif=exif)


def test_screen_template_ignores_the_phone_lens(tmp_path):
    p, _ = setup(tmp_path)
    screen = next(t for t in TEMPLATES if t.id == "tpl-screen")
    assert screen.kind == "screen" and "found while solving" in screen.summary()
    p.profiles.choose(screen)
    _phone_jpg(tmp_path / "ipad.jpg")
    img = p.load(tmp_path / "ipad.jpg")
    # the phone's EXIF does not switch to the smartphone template ...
    assert p.profiles.active_id == "tpl-screen" and p.screen_photo()
    # ... and its lens says nothing about the stars on the screen
    assert img.hints.scale_arcsec() is None and img.hints.focal_length_mm is None
    assert any("Photo of a screen" in n for n in img.notes)
    # distortion is not corrected (it comes from the viewing angle, not a lens)
    sentinel = object()
    assert p.correct_distortion(img, sentinel, TaskContext()) is sentinel


def test_screen_profile_returns_after_an_automatic_switch(tmp_path):
    p, _ = setup(tmp_path)
    p.profiles.choose(next(t for t in TEMPLATES if t.id == "tpl-screen"))
    p.profiles.apply(next(t for t in TEMPLATES if t.id == "tpl-phone"))
    _phone_jpg(tmp_path / "ipad.jpg")
    img = p.load(tmp_path / "ipad.jpg")
    assert p.profiles.active_id == "tpl-screen" and "photos of a screen" in img.profile_note


def test_screen_profile_with_known_field(tmp_path):
    p, store = setup(tmp_path, equipment=False)
    mgr = p.profiles
    prof = mgr.new_profile("ASIAIR on iPad", "screen")
    prof.values["solver.astap"]["fov_override"] = 0.6
    prof = mgr.save(prof)
    assert "0.6" in prof.summary()
    mgr.choose(prof)
    assert store.get("solver.astap", "fov_override") == pytest.approx(0.6)


def test_softened_copy_retry(tmp_path):
    from platesolver.core import screenphoto
    from platesolver.core.models import ImageData, SolveResult
    data = np.zeros((60, 80), np.float32)
    data[::2, ::2] = 1.0                              # a moiré-like checker pattern
    img = ImageData(tmp_path / "x.jpg", "JPEG", data)
    soft = screenphoto.softened_copy(img)
    assert soft.data.shape == data.shape and soft.data.std() < data.std() / 2
    assert img.data.std() == data.std()               # the original is untouched

    class Solver:
        plugin_id, name, uses_pixels = "fake", "Fake", True
        def is_available(self):
            return True, ""

    class FakePipeline:
        def __init__(self, ok):
            self.ok, self.seen = ok, []
        def solvers(self):
            return [Solver()]
        def _run_solver(self, solver, image, ctx):
            self.seen.append(image)
            if not self.ok:
                return SolveResult.failed("no match")
            from astropy.wcs import WCS
            w = WCS(naxis=2)
            w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
            w.wcs.crval = [292.5, 18.0]
            w.wcs.crpix = [40, 30]
            w.wcs.cdelt = [-0.001, 0.001]
            return SolveResult.from_wcs(w, 80, 60, "fake", "Fake", 0.1)

    attempts = []
    res = screenphoto.solve_softened(FakePipeline(True), img, TaskContext(), attempts)
    assert res.success and "softened copy" in res.message and attempts
    fp = FakePipeline(False)
    assert screenphoto.solve_softened(fp, img, TaskContext(), []) is None and len(fp.seen) == 1
    fits_img = ImageData(tmp_path / "x.fits", "FITS", data)
    assert screenphoto.solve_softened(FakePipeline(True), fits_img, TaskContext(), []) is None


def test_profiles_dialog_screen_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from platesolver.ui.profiles_dialog import ProfilesDialog
    p, _ = setup(tmp_path, equipment=False)
    d = ProfilesDialog(p.profiles)
    row = next(i for i in range(d.list.count()) if "screen" in d.list.item(i).text())
    d.list.setCurrentRow(row)
    assert d.current.kind == "screen" and d.fov.isVisibleTo(d) and not d.focal.isVisibleTo(d)
    d._copy_template()
    d.fov.setValue(0.6)
    d._use()
    act = p.profiles.active()
    assert act.kind == "screen" and act.values["solver.astap"]["fov_override"] == pytest.approx(0.6)
    d.close()
    app.processEvents()
