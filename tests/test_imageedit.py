# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Crop and rotate the shown image (Image › Crop and rotate)."""
import numpy as np
import pytest
from PIL import Image

from platesolver.core.imageedit import Edit, clamp_crop, describe, edited_image, rotate_array
from platesolver.core.models import ImageData, SolveHints


def _peak(a):
    y, x = np.unravel_index(np.argmax(a if a.ndim == 2 else a.sum(axis=2)), a.shape[:2])
    return x, y


def test_rotation_directions():
    z = np.zeros((101, 101), np.float32)
    z[50, 90] = 1.0                                   # 40 px right of the centre
    x, y = _peak(rotate_array(z, 1, 0.0, 0))          # 90° clockwise: right -> below
    assert (x, y) == (50, 90)
    x, y = _peak(rotate_array(z, -1, 0.0, 0))         # anticlockwise: right -> above
    assert (x, y) == (50, 10)
    r = rotate_array(z, 0, 30.0, 0)                   # 30° clockwise: down and a little left
    cx, cy = (r.shape[1] - 1) / 2, (r.shape[0] - 1) / 2
    x, y = _peak(r)
    assert x - cx == pytest.approx(40 * np.cos(np.radians(30)), abs=1.0)
    assert y - cy == pytest.approx(40 * np.sin(np.radians(30)), abs=1.0)


def test_preview_and_data_rotate_to_the_same_size():
    data = np.random.default_rng(1).random((300, 500, 3)).astype(np.float32)
    rgb = (data * 255).astype(np.uint8)
    for q, a in ((0, 3.5), (1, -12.0), (2, 0.0), (3, 44.9)):
        assert rotate_array(data, q, a).shape == rotate_array(rgb, q, a).shape


def test_new_corners_are_sky_coloured():
    data = np.full((200, 300), 0.2, np.float32)
    r = rotate_array(data, 0, 20.0)
    assert r[0, 0] == pytest.approx(0.2) and r.shape[0] > 200 and r.shape[1] > 300


def test_clamp_crop():
    assert clamp_crop(None, 100, 100) is None
    assert clamp_crop((0, 0, 100, 100), 100, 100) is None           # the whole image = no crop
    assert clamp_crop((-10, 5, 60, 200), 100, 100) == (0, 5, 50, 95)
    with pytest.raises(ValueError):
        clamp_crop((10, 10, 20, 20), 100, 100)


def test_edited_image_keeps_the_scale_and_drops_a_stored_solution(tmp_path):
    data = np.random.default_rng(2).random((400, 600)).astype(np.float32)
    hints = SolveHints()
    hints.pixel_scale_arcsec = 1.04
    img = ImageData(tmp_path / "x.fits", "FITS", data, hints=hints, header_wcs=object(), rows_flipped=True,
                    star_count=55)
    new = edited_image(img, Edit(1, 0.0, (10, 20, 300, 200)))
    assert (new.width, new.height) == (300, 200)
    # 90° clockwise then crop: new[0, 0] is original row (600-1-... ) – check one pixel exactly
    rot = np.rot90(data, -1)
    assert np.array_equal(new.data, rot[20:220, 10:310])
    assert new.header_wcs is None and not new.rows_flipped and new.star_count is None
    assert new.hints.pixel_scale_arcsec == 1.04 and new.hints is not img.hints
    assert any("Edited view" in n for n in new.notes) and any("solved again" in n for n in new.notes)
    assert img.data is data and img.width == 600                    # the original is untouched
    assert describe(Edit(0, -2.0, (0, 0, 50, 50))) == "rotated 2° anticlockwise, cropped to 50 × 50 px"
    assert describe(Edit(3, 0.0)) == "rotated 90° anticlockwise"


def test_crop_and_rotate_in_the_main_window(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path / "home"))
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from platesolver.core import paths
    from platesolver.core.registry import PluginRegistry
    from platesolver.core.settings import SettingsStore
    from platesolver.ui.main_window import MainWindow
    store = SettingsStore(paths.settings_file())
    store.set("general", "auto_solve", False)
    win = MainWindow(store, PluginRegistry(store).discover())
    win.resize(1200, 800)
    win.show()
    rng = np.random.default_rng(3)
    Image.fromarray((rng.random((600, 900, 3)) * 255).astype(np.uint8)).save(tmp_path / "sky.jpg")

    def wait(cond, seconds=30):
        import time
        t0 = time.time()
        while not cond() and time.time() - t0 < seconds:
            app.processEvents()
        assert cond()

    win.open_file(tmp_path / "sky.jpg")
    wait(lambda: win.image is not None and win.worker is None)
    assert not win.edited() and not win.act_original.isEnabled()
    win.act_crop.trigger()
    assert win.view.crop_mode() and win.crop_bar.isVisible() and not win.act_solve.isEnabled()
    # draw a selection with the mouse
    vp = win.view.viewport()
    a, b = win.view.mapFromScene(100, 50), win.view.mapFromScene(700, 450)
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(vp, QPoint((a.x() + b.x()) // 2, (a.y() + b.y()) // 2))
    QTest.mouseMove(vp, b)
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, b)
    x, y, w, h = win.view.crop_rect()
    assert abs(x - 100) <= 3 and abs(y - 50) <= 3 and abs(w - 600) <= 4 and abs(h - 400) <= 4
    assert "Selection" in win.crop_bar.hint.text()
    # drag the right edge to resize, then drag inside to move
    for start, end in (((x + w, y + h // 2), (x + w + 100, y + h // 2)), ((x + 50, y + 50), (x + 30, y + 60))):
        p0, p1 = win.view.mapFromScene(*start), win.view.mapFromScene(*end)
        QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, p0)
        QTest.mouseMove(vp, p1)
        QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, p1)
    x2, y2, w2, h2 = win.view.crop_rect()
    assert abs(w2 - (w + 100)) <= 4 and h2 == h and abs(x2 - (x - 20)) <= 3 and abs(y2 - (y + 10)) <= 3
    # rotating starts a new selection on the rotated preview
    win.crop_bar.btn_right.click()
    assert win.view.crop_rect() is None and win.view.sceneRect().width() == 600
    win.view.set_crop_rect((50, 100, 400, 500))
    win.crop_bar.btn_apply.click()
    wait(lambda: win.worker is None and win.edited())
    assert (win.image.width, win.image.height) == (400, 500) and not win.view.crop_mode()
    assert win.act_original.isEnabled() and win.act_original_tb.isVisible()
    # a second crop works on the edited view; Cancel leaves it unchanged
    win.start_crop()
    win.cancel_crop()
    assert (win.image.width, win.image.height) == (400, 500) and not win.crop_bar.isVisible()
    win.restore_original()
    wait(lambda: win.worker is None and not win.edited())
    assert (win.image.width, win.image.height) == (900, 600)
    win.close()
    app.processEvents()


def test_images_are_not_solved_on_opening_by_default():
    from platesolver.core.general import GeneralSettings
    from platesolver.core.settings import SettingsStore
    store = SettingsStore(None)
    store.set("general", "auto_solve", True)          # saved by an earlier version
    GeneralSettings.migrate(store)
    assert store.section(GeneralSettings()).get("auto_solve") is False
    store.set("general", "auto_solve", True)          # the user's own choice afterwards is kept
    GeneralSettings.migrate(store)
    assert store.get("general", "auto_solve") is True
    assert SettingsStore(None).section(GeneralSettings()).get("auto_solve") is False
