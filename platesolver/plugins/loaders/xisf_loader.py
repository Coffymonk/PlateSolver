# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""PixInsight XISF images (.xisf).

Reads the first image of the file: 8/16/32-bit integer or 32/64-bit float, mono or colour, planar or
normal pixel storage, uncompressed or compressed (zlib, LZ4, LZ4HC, Zstandard, with or without byte
shuffling). FITS keywords and XISF properties are kept in the header; the useful ones become solving
hints: position (RA/Dec, the centre of a stored plate solution), focal length, pixel size, scale and
the camera name (for automatic profile choice).

A plate solution stored by PixInsight is used as an exact position and scale hint, so the image is
re-solved within seconds; this avoids any doubt about pixel-orientation conventions.

XISF specification: https://pixinsight.com/doc/docs/XISF-1.0-spec/XISF-1.0-spec.html
"""
from __future__ import annotations

import base64
import math
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import numpy as np

from platesolver.core.interfaces import ImageLoader
from platesolver.core.models import ImageData
from platesolver.plugins.loaders.fits_loader import hints_from_header

NS = "{http://www.pixinsight.com/xisf}"
SAMPLE_TYPES = {"UInt8": "u1", "UInt16": "u2", "UInt32": "u4", "UInt64": "u8",
                "Float32": "f4", "Float64": "f8"}
VECTOR_TYPES = {"I8Vector": "i1", "UI8Vector": "u1", "I16Vector": "i2", "UI16Vector": "u2",
                "I32Vector": "i4", "UI32Vector": "u4", "I64Vector": "i8", "UI64Vector": "u8",
                "F32Vector": "f4", "F64Vector": "f8",
                "I8Matrix": "i1", "UI8Matrix": "u1", "I16Matrix": "i2", "UI16Matrix": "u2",
                "I32Matrix": "i4", "UI32Matrix": "u4", "F32Matrix": "f4", "F64Matrix": "f8"}


class XisfError(ValueError):
    pass


# ---------------------------------------------------------------- data blocks
def _unshuffle(buf: bytes, item_size: int) -> bytes:
    if item_size <= 1:
        return buf
    n = len(buf) // item_size
    head = np.frombuffer(buf, np.uint8, n * item_size).reshape(item_size, n).T.tobytes()
    return head + buf[n * item_size:]


def _decompress(raw: bytes, spec: str) -> bytes:
    """spec: 'codec:uncompressed-size[:item-size]', codec e.g. zlib, lz4, lz4hc, zstd, zlib+sh."""
    parts = spec.split(":")
    codec, size = parts[0].lower(), int(parts[1])
    item = int(parts[2]) if len(parts) > 2 else 1
    shuffled = codec.endswith("+sh")
    codec = codec.replace("+sh", "")
    if codec == "zlib":
        out = zlib.decompress(raw)
    elif codec in ("lz4", "lz4hc"):
        try:
            import lz4.block
        except ImportError as exc:
            raise XisfError("this XISF file is LZ4-compressed; install the 'lz4' package "
                            "(start PlateSolver with run.bat)") from exc
        out = lz4.block.decompress(raw, uncompressed_size=size)
    elif codec == "zstd":
        try:
            import zstandard
        except ImportError as exc:
            raise XisfError("this XISF file is Zstandard-compressed; install the 'zstandard' package "
                            "(start PlateSolver with run.bat)") from exc
        out = zstandard.ZstdDecompressor().decompress(raw, max_output_size=size)
    else:
        raise XisfError(f"unsupported XISF compression '{codec}'")
    return _unshuffle(out, item) if shuffled else out


def _block(fh, el, header_end: int) -> bytes:
    """The bytes of a data block described by an element's location/compression attributes."""
    loc = el.get("location", "")
    if loc.startswith("attachment:"):
        _, pos, size = loc.split(":")
        fh.seek(int(pos))
        raw = fh.read(int(size))
        if len(raw) != int(size):
            raise XisfError("the XISF file is incomplete (data block cut short)")
    elif loc.startswith("inline:"):
        enc = loc.split(":")[1].lower()
        text = (el.text or "").strip()
        raw = base64.b64decode(text) if enc == "base64" else bytes.fromhex(text)
    elif loc == "embedded":
        data = el.find(NS + "Data")
        if data is None:
            raise XisfError("embedded XISF data block without a Data element")
        enc = data.get("encoding", "base64").lower()
        text = (data.text or "").strip()
        raw = base64.b64decode(text) if enc == "base64" else bytes.fromhex(text)
        if data.get("compression"):
            return _decompress(raw, data.get("compression"))
    else:
        raise XisfError(f"unsupported XISF data location '{loc}'")
    comp = el.get("compression")
    return _decompress(raw, comp) if comp else raw


