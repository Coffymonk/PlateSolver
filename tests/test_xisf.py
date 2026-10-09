# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""PixInsight XISF images: pixel formats, compression, metadata and hints."""
import base64
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

from platesolver.core.general import GeneralSettings
from platesolver.core.pipeline import Pipeline
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import SettingsStore
from platesolver.plugins.loaders.xisf_loader import XisfError, XisfLoader, read_xisf

SAMPLE = Path(__file__).resolve().parent.parent / "Samples" / "xisf" / "IC5070" / "FINAL_PELICAN_IC5070.xisf"


def shuffle(buf: bytes, item: int) -> bytes:
    n = len(buf) // item
    return np.frombuffer(buf, np.uint8, n * item).reshape(n, item).T.tobytes() + buf[n * item:]


def write_xisf(path, data, fmt="UInt16", planar=True, compression=None, keywords=(), properties="",
               bounds=None, color=None):
    """A minimal XISF writer for tests (attachment block right after the header)."""
    data = np.asarray(data)
    h, w = data.shape[:2]
    c = data.shape[2] if data.ndim == 3 else 1
    arr = data.reshape(h, w, c)
    body = (arr.transpose(2, 0, 1) if planar else arr).astype({"UInt8": "<u1", "UInt16": "<u2",
                                                               "Float32": "<f4"}[fmt]).tobytes()
    comp_attr = ""
    if compression:
        item = {"UInt8": 1, "UInt16": 2, "Float32": 4}[fmt]
        src = shuffle(body, item) if compression.endswith("+sh") else body
        codec = compression.replace("+sh", "")
        if codec == "zlib":
            packed = zlib.compress(src)
        elif codec == "lz4":
            import lz4.block
            packed = lz4.block.compress(src, store_size=False)
        else:
            import zstandard
            packed = zstandard.ZstdCompressor().compress(src)
        comp_attr = f' compression="{compression}:{len(body)}' + (f':{item}"' if compression.endswith("+sh") else '"')
        body = packed
    kws = "".join(f'<FITSKeyword name="{k}" value="{v}" comment=""/>' for k, v in keywords)
    attrs = (f'geometry="{w}:{h}:{c}" sampleFormat="{fmt}" colorSpace="{color or ("RGB" if c == 3 else "Gray")}"'
             + ("" if planar else ' pixelStorage="Normal"') + (f' bounds="{bounds}"' if bounds else "") + comp_attr)

    def xml(pos):
        return (f'<?xml version="1.0" encoding="UTF-8"?><xisf version="1.0" xmlns="http://www.pixinsight.com/xisf">'
                f'<Image {attrs} location="attachment:{pos}:{len(body)}">{kws}{properties}</Image>'
                f'<Metadata><Property id="XISF:CreatorApplication" type="String">Test</Property></Metadata></xisf>')
    pos = 0
    while True:                      # the header mentions the block position, which depends on its length
        hdr = xml(pos).encode()
        if 16 + len(hdr) == pos:
            break
        pos = 16 + len(hdr)
    with open(path, "wb") as fh:
        fh.write(b"XISF0100" + struct.pack("<I", len(hdr)) + b"\0\0\0\0" + hdr)
        fh.seek(pos)
        fh.write(body)


def vec(name, values, kind="F64Vector"):
    raw = np.asarray(values, "<f8").tobytes()
    return f'<Property id="{name}" type="{kind}" length="{len(values)}">{base64.b64encode(raw).decode()}</Property>'


@pytest.mark.parametrize("planar", [True, False])
@pytest.mark.parametrize("compression", [None, "zlib", "zlib+sh", "lz4+sh", "zstd"])
def test_pixels_round_trip(tmp_path, planar, compression):
    rng = np.random.default_rng(3)
    rgb = rng.integers(0, 65535, (30, 40, 3), dtype=np.uint16)
    write_xisf(tmp_path / "x.xisf", rgb, planar=planar, compression=compression)
    img = XisfLoader().load(tmp_path / "x.xisf")
    assert img.data.shape == (30, 40, 3) and img.data.flags["C_CONTIGUOUS"]
    assert np.allclose(img.data, rgb / 65535.0, atol=1e-6)
    assert img.data[0, 0, 0] == pytest.approx(rgb[0, 0, 0] / 65535.0)   # top row stays on top


