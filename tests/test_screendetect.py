# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Photos of a screen: tilt, star area, app text and buttons, hiding them, and the profile suggestion."""
import numpy as np
import pytest

from platesolver.core import screendetect as sd
from platesolver.core.imageedit import Edit, edited_image, hide_areas, rotate_array
from platesolver.core.models import ImageData

H, W = 900, 1400


def star_field(h, w, n, rng, sky=0.18):
    a = np.full((h, w), sky, np.float32) + rng.normal(0, 0.01, (h, w)).astype(np.float32)
    ys, xs = rng.integers(3, h - 3, n), rng.integers(3, w - 3, n)
    for y, x in zip(ys, xs):
        b = rng.uniform(0.3, 0.8)
        a[y - 1:y + 2, x - 1:x + 2] += b * 0.5
        a[y, x] += b * 0.5
    return np.clip(a, 0, 1)


def screen_photo(tilt=3.0, seed=1):
    """A desk, a dark tablet bezel, and inside it a star field with app text at the top and a toolbar."""
    rng = np.random.default_rng(seed)
    img = 0.45 + 0.1 * np.sin(np.arange(W)[None, :] / 7.0) * np.ones((H, 1), np.float32)   # wood grain
    img = img.astype(np.float32) + rng.normal(0, 0.02, (H, W)).astype(np.float32)
    img[120:780, 180:1220] = 0.04                                                         # bezel
    sx0, sy0, sx1, sy1 = 220, 160, 1180, 740
    img[sy0:sy1, sx0:sx1] = star_field(sy1 - sy0, sx1 - sx0, 900, rng)
    # app text: two lines of letter-like marks near the top, and a column of hollow icons on the right
    for line_y in (180, 200):
        for k in range(14):
            x = 300 + k * 11
            img[line_y:line_y + 9, x:x + 2] = 0.95
            img[line_y:line_y + 2, x:x + 7] = 0.95
    for k in range(4):
        y = 300 + k * 70
        img[y:y + 30, 1120:1150] = 0.95
        img[y + 4:y + 26, 1124:1146] = 0.18
    return np.clip(rotate_array(img, 0, tilt, fill=0.45), 0, 1)


def test_tilt_of_a_screen_and_of_a_sky_photo():
    assert sd.estimate_tilt(screen_photo(tilt=3.0)) == pytest.approx(-3.0, abs=0.5)
    sky = star_field(H, W, 2500, np.random.default_rng(2))
    assert sd.estimate_tilt(sky) == 0.0


def test_star_area_and_screen_suggestion():
    g = sd.find_star_area(screen_photo(tilt=3.0))
    assert g.looks_like_screen and g.sharp_sides >= 3
    assert g.angle == pytest.approx(-3.0, abs=0.5)
    x, y, w, h = g.crop
    straight = rotate_array(screen_photo(tilt=3.0), 0, g.angle)
    assert straight[y + h // 2, x + w // 2] < 0.4                  # the middle of the crop is sky, not desk
    assert 700 < w < 1000 and 420 < h < 620                        # roughly the star area (960 x 580)
    sky = sd.find_star_area(star_field(H, W, 2500, np.random.default_rng(3)))
    assert not sky.looks_like_screen


def test_app_text_and_buttons_are_found_but_stars_are_left():
    rng = np.random.default_rng(4)
    a = star_field(580, 960, 900, rng)
    for line_y in (20, 40):
        for k in range(14):
            x = 80 + k * 11
            a[line_y:line_y + 9, x:x + 2] = 0.95
            a[line_y:line_y + 2, x:x + 7] = 0.95
    for k in range(4):
        y = 140 + k * 70
        a[y:y + 30, 900:930] = 0.95
        a[y + 4:y + 26, 904:926] = 0.18
    marks = sd.find_app_marks(a)
    covered = np.zeros_like(a, bool)
    for x, y, w, h in marks:
        covered[y:y + h, x:x + w] = True
    assert covered[24, 120] and covered[44, 200]                  # the text lines
    assert all(covered[150 + k * 70, 915] for k in range(4))       # the toolbar icons
    assert covered.mean() < 0.15                                    # most of the sky is left alone


def test_hidden_areas_are_filled_with_the_surrounding_sky():
    a = np.full((100, 120), 0.2, np.float32)
    a[30:50, 40:80] = 0.9                                           # a bright button
    out = hide_areas(a, [(38, 28, 44, 24)])
    assert out[40, 60] == pytest.approx(0.2) and a[40, 60] == pytest.approx(0.9)
    rgb = np.dstack([a, a * 0.5, a * 0.25])
    out = hide_areas(rgb, [(38, 28, 44, 24)])
    assert out[40, 60, 1] == pytest.approx(0.1)
    img = ImageData(__import__("pathlib").Path("x.jpg"), "JPEG", a)
    new = edited_image(img, Edit(crop=(10, 10, 100, 80), hide=[(38, 28, 44, 24)]))
    assert new.data.shape == (80, 100) and new.data[30, 50] == pytest.approx(0.2)
    assert "1 area hidden" in new.notes[-1]


def test_screen_tools_in_the_main_window(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path / "home"))
    from PIL import Image
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
    win.resize(1300, 850)
    win.show()
    Image.fromarray((screen_photo() * 255).astype(np.uint8)).save(tmp_path / "ipad.jpg")

    def wait(cond, seconds=60):
        import time
        t0 = time.time()
        while not cond() and time.time() - t0 < seconds:
            app.processEvents()
        assert cond()
    win.open_file(tmp_path / "ipad.jpg")
    wait(lambda: win.image is not None and win.worker is None)
    assert win.suggest_bar.isVisible()                               # "This looks like a photo of a screen"
    win.suggest_bar._buttons[0].click()                              # use the profile and crop
    app.processEvents()
    assert win.pipeline.profiles.active().kind == "screen" and win.view.crop_mode()
    assert win._crop_angle == pytest.approx(-3.0, abs=0.5) and win.view.crop_rect() is not None
    n = len(win.view.hidden_rects())
    assert n >= 2
    # draw one more hidden area, then click it away again
    win.crop_bar.btn_hide.click()
    vp = win.view.viewport()
    a, b = win.view.mapFromScene(400, 500), win.view.mapFromScene(460, 540)
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(vp, b)
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, b)
    assert len(win.view.hidden_rects()) == n + 1
    mid = win.view.mapFromScene(430, 520)
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, mid)
    assert len(win.view.hidden_rects()) == n
    win.apply_crop(False)
    wait(lambda: win.worker is None and win.edited())
    assert "hidden" in win.image.notes[-1]
    # a normal sky photo gets no suggestion
    Image.fromarray((star_field(H, W, 2500, np.random.default_rng(5)) * 255).astype(np.uint8)).save(tmp_path / "s.jpg")
    win.open_file(tmp_path / "s.jpg")
    wait(lambda: win.worker is None and win.image.path.name == "s.jpg")
    assert not win.suggest_bar.isVisible()
    win.close()
    app.processEvents()
