# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Filtering the object list."""
import os

import pytest

from platesolver.core.models import Distance, SkyObject
from platesolver.core.objectfilter import ObjectFilter, is_messier, is_well_known


def obj(name, otype="Galaxy", cat="galaxy", mag=None, ly=None, size=None, catalog="SIMBAD", common="", **extra):
    return SkyObject(name=name, ra_deg=0, dec_deg=0, object_type=otype, category=cat, magnitude=mag,
                     size_arcmin=size, catalog=catalog, common_name=common,
                     distance=Distance(ly) if ly else None, extra=extra)


M101 = obj("M 101", "Spiral galaxy", mag=7.9, ly=2.1e7, size=28, common="Pinwheel Galaxy")
N5474 = obj("NGC 5474", mag=10.8, ly=2.1e7, size=4.8)
STAR = obj("HD 122676", "Star", "star", mag=8.9, ly=412)
PGC = obj("PGC 49919", ly=3.1e8, size=1.2, catalog="HyperLeda")
SDSS = obj("SDSS J140353.07+542154.4", size=0.3, catalog="HyperLeda")
ALL = [M101, N5474, STAR, PGC, SDSS]


def names(f):
    return [o.name for o in ALL if f.matches(o)]


def test_default_shows_everything():
    f = ObjectFilter()
    assert names(f) == [o.name for o in ALL] and f.active_count() == 0 and f.describe() == ""


def test_magnitude_range_and_unknown_magnitudes():
    f = ObjectFilter(mag_max=9.0)
    assert names(f) == ["M 101", "HD 122676", "PGC 49919", "SDSS J140353.07+542154.4"]
    f.include_no_magnitude = False
    assert names(f) == ["M 101", "HD 122676"]
    assert names(ObjectFilter(mag_min=8.0, include_no_magnitude=False)) == ["NGC 5474", "HD 122676"]
    assert "mag …–9" in ObjectFilter(mag_max=9.0).describe()


def test_distance_and_size():
    assert names(ObjectFilter(dist_max_ly=1000)) == ["HD 122676"]
    assert names(ObjectFilter(dist_min_ly=1e8)) == ["PGC 49919"]
    assert names(ObjectFilter(only_with_distance=True)) == ["M 101", "NGC 5474", "HD 122676", "PGC 49919"]
    assert names(ObjectFilter(size_min_arcmin=1.0)) == ["M 101", "NGC 5474", "PGC 49919"]
    assert names(ObjectFilter(size_max_arcmin=2.0)) == ["HD 122676", "PGC 49919", "SDSS J140353.07+542154.4"]


def test_show_only_and_hidden_types_and_catalogues():
    assert names(ObjectFilter(only_messier=True)) == ["M 101"]
    assert names(ObjectFilter(only_named=True)) == ["M 101"]
    assert names(ObjectFilter(only_well_known=True)) == ["M 101", "NGC 5474"]
    assert names(ObjectFilter(hidden_types={"Star", "Spiral galaxy"})) == ["NGC 5474", "PGC 49919",
                                                                           "SDSS J140353.07+542154.4"]
    assert names(ObjectFilter(hidden_catalogs={"HyperLeda"})) == ["M 101", "NGC 5474", "HD 122676"]
    assert is_messier(SkyObject("NGC 5457", 0, 0, aliases=["M 101"]))
    assert not is_well_known(PGC) and is_well_known(obj("Sh2-101"))


def test_save_and_load():
    f = ObjectFilter(mag_max=12.5, only_messier=True, hidden_types={"Star"}, hidden_catalogs={"HyperLeda"})
    g = ObjectFilter.from_dict(f.to_dict())
    assert g == f and g.active_count() == 4
    assert ObjectFilter.from_dict(None) == ObjectFilter()
    assert ObjectFilter.from_dict({"mag_max": "bad", "unknown": 1}).mag_max is None