def test_float_mono_with_bounds_and_linear_detection(tmp_path):
    lin = np.full((50, 60), 100.0, np.float32)
    lin[10, 10] = 60000.0
    write_xisf(tmp_path / "lin.xisf", lin, fmt="Float32", bounds="0:65535")
    img = XisfLoader().load(tmp_path / "lin.xisf")
    assert img.data.ndim == 2 and img.data[10, 10] == pytest.approx(60000 / 65535, rel=1e-5)
    assert img.is_linear and img.bit_depth == "32-bit float"
    bright = np.full((50, 60), 0.3, np.float32)
    write_xisf(tmp_path / "proc.xisf", bright, fmt="Float32")
    assert not XisfLoader().load(tmp_path / "proc.xisf").is_linear


def test_fits_keywords_become_hints(tmp_path):
    kws = [("FOCALLEN", "349."), ("XPIXSZ", "2.9"), ("INSTRUME", "'ZWO ASI585MC Pro'"),
           ("OBJCTRA", "'20 50 48'"), ("OBJCTDEC", "'+44 21 00'"), ("OBJECT", "'IC 5070'")]
    write_xisf(tmp_path / "stack.xisf", np.zeros((20, 30), np.uint16), keywords=kws)
    img = XisfLoader().load(tmp_path / "stack.xisf")
    h = img.hints
    assert (h.focal_length_mm, h.pixel_size_um) == (349.0, 2.9)
    assert h.ra_deg == pytest.approx(312.7) and h.dec_deg == pytest.approx(44.35)
    assert img.header["INSTRUME"] == "ZWO ASI585MC Pro" and img.header["OBJECT"] == "IC 5070"


def test_pixinsight_plate_solution_and_instrument_properties(tmp_path):
    s = 0.857 / 3600
    props = (vec("PCL:AstrometricSolution:ReferenceCelestialCoordinates", [312.75, 44.35])
             + vec("PCL:AstrometricSolution:LinearTransformationMatrix", [s, 0.0, 0.0, -s], "F64Matrix")
             .replace('length="4"', 'rows="2" columns="2"')
             + '<Property id="Instrument:Telescope:FocalLength" type="Float32" value="0.349"/>'
             + '<Property id="Instrument:Sensor:XPixelSize" type="Float32" value="2.9"/>'
             + '<Property id="Instrument:Camera:Name" type="String">ZWO ASI585MC Pro</Property>')
    write_xisf(tmp_path / "solved.xisf", np.zeros((20, 30), np.uint16), properties=props)
    img = XisfLoader().load(tmp_path / "solved.xisf")
    h = img.hints
    assert h.ra_deg == pytest.approx(312.75) and h.dec_deg == pytest.approx(44.35)
    assert h.pixel_scale_arcsec == pytest.approx(0.857) and h.source["scale"] == "PixInsight plate solution"
    assert h.focal_length_mm == pytest.approx(349.0) and h.pixel_size_um == pytest.approx(2.9)
    assert img.header["INSTRUME"] == "ZWO ASI585MC Pro" and "plate solution" in img.notes[0]


def test_pipeline_opens_xisf_with_profile_and_file_name_hint(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    reg = PluginRegistry(store).discover()
    p = Pipeline(reg, store.section(GeneralSettings()))
    assert any(".xisf" in f for f in p.file_filters())
    redcat = p.profiles.save(p.profiles.new_profile("Redcat", "astro", "zwo-asi585mc-mm-pro", 349.0))
    write_xisf(tmp_path / "FINAL_PELICAN_IC5070.xisf", np.zeros((20, 30), np.uint16),
               keywords=[("INSTRUME", "'ZWO ASI585MC Pro'")])
    img = p.load(tmp_path / "FINAL_PELICAN_IC5070.xisf")
    assert img.format == "XISF" and p.profiles.active_id == redcat.id
    assert img.hints.scale_arcsec() == pytest.approx(1.714, abs=0.002)
    from platesolver.core.plugin import TaskContext
    p.hint_from_filename(img, TaskContext())
    assert img.hints.position_hint.startswith("IC 5070")


def test_bad_files(tmp_path):
    (tmp_path / "a.xisf").write_bytes(b"SIMPLE  = T")
    with pytest.raises(XisfError, match="not an XISF"):
        read_xisf(tmp_path / "a.xisf")
    write_xisf(tmp_path / "b.xisf", np.zeros((20, 30), np.uint16))
    data = (tmp_path / "b.xisf").read_bytes()
    (tmp_path / "b.xisf").write_bytes(data[:-100])
    with pytest.raises(XisfError, match="incomplete"):
        read_xisf(tmp_path / "b.xisf")


@pytest.mark.skipif(not SAMPLE.exists(), reason="Pelican sample not present")
def test_real_pixinsight_file():
    img = XisfLoader().load(SAMPLE)
    assert (img.width, img.height) == (7660, 4304) and img.is_color
    assert img.header.get("Software", "").startswith("PixInsight")
