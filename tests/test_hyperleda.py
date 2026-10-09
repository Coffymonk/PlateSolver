# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""ASTAP's HyperLeda galaxy database, and finding ASTAP star databases in a subfolder."""
from pathlib import Path

import pytest
from astropy.wcs import WCS

from platesolver.core.identifiers import canonical, merge_objects
from platesolver.core.models import SkyObject, SolveResult
from platesolver.core.plugin import TaskContext
from platesolver.core.settings import SettingsStore
from platesolver.plugins.catalogs import hyperleda_catalog as hl
from platesolver.plugins.solvers import astap
from platesolver.services import simbad

REAL = Path(__file__).resolve().parent.parent / "astap" / "hyperleda.csv"
W, H, SCALE = 2000, 1400, 2.0


def ra_units(deg):
    return int(round(deg * 2400))


def dec_units(deg):
    return int(round(deg * 3600))


def write_csv(path, rows):
    lines = ["ASTAP HyperLeda galaxy database containing 7 objects. Test.",
             "RA[0..864000], DEC[-324000..324000], magn*10, name(s), ..."]
    for r in rows:
        lines.append(",".join(str(v) for v in r))
    path.write_text("\n".join(lines) + "\n", encoding="latin-1")


def solution(ra=210.80, dec=54.35):
    s = SCALE / 3600
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crpix = [(W + 1) / 2, (H + 1) / 2]
    w.wcs.crval = [ra, dec]
    w.wcs.cd = [[-s, 0], [0, s]]
    w.wcs.set()
    return SolveResult.from_wcs(w, W, H, "astap", "ASTAP")


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path / "home"))
    hl._index_cache.clear()
    csv = tmp_path / "hyperleda.csv"
    write_csv(csv, [
        (ra_units(210.80), dec_units(54.35), "NGC5457", 2400, 2310),           # M101: skipped by default
        (ra_units(210.70), dec_units(54.30), "PGC049919", 12, 6, 45),
        (ra_units(210.90), dec_units(54.40), "SDSSJ140353.07+542154.4", 3, 2),
        (ra_units(210.85), dec_units(54.20), "2MASXJ14022821+5416256", 2.5, 2.1, 10),
        (ra_units(215.00), dec_units(54.35), "PGC000001", 5, 5),                 # outside the frame
        (ra_units(210.75), dec_units(54.45)),                                    # no name: skipped
        (ra_units(210.81), dec_units(54.31), "SAGITTARIUS_DWARF_SPHEROIDAL", 4, 2),
    ])
    store = SettingsStore(tmp_path / "s.json")
    p = hl.HyperLedaCatalog(store.section(hl.HyperLedaCatalog()))
    p.settings.set("file", str(csv))
    p.settings.set("online_details", False)
    return p


def test_names():
    assert hl.pretty_name("PGC049919") == "PGC 49919"
    assert hl.pretty_name("SDSSJ100155.00+411130.1") == "SDSS J100155.00+411130.1"
    assert hl.pretty_name("2MASXJ12260374+1306432") == "2MASX J12260374+1306432"
    assert hl.pretty_name("UGC05470") == "UGC 5470" and hl.pretty_name("UGCA166") == "UGCA 166"
    assert hl.pretty_name("ESO056-115") == "ESO 056-115"
    assert hl.pretty_name("SAGITTARIUS_DWARF_SPHEROIDAL") == "Sagittarius Dwarf Spheroidal"
    assert "LEDA 49919" in hl.simbad_forms("PGC 49919") and "ESO 56-115" in hl.simbad_forms("ESO 056-115")
    assert canonical("LEDA 49919") == canonical("PGC 049919") == "PGC49919"
    assert canonical("ESO 56-115") == canonical("ESO056-115")


def test_finds_galaxies_brightest_first(plugin):
    found = plugin.find_objects(None, solution(), TaskContext())
    names = [o.name for o in found]
    assert names == ["PGC 49919", "SDSS J140353.07+542154.4", "2MASX J14022821+5416256",
                     "Sagittarius Dwarf Spheroidal"]
    pgc = found[0]
    assert pgc.category == "galaxy" and pgc.catalog == "HyperLeda"
    assert pgc.size_arcmin == pytest.approx(1.2) and pgc.size_minor_arcmin == pytest.approx(0.6)
    assert pgc.position_angle_deg == 45 and "LEDA 49919" in pgc.aliases
    assert pgc.ra_deg == pytest.approx(210.70, abs=1e-4) and pgc.dec_deg == pytest.approx(54.30, abs=1e-4)
    x, y = solution().radec_to_pixel(210.70, 54.30)
    assert (pgc.x, pgc.y) == pytest.approx((x, y), abs=0.5)
    assert found[3].common_name == "Sagittarius Dwarf Spheroidal"


def test_settings_limit_and_filter(plugin):
    plugin.settings.set("max_objects", 2)
    assert len(plugin.find_objects(None, solution(), TaskContext())) == 2
    plugin.settings.set("max_objects", 50)
    plugin.settings.set("min_size", 0.5)
    assert [o.name for o in plugin.find_objects(None, solution(), TaskContext())] == ["PGC 49919"]
    plugin.settings.set("min_size", 0.0)
    plugin.settings.set("skip_ngc_ic", False)
    assert plugin.find_objects(None, solution(), TaskContext())[0].name == "NGC 5457"