# ---------------------------------------------------------------- reading
def _property_value(fh, el, header_end: int):
    t = el.get("type", "String")
    if t in VECTOR_TYPES:
        if el.get("location"):
            raw = _block(fh, el, header_end)
        else:
            raw = base64.b64decode((el.text or "").strip() or b"")
        order = ">" if el.get("byteOrder", "little") == "big" else "<"
        arr = np.frombuffer(raw, order + VECTOR_TYPES[t])
        if t.endswith("Matrix") and el.get("rows") and el.get("columns"):
            arr = arr.reshape(int(el.get("rows")), int(el.get("columns")))
        return arr.tolist()
    if t == "String":
        return el.get("value") if el.get("value") is not None else (el.text or "")
    if t == "Boolean":
        return str(el.get("value", "")).lower() in ("1", "true")
    v = el.get("value")
    if v is None:
        return el.text
    try:
        return float(v) if "." in v or "e" in v.lower() or t.startswith("Float") else int(v)
    except ValueError:
        return v


def read_xisf(path: Path) -> tuple[np.ndarray, dict, dict, dict]:
    """(pixels HxW or HxWxC, FITS keywords, XISF properties, image attributes) of the first image."""
    with open(path, "rb") as fh:
        sig = fh.read(8)
        if sig != b"XISF0100":
            raise XisfError("not an XISF file (or an XISF version this program doesn't know)")
        (hlen,) = struct.unpack("<I", fh.read(4))
        fh.read(4)
        header_end = 16 + hlen
        try:
            root = ET.fromstring(fh.read(hlen).decode("utf-8"))
        except ET.ParseError as exc:
            raise XisfError(f"damaged XISF header ({exc})") from exc
        img = root.find(NS + "Image")
        if img is None:
            raise XisfError("the XISF file contains no image")
        geom = [int(g) for g in img.get("geometry", "").split(":") if g]
        if len(geom) < 2:
            raise XisfError("XISF image without geometry")
        w, h = geom[0], geom[1]
        channels = geom[-1] if len(geom) > 2 else 1
        fmt = img.get("sampleFormat", "UInt16")
        if fmt not in SAMPLE_TYPES:
            raise XisfError(f"unsupported XISF sample format '{fmt}' (complex images are not supported)")
        order = ">" if img.get("byteOrder", "little") == "big" else "<"
        raw = _block(fh, img, header_end)
        arr = np.frombuffer(raw, order + SAMPLE_TYPES[fmt])
        expected = w * h * channels
        if arr.size < expected:
            raise XisfError("XISF image data is shorter than its geometry")
        arr = arr[:expected]
        planar = img.get("pixelStorage", "Planar").lower() != "normal"
        data = arr.reshape(channels, h, w) if planar else arr.reshape(h, w, channels)

        fits_kw: dict = {}
        for kw in img.iter(NS + "FITSKeyword"):
            name = kw.get("name", "").strip()
            if not name or name in ("COMMENT", "HISTORY", "END"):
                continue
            val = kw.get("value", "").strip()
            if val.startswith("'") and val.endswith("'") and len(val) >= 2:
                val = val[1:-1].replace("''", "'").strip()
            elif val in ("T", "F"):
                val = val == "T"
            else:
                try:
                    val = float(val) if any(c in val for c in ".eE") else int(val)
                except ValueError:
                    pass
            fits_kw.setdefault(name, val)
        props: dict = {}
        for parent in (img, root.find(NS + "Metadata")):
            if parent is None:
                continue
            for p in parent.findall(NS + "Property"):
                pid = p.get("id", "")
                if pid and pid != "PixInsight:ProcessingHistory":
                    try:
                        props[pid] = _property_value(fh, p, header_end)
                    except Exception:
                        pass
        attrs = dict(img.attrib)
        attrs["_planar"] = planar
    return data, fits_kw, props, attrs


def to_image(data: np.ndarray, attrs: dict) -> np.ndarray:
    """Planar (C, H, W) or normal (H, W, C) samples -> contiguous float32 0..1, HxW or HxWx3.

    Built channel by channel into one output array, so a large file needs little more memory than
    its own size twice.
    """
    planar = attrs.get("_planar", True)
    channels = data.shape[0] if planar else data.shape[2]
    h, w = (data.shape[1], data.shape[2]) if planar else (data.shape[0], data.shape[1])
    n = 3 if channels >= 3 else 1
    out = np.empty((h, w, n), np.float32)
    if data.dtype.kind == "f":
        lo, hi = 0.0, 1.0
        if attrs.get("bounds"):
            try:
                lo, hi = (float(x) for x in attrs["bounds"].split(":"))
            except ValueError:
                pass
        scale, offset = (1.0 / (hi - lo), lo) if hi > lo else (1.0, 0.0)
    else:
        scale, offset = 1.0 / float(np.iinfo(data.dtype).max), 0.0
    for c in range(n):
        plane = data[c] if planar else data[..., c]
        dst = out[..., c]
        dst[...] = plane
        if offset:
            dst -= offset
        if scale != 1.0:
            dst *= scale
        np.clip(dst, 0.0, 1.0, out=dst)
    return out[..., 0] if n == 1 else out


