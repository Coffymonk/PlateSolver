# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Photos of a screen taken a little from the side: finding the screen's corners, squaring it up, the
Screen corners tool, the automatic crop when the screen profile is chosen, and the retry when solving fails."""
import numpy as np
import pytest
from PIL import Image

from platesolver.core import screendetect as sd
from platesolver.core.imageedit import (Edit, edited_image, keystone, map_points, map_rect, rotate_array,
                                        square_size, square_up)
from platesolver.core.models import ImageData

H, W = 900, 1400
MARKS = [(260, 200), (1140, 200), (1140, 700), (260, 700)]     # four bright "stars" at the corners of a rectangle


def flat_photo(seed=1):
    rng = np.random.default_rng(seed)
    img = 0.45 + 0.1 * np.sin(np.arange(W)[None, :] / 7.0) * np.ones((H, 1), np.float32)   # desk
    img = img.astype(np.float32) + rng.normal(0, 0.02, (H, W)).astype(np.float32)
    img[120:780, 180:1220] = 0.03                                                          # bezel
    sky = np.full((580, 960), 0.2, np.float32) + rng.normal(0, 0.01, (580, 960)).astype(np.float32)
    for y, x in zip(rng.integers(3, 577, 900), rng.integers(3, 957, 900)):
        sky[y - 1:y + 2, x - 1:x + 2] += rng.uniform(0.15, 0.4)
    img[160:740, 220:1180] = sky
    for x, y in MARKS:
        img[y - 3:y + 4, x - 3:x + 4] = 1.0
    return np.clip(img, 0, 1)


def tilted_photo():
    """The flat photo seen a little from the side: the right side is 6 % shorter than the left."""
    from platesolver.core.imageedit import _homography
    src = [(0, 0), (W, 0), (W, H), (0, H)]
    dst = [(40, 20), (W - 60, 50), (W - 60, H - 50), (40, H - 20)]
    inv = _homography(dst, src)
    coeffs = tuple((inv / inv[2, 2]).flatten()[:8])
    im = Image.fromarray(flat_photo(), mode="F").transform((W, H), Image.PERSPECTIVE, coeffs, Image.BILINEAR,
                                                          fillcolor=0.45)
    return np.asarray(im, np.float32), _homography(src, dst)


def warp_point(M, x, y):
    p = M @ np.array([x, y, 1.0])
    return p[0] / p[2], p[1] / p[2]


def bright_spots(a, n=4):
    """Centres of the n brightest compact spots."""
    b = a.copy()
    out = []
    for _ in range(n):
        y, x = np.unravel_index(np.argmax(b), b.shape)
        y0, x0 = max(0, y - 6), max(0, x - 6)
        win = a[y0:y + 7, x0:x + 7]
        wy, wx = np.mgrid[y0:y0 + win.shape[0], x0:x0 + win.shape[1]]
        wgt = np.clip(win - 0.6, 0, None)
        out.append((float((wx * wgt).sum() / wgt.sum()), float((wy * wgt).sum() / wgt.sum())))
        b[max(0, y - 12):y + 13, max(0, x - 12):x + 13] = 0
    return out


def test_square_up_and_mapping():
    quad = [(10, 12), (110, 8), (115, 90), (5, 95)]
    w, h = square_size(quad)
    assert (w, h) == (110, 83)
    assert np.allclose(map_points(quad, quad), [(0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)], atol=1e-6)
    a = np.random.default_rng(0).random((100, 130)).astype(np.float32)
    assert square_up(a, quad).shape == (h, w)
    assert square_up((a[..., None] * np.ones(3) * 255).astype(np.uint8), quad).shape == (h, w, 3)
    assert map_rect((10, 12, 50, 40), quad, w, h)[:2] == (0, 0)
    assert keystone([(0, 0), (100, 0), (100, 100), (0, 100)]) == 0.0


def test_screen_corners_are_found_and_the_stars_become_a_rectangle_again():
    photo, M = tilted_photo()
    # the corner stars are no longer a rectangle in the tilted photo
    pts = [warp_point(M, x, y) for x, y in MARKS]
    left, right = pts[3][1] - pts[0][1], pts[2][1] - pts[1][1]
    assert abs(left - right) / left > 0.03
    g = sd.find_star_area(photo, perspective=True)
    assert g.looks_like_screen and g.quad is not None and 0.02 < g.keystone < 0.15
    squared = square_up(photo, g.quad, g.aspect)
    s = sorted(bright_spots(squared), key=lambda p: (round(p[1] / 100), p[0]))
    tl, tr, bl, br = s[0], s[1], s[2], s[3]
    widths = (tr[0] - tl[0], br[0] - bl[0])
    heights = (bl[1] - tl[1], br[1] - tr[1])
    assert abs(widths[0] - widths[1]) / max(widths) < 0.01           # opposite sides equal again
    assert abs(heights[0] - heights[1]) / max(heights) < 0.01
    assert abs((widths[0] / heights[0]) / (880 / 500) - 1) < 0.06      # and close to the true shape
    # the star area is in the squared-up picture; the plain crop stays for when it isn't squared up
    x, y, w, h = g.quad_crop
    assert w > 600 and h > 350 and x + w <= squared.shape[1] and y + h <= squared.shape[0]
    assert g.crop is not None
    # without the setting nothing changes
    assert sd.find_star_area(photo).quad is None


def test_edit_with_quad():
    photo, _ = tilted_photo()
    g = sd.find_star_area(photo, perspective=True)
    img = ImageData(__import__("pathlib").Path("tablet.jpg"), "JPEG", photo)
    new = edited_image(img, Edit(0, 0.0, g.quad_crop, quad=g.quad, aspect=g.aspect))
    assert new.data.shape == (g.quad_crop[3], g.quad_crop[2])
    assert "perspective corrected" in new.notes[-1]
    assert not Edit(quad=g.quad).is_identity()


def _window(tmp_path, monkeypatch, perspective="if_fails"):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from platesolver.core import paths
    from platesolver.core.registry import PluginRegistry
    from platesolver.core.settings import SettingsStore
    from platesolver.ui import main_window as mw
    monkeypatch.setattr(mw.QMessageBox, "information", lambda *a, **k: None)
    store = SettingsStore(paths.settings_file())
    store.set("general", "auto_solve", False)
    store.set("profiles", "perspective", perspective)
    win = mw.MainWindow(store, PluginRegistry(store).discover())
    win.resize(1300, 850)
    win.show()
    Image.fromarray((tilted_photo()[0] * 255).astype(np.uint8)).save(tmp_path / "tablet.jpg")

    def wait(cond, seconds=60):
        import time
        t0 = time.time()
        while not cond() and time.time() - t0 < seconds:
            app.processEvents()
        assert cond()
    return app, win, wait


def test_reopening_with_the_screen_profile_crops_automatically(tmp_path, monkeypatch):
    app, win, wait = _window(tmp_path, monkeypatch)
    win.open_file(tmp_path / "tablet.jpg")
    wait(lambda: win.image is not None and win.worker is None)
    assert win.suggest_bar.isVisible() and not win.view.crop_mode()       # first time: a suggestion
    win.suggest_bar._buttons[1].click()                                    # use the profile only
    assert win._screen_profile_active()
    win.open_file(tmp_path / "tablet.jpg")                                 # again, now with the profile
    wait(lambda: win.worker is None and win.view.crop_mode())
    assert win.view.crop_rect() is not None and win.suggest_bar.isVisible()
    assert "photo of a screen" in win.suggest_bar.text.text()
    win.suggest_bar._buttons[1].click()                                    # show the whole photo
    assert not win.view.crop_mode() and not win.suggest_bar.isVisible()
    # "only suggest" asks instead
    win.store.set("profiles", "screen_on_open", "suggest")
    win.open_file(tmp_path / "tablet.jpg")
    wait(lambda: win.worker is None and win.suggest_bar.isVisible())
    assert not win.view.crop_mode()
    win.suggest_bar._buttons[0].click()
    assert win.view.crop_mode() and win.view.crop_rect() is not None
    win.cancel_crop()
    win.store.set("profiles", "screen_on_open", "off")
    win.open_file(tmp_path / "tablet.jpg")
    wait(lambda: win.worker is None and win.image is not None)
    app.processEvents()
    assert not win.suggest_bar.isVisible() and not win.view.crop_mode()
    win.close()


def test_screen_corners_tool_and_always_setting(tmp_path, monkeypatch):
    app, win, wait = _window(tmp_path, monkeypatch, perspective="always")
    win.open_file(tmp_path / "tablet.jpg")
    wait(lambda: win.image is not None and win.worker is None)
    win.suggest_bar._buttons[0].click()                                    # profile + crop: squared up at once
    assert win._crop_quad is not None and win.view.crop_rect() is not None
    win.crop_bar.btn_corners.click()                                       # the corners on the unsquared picture
    assert win.view.crop_tool() == "corners" and win.view.quad() is not None
    q = list(win.view.quad())
    q[0] = (q[0][0] + 5, q[0][1] + 5)
    win.view.set_quad(q)
    win._quad_moved = True
    win.crop_bar.btn_keep.click()                                          # squared up with the corners shown
    assert win._crop_quad_manual and win._crop_quad[0] == pytest.approx(q[0])
    win.find_star_area()                                                   # keeps the hand-set corners
    assert win._crop_quad[0] == pytest.approx(q[0]) and win.view.crop_rect() is not None
    win.apply_crop(False)
    wait(lambda: win.worker is None and win.edited())
    assert "perspective corrected" in win.image.notes[-1]
    assert win._last_edit is not None and win._last_edit.quad is not None
    win.close()


def test_failed_solve_is_retried_with_the_perspective_corrected(tmp_path, monkeypatch):
    app, win, wait = _window(tmp_path, monkeypatch, perspective="if_fails")
    from platesolver.core.models import SolveResult
    calls = []

    def fake_solve(image, ctx):
        calls.append(image)
        return SolveResult.failed("no stars matched")
    monkeypatch.setattr(win.pipeline, "solve", fake_solve)
    monkeypatch.setattr(win, "_offer_astap_setup", lambda: False)
    monkeypatch.setattr(win, "_offer_hint", lambda: None)
    monkeypatch.setattr(win, "_use_quad", lambda guess: False)              # as if only slightly skewed
    win.open_file(tmp_path / "tablet.jpg")
    wait(lambda: win.image is not None and win.worker is None)
    win.suggest_bar._buttons[0].click()                                    # profile + crop (not squared up)
    assert win._crop_quad is None
    win.apply_crop(True)
    wait(lambda: len(calls) >= 2 and win.worker is None)
    app.processEvents()
    assert "perspective corrected" in calls[1].notes[-1]
    assert win.image is calls[1] and win._last_edit.quad is not None
    win.solve()                                                            # no second retry
    wait(lambda: len(calls) >= 3 and win.worker is None)
    app.processEvents()
    assert len(calls) == 3
    win.close()


def test_clearly_skewed_screen_is_squared_up_at_once(tmp_path, monkeypatch):
    app, win, wait = _window(tmp_path, monkeypatch, perspective="if_fails")
    win.open_file(tmp_path / "tablet.jpg")
    wait(lambda: win.image is not None and win.worker is None)
    win.suggest_bar._buttons[0].click()                                    # profile + crop
    assert win._crop_quad is not None and win._crop_angle == 0.0           # 6 % skew: squared up
    win.apply_crop(False)
    wait(lambda: win.worker is None and win.edited())
    assert "perspective corrected" in win.image.notes[-1]
    win.store.set("profiles", "perspective", "off")
    win.restore_original()
    wait(lambda: win.worker is None and not win.edited())
    win.start_crop()
    win.find_star_area()
    assert win._crop_quad is None
    win.close()


def camera_view(flat, aspect_true, yaw_deg, pitch_deg, f35=24.0, size=(1600, 1200)):
    """The flat screen photo seen by a camera (35 mm-equivalent focal length f35) turned yaw/pitch degrees
    away from straight on; returns the photo and the true corners of the screen's star area."""
    from platesolver.core.imageedit import _homography
    W, H = size
    f = f35 * np.hypot(W, H) / 43.2666
    K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1]])
    y, p = np.radians(yaw_deg), np.radians(pitch_deg)
    Ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
    R = Rx @ Ry
    fh, fw = flat.shape
    # the flat photo's pixel (u, v) lies on a plane, centred, 0.55 of the view wide, 2.2 widths away
    span = 2.2 * np.tan(0.5 * 2 * np.arctan(W / (2 * f))) * 0.55 / 0.5
    s = span / fw
    A = np.array([[s, 0, -s * fw / 2], [0, s, -s * fh / 2], [0, 0, 1.0]])
    t = np.array([0, 0, 2.2])
    Hm = K @ np.c_[R[:, 0], R[:, 1], t] @ A                 # flat pixel -> photo pixel
    inv = np.linalg.inv(Hm)
    inv /= inv[2, 2]
    im = Image.fromarray(flat, mode="F").transform((W, H), Image.PERSPECTIVE, tuple(inv.flatten()[:8]),
                                                  Image.BILINEAR, fillcolor=0.45)

    def to_photo(u, v):
        q = Hm @ np.array([u, v, 1.0])
        return q[0] / q[2], q[1] / q[2]
    sky = [to_photo(220, 160), to_photo(1180, 160), to_photo(1180, 740), to_photo(220, 740)]
    frame = [to_photo(180, 120), to_photo(1220, 120), to_photo(1220, 780), to_photo(180, 780)]
    return np.asarray(im, np.float32), sky, frame