def test_index_is_built_once_and_reused(plugin, tmp_path):
    logs = []
    plugin.find_objects(None, solution(), TaskContext(log=logs.append))
    assert any("first time" in m for m in logs)
    hl._index_cache.clear()
    logs.clear()
    plugin.find_objects(None, solution(), TaskContext(log=logs.append))
    assert not any("first time" in m for m in logs)
    assert len(list((tmp_path / "home" / "cache").glob("hyperleda-v*"))) == 1


def test_simbad_details_give_type_redshift_and_id(plugin, monkeypatch):
    plugin.settings.set("online_details", True)
    seen = []

    def fake_query(adql, timeout=90, use_cache=True):
        seen.append(adql)
        return [{"id": "LEDA 49919", "oid": "123", "main_id": "LEDA   49919", "otype": "G", "rvz_redshift": "0.0123"}]
    monkeypatch.setattr(simbad, "query", fake_query)
    found = plugin.find_objects(None, solution(), TaskContext())
    pgc = found[0]
    assert "'LEDA 49919'" in seen[0]
    assert pgc.extra["simbad_oid"] == 123 and pgc.extra["redshift"] == pytest.approx(0.0123)


def test_no_internet_still_lists_galaxies(plugin, monkeypatch):
    plugin.settings.set("online_details", True)

    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(simbad, "query", boom)
    assert len(plugin.find_objects(None, solution(), TaskContext())) == 4


def test_merges_with_simbad_entry():
    a = SkyObject(name="LEDA 49919", ra_deg=210.70, dec_deg=54.30, object_type="Galaxy", category="galaxy",
                  catalog="SIMBAD", extra={"identifiers": ["LEDA 49919"]})
    b = SkyObject(name="PGC 49919", ra_deg=210.7001, dec_deg=54.3001, object_type="Galaxy", category="galaxy",
                  size_arcmin=1.2, catalog="HyperLeda", extra={"identifiers": ["PGC 49919"]})
    merged = merge_objects([a, b])
    assert len(merged) == 1 and merged[0].size_arcmin == 1.2


def test_found_automatically_next_to_astap(tmp_path, monkeypatch):
    folder = tmp_path / "astap"
    (folder / "databases").mkdir(parents=True)
    (folder / "astap_cli.exe").write_text("")
    write_csv(folder / "databases" / "hyperleda.csv", [(0, 0, "PGC1", 1, 1)])
    store = SettingsStore(tmp_path / "s.json")
    store.set(astap.AstapSolver.section_key(), "path", str(folder / "astap_cli.exe"))
    p = hl.HyperLedaCatalog(store.section(hl.HyperLedaCatalog()))
    assert p.csv_file() == folder / "databases" / "hyperleda.csv"
    assert p.is_available()[0]
    assert hl.validate_file(str(folder / "databases" / "hyperleda.csv"))[0]
    field = next(f for f in p.settings_schema() if f.key == "file")
    ok, msg = field.validator("")
    assert ok and msg.startswith("Found automatically") and "hyperleda.csv" in msg
    (folder / "databases" / "hyperleda.csv").unlink()
    ok, msg = field.validator("")
    assert not ok and "Not found" in msg


def test_astap_databases_in_a_subfolder(tmp_path):
    folder = tmp_path / "astap"
    (folder / "Database").mkdir(parents=True)
    exe = folder / "astap_cli.exe"
    exe.write_text("")
    for f in ("d50_0101.1476", "d50_0102.1476", "w08_0101.001"):
        (folder / "Database" / f).write_text("")
    assert astap.database_dir(folder) == folder / "Database"
    store = SettingsStore(tmp_path / "s.json")
    solver = astap.AstapSolver(store.section(astap.AstapSolver()))
    solver.settings.set("path", str(exe))
    assert solver.installed_databases() == ["d50", "w08"]
    assert "Database folder" in astap.validate_path(str(exe))[1]
    labels = dict(next(f for f in solver.settings_schema() if f.key == "database").choices)
    assert "installed" in labels["d50"]
    from platesolver.core.models import ImageData
    import numpy as np
    img = ImageData(path=tmp_path / "x.fits", data=np.zeros((10, 10), np.float32), format="FITS")
    cmd, _ = solver.build_command(exe, tmp_path / "x.fits", img, fov=1.0)
    assert cmd[cmd.index("-d") + 1] == str(folder / "Database")
    # databases right next to ASTAP: no -d needed
    (folder / "d80_0101.1476").write_text("")
    (folder / "d20_0101.1476").write_text("")
    (folder / "v50_0101.1476").write_text("")
    assert astap.database_dir(folder) == folder
    cmd, _ = solver.build_command(exe, tmp_path / "x.fits", img, fov=1.0)
    assert "-d" not in cmd


@pytest.mark.skipif(not REAL.exists(), reason="ASTAP's hyperleda.csv not present")
def test_real_file_around_m101(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path / "home"))
    hl._index_cache.clear()
    store = SettingsStore(tmp_path / "s.json")
    p = hl.HyperLedaCatalog(store.section(hl.HyperLedaCatalog()))
    p.settings.set("file", str(REAL))
    p.settings.set("online_details", False)
    found = p.find_objects(None, solution(), TaskContext())
    assert len(found) == 50 and all(not o.name.startswith(("NGC", "IC ")) for o in found)


def test_skipped_for_wide_fields(plugin):
    wide = solution()
    wide.fov_width_deg, wide.fov_height_deg = 40.0, 55.0
    logs = []
    assert plugin.find_objects(None, wide, TaskContext(log=logs.append)) == []
    assert any("skipped" in m for m in logs)