def hints_and_header(fits_kw: dict, props: dict):
    """Solve hints from FITS keywords plus PixInsight/XISF properties."""
    h = hints_from_header(fits_kw)

    def num(key):
        v = props.get(key)
        try:
            return float(v) if v is not None and not isinstance(v, (list, tuple)) else None
        except (TypeError, ValueError):
            return None
    # a stored plate solution: its centre and scale are the best hints there are
    ref = props.get("PCL:AstrometricSolution:ReferenceCelestialCoordinates")
    if isinstance(ref, list) and len(ref) >= 2:
        h.ra_deg, h.dec_deg = float(ref[0]) % 360.0, float(ref[1])
        h.source["ra"] = h.source["dec"] = "PixInsight plate solution"
    elif not h.has_position and num("Observation:Center:RA") is not None and num("Observation:Center:Dec") is not None:
        h.ra_deg, h.dec_deg = num("Observation:Center:RA") % 360.0, num("Observation:Center:Dec")
        h.source["ra"] = h.source["dec"] = "Observation:Center"
    m = props.get("PCL:AstrometricSolution:LinearTransformationMatrix")
    if isinstance(m, list) and len(m) == 2 and all(isinstance(r, list) and len(r) == 2 for r in m):
        det = abs(m[0][0] * m[1][1] - m[0][1] * m[1][0])
        if det > 0:
            h.pixel_scale_arcsec, h.source["scale"] = math.sqrt(det) * 3600.0, "PixInsight plate solution"
    if not h.pixel_scale_arcsec:
        cd = [fits_kw.get(k) for k in ("CD1_1", "CD1_2", "CD2_1", "CD2_2")]
        if all(isinstance(v, (int, float)) for v in cd) and abs(cd[0] * cd[3] - cd[1] * cd[2]) > 0:
            h.pixel_scale_arcsec = math.sqrt(abs(cd[0] * cd[3] - cd[1] * cd[2])) * 3600.0
            h.source["scale"] = "CD matrix"
        elif isinstance(fits_kw.get("CDELT2"), (int, float)) and fits_kw.get("CDELT2"):
            h.pixel_scale_arcsec, h.source["scale"] = abs(fits_kw["CDELT2"]) * 3600.0, "CDELT2"
    fl = num("Instrument:Telescope:FocalLength")            # metres in XISF
    if not h.focal_length_mm and fl and fl > 0:
        h.focal_length_mm, h.source["focal_length"] = fl * 1000.0 if fl < 50 else fl, "Instrument:Telescope:FocalLength"
    px = num("Instrument:Sensor:XPixelSize")                # micrometres
    if not h.pixel_size_um and px and px > 0:
        h.pixel_size_um, h.source["pixel_size"] = px, "Instrument:Sensor:XPixelSize"
    header = {k: v for k, v in fits_kw.items()}
    cam = props.get("Instrument:Camera:Name")
    if cam and not header.get("INSTRUME"):
        header["INSTRUME"] = cam
    for key, name in (("Observation:Object:Name", "OBJECT"), ("XISF:CreatorApplication", "Software"),
                      ("XISF:CreationTime", "DateTime")):
        if props.get(key) and name not in header:
            header[name] = props[key]
    return h, header


class XisfLoader(ImageLoader):
    plugin_id = "xisf"
    name = "XISF (PixInsight)"
    format_name = "XISF (PixInsight)"
    description = ("Reads PixInsight's XISF images: integer or floating-point, mono or colour, compressed or not. "
                   "FITS keywords and PixInsight's metadata (camera, focal length, pixel size, a stored plate "
                   "solution) are used as solving hints.")
    extensions = (".xisf",)
    priority = 15

    def load(self, path: Path) -> ImageData:
        raw, fits_kw, props, attrs = read_xisf(path)
        itemsize = raw.dtype.itemsize
        data = to_image(raw, attrs)
        del raw
        hints, header = hints_and_header(fits_kw, props)
        notes = []
        if hints.source.get("scale") == "PixInsight plate solution":
            notes.append("The file contains a PixInsight plate solution; it is used as an exact hint and the "
                         "image is re-solved in seconds")
        lum = data.mean(axis=2) if data.ndim == 3 else data
        sample = lum[::8, ::8]
        linear = float(np.median(sample)) < 0.06 and itemsize >= 2
        fmt = attrs.get("sampleFormat", "")
        depth = {"UInt8": "8-bit", "UInt16": "16-bit", "UInt32": "32-bit int", "Float32": "32-bit float",
                 "Float64": "64-bit float"}.get(fmt, fmt)
        if attrs.get("compression"):
            depth += f", {attrs['compression'].split(':')[0]} compressed"
        return ImageData(path=path, format="XISF", data=data, is_linear=linear, bit_depth=depth,
                         header=header, hints=hints, notes=notes)