def test_panel_filter_button_and_overlay_list(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from platesolver.ui.sidebar import ObjectsPanel

    app = QApplication.instance() or QApplication([])
    p = ObjectsPanel()
    saved = []
    p.filter_saved.connect(saved.append)
    p.set_objects(list(ALL), 1)
    panel = p.filter_panel
    assert panel.types.count() == 3 and panel.catalogs.count() == 2      # Galaxy, Spiral galaxy, Star
    panel.mag_max.setText("9")
    panel.no_mag.setChecked(False)
    panel._timer.timeout.emit()
    app.processEvents()
    assert [o.name for o in p.visible_objects()] == ["M 101", "HD 122676"]
    assert saved and saved[-1]["mag_max"] == 9.0
    assert "Filter: mag" in p.summary.text() and "Showing 2" in p.summary.text()
    p._type_filter("Star", only=True)            # right-click › Show only: Star
    assert [o.name for o in p.visible_objects()] == ["HD 122676"]
    panel.reset()
    assert len(p.visible_objects()) == len(ALL) and p.filter_panel.filter().active_count() == 0
    # the distance unit is applied
    panel.dist_unit.setCurrentIndex(2)          # million ly
    panel.dist_min.setText("100")
    panel._timer.timeout.emit()
    assert [o.name for o in p.visible_objects()] == ["PGC 49919"]


def test_decimal_input_with_point_or_comma():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QLocale
    from PySide6.QtGui import QValidator
    from PySide6.QtWidgets import QApplication
    from platesolver.ui.object_filter import _num_edit, _value

    app = QApplication.instance() or QApplication([])
    old = QLocale()
    QLocale.setDefault(QLocale(QLocale.Swedish, QLocale.Sweden))
    try:
        e = _num_edit()
        for text, value in (("9.5", 9.5), ("9,5", 9.5), ("-1.46", -1.46), ("12,25", 12.25), ("0.3", 0.3)):
            assert e.validator().validate(text, 0)[0] == QValidator.Acceptable
            e.setText(text)
            assert _value(e) == pytest.approx(value)
        assert e.validator().validate("abc", 0)[0] == QValidator.Invalid
    finally:
        QLocale.setDefault(old)


def test_object_label_text_size_is_its_own_setting(tmp_path):
    from astropy.wcs import WCS
    from platesolver.core.models import ImageData, SolveResult
    from platesolver.core.settings import SettingsStore
    from platesolver.plugins.overlays.object_labels import ObjectLabelsOverlay
    import numpy as np

    class Recorder:
        def __init__(self, font_scale, size_scale=1.0):
            self.font_scale, self.size_scale, self.screen_px, self.sizes = font_scale, size_scale, 1.0, []
            self.ly_decimals = 1

        def text(self, x, y, text, color, size=10, anchor="left"):
            self.sizes.append(size * self.font_scale)        # the pixel size the real painter uses

        def text_extent(self, text, size=10):
            return len(text) * size * self.font_scale * 0.6, size * self.font_scale * 1.3

        def __getattr__(self, name):                         # outlines, lines, …
            return lambda *a, **k: None

    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crpix, w.wcs.crval, w.wcs.cd = [500, 400], [83.8, -5.4], [[-0.001, 0], [0, 0.001]]
    w.wcs.set()
    sol = SolveResult.from_wcs(w, 1000, 800, "t", "t")
    img = ImageData(tmp_path / "x.fits", "FITS", np.zeros((800, 1000), np.float32))
    o = obj("M 42", "Nebula", "nebula", size=10)
    o.x, o.y = 500.0, 400.0
    store = SettingsStore(tmp_path / "s.json")
    ov = ObjectLabelsOverlay(store.section(ObjectLabelsOverlay()))
    for general_scale in (1.0, 2.0):                         # General › Overlay text size 11 or 22 px
        r = Recorder(general_scale)
        ov.render(r, img, sol, [o])
        assert r.sizes and r.sizes[0] == pytest.approx(14.0)
    ov.settings.set("label_size", 20)
    r = Recorder(1.0, size_scale=2.0)                        # an export at twice the reference width
    ov.render(r, img, sol, [o])
    assert r.sizes[0] == pytest.approx(40.0)
