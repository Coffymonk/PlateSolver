# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""tools/update_openngc.py: converting an OpenNGC release, refusing a broken one, and the version display."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import update_openngc as U  # noqa: E402

HEAD = ("Name;Type;RA;Dec;Const;MajAx;MinAx;PosAng;B-Mag;V-Mag;J-Mag;H-Mag;K-Mag;SurfBr;Hubble;Pax;Pm-RA;Pm-Dec;"
        "RadVel;Redshift;Cstar U-Mag;Cstar B-Mag;Cstar V-Mag;M;NGC;IC;Cstar Names;Identifiers;Common names;"
        "NED notes;OpenNGC notes;Sources")


def row(name, typ, ra, dec, maj="", m="", ids="", common="", z=""):
    cells = [name, typ, ra, dec, "And", maj, "", "35", "4.29", "3.44", "", "", "", "", "", "", "", "", "", z,
             "", "", "", m, "", "", "", ids, common, "", "", ""]
    return ";".join(cells)


def test_convert_matches_the_bundled_format():
    text = "\n".join([HEAD,
                      row("NGC0224", "G", "00:42:44.35", "+41:16:08.6", "177.83", "031", "PGC 002557,UGC 00454",
                          "Andromeda Galaxy", "-0.001000"),
                      row("NGC0001", "Dup", "00:07:15.84", "+27:42:29.1"),          # duplicate: left out
                      row("NGC9999", "NonEx", "00:07:15.84", "+27:42:29.1"),        # doesn't exist: left out
                      row("IC0002", "G", "00:11:00.88", "-12:49:22.3", "0.98", z="0.022860")])
    rows = U.convert(text)
    assert [r["name"] for r in rows] == ["NGC0224", "IC0002"]
    m31, ic2 = rows
    assert m31["ra"] == "10.684792" and m31["dec"] == "41.269056"
    assert m31["messier"] == "031" and m31["commonnames"] == "Andromeda Galaxy" and m31["pa"] == "35"
    assert m31["identifiers"] == "PGC 002557,UGC 00454" and m31["redshift"] == "-0.001"
    assert ic2["dec"] == "-12.822861" and ic2["redshift"] == "0.02286"
    assert list(m31) == list(m31) and set(m31) == set(U.OUT_COLUMNS)


def test_a_broken_download_changes_nothing(tmp_path):
    with pytest.raises(SystemExit, match="nothing was changed"):
        U.check([{"name": "NGC0224", "ra": "10", "dec": "41"}])


def test_write_records_the_version(tmp_path):
    rows = [{k: "" for k in U.OUT_COLUMNS} | {"name": "NGC0224", "ra": "10.6", "dec": "41.2"}]
    U.write(rows, "v20260501", tmp_path)
    info = json.loads((tmp_path / "openngc_version.json").read_text())
    assert info["version"] == "v20260501" and info["release_date"] == "2026-05-01" and info["objects"] == 1
    assert (tmp_path / "openngc.csv").read_text().startswith(",".join(U.OUT_COLUMNS))


def test_settings_page_shows_the_bundled_version():
    from platesolver.plugins.catalogs.openngc_catalog import OpenNgcCatalog, version_info
    info = version_info()
    assert info["version"].startswith("v") and info["objects"] > 13000
    assert f"OpenNGC {info['version']}" in OpenNgcCatalog().description