@pytest.mark.parametrize("yaw, pitch", [(35, 0), (-30, 15), (10, -35)])
def test_strongly_skewed_screen_is_found_with_its_true_proportions(yaw, pitch):
    from platesolver.core.imageedit import screen_aspect
    photo, sky, frame = camera_view(flat_photo(), 960 / 580, yaw, pitch)
    W, H = photo.shape[1], photo.shape[0]
    # the true corners give the true proportions back
    assert screen_aspect(sky, W, H, 24.0) == pytest.approx(960 / 580, rel=0.01)
    assert screen_aspect(frame, W, H, 24.0) == pytest.approx(1040 / 660, rel=0.01)
    g = sd.find_star_area(photo, perspective=True, focal35=24.0)
    assert g.looks_like_screen and g.quad is not None and g.keystone > 0.1
    # the corners found lie on the edge of the star area or of the screen's frame (to 1 % of the width),
    # both rectangles on the screen, with their own proportions
    for true, aspect in ((sky, 960 / 580), (frame, 1040 / 660)):
        if all(abs(x - tx) < 0.01 * W and abs(y - ty) < 0.01 * W for (x, y), (tx, ty) in zip(g.quad, true)):
            assert g.aspect == pytest.approx(aspect, rel=0.04)
            break
    else:
        pytest.fail(f"corners {g.quad} are on neither rectangle")
    # squared up, the four marker stars form a rectangle of the right shape again
    squared = square_up(photo, g.quad, g.aspect)
    s = sorted(bright_spots(squared), key=lambda p: (round(p[1] / 150), p[0]))
    tl, tr, bl, br = s
    w1, w2, h1, h2 = tr[0] - tl[0], br[0] - bl[0], bl[1] - tl[1], br[1] - tr[1]
    assert abs(w1 - w2) / max(w1, w2) < 0.02 and abs(h1 - h2) / max(h1, h2) < 0.02
    assert (w1 / h1) == pytest.approx(880 / 500, rel=0.05)
