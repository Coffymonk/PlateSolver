# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""JPEG, PNG and HEIC/HEIF images (8-bit, already stretched). HEIC is the iPhone's photo format."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from platesolver.core.imaging import normalize, to_hwc
from platesolver.core.interfaces import ImageLoader
from platesolver.core.models import ImageData, SolveHints

EXIF_FOCAL_LENGTH = 37386
EXIF_FOCAL_35MM = 41989
EXIF_FOCAL_PLANE_XRES = 41486
EXIF_FOCAL_PLANE_UNIT = 41488      # 2 = inch, 3 = cm, 4 = mm, 5 = µm
EXIF_PIXEL_X = 40962
EXIF_PIXEL_Y = 40963
EXIF_IFD = 0x8769
UNIT_MM = {2: 25.4, 3: 10.0, 4: 1.0, 5: 0.001}


def _num(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


FULL_FRAME_DIAGONAL_MM = 43.2666


def camera_hints(exif_ifd: dict, width: int, height: int) -> SolveHints:
    """Image scale of a camera photo from its EXIF data.

    1. Focal length + sensor pixel size (from FocalPlaneXResolution), corrected if the
       picture was resized after it left the camera.
    2. Otherwise the 35 mm-equivalent focal length (what phones write), via the full-frame
       diagonal of 43.27 mm.
    """
    h = SolveHints()
    fl = _num(exif_ifd.get(EXIF_FOCAL_LENGTH))
    long_side = max(width, height)
    if fl:
        h.focal_length_mm = fl
        h.source["focal_length"] = "EXIF"
    xres = _num(exif_ifd.get(EXIF_FOCAL_PLANE_XRES))
    unit = UNIT_MM.get(int(exif_ifd.get(EXIF_FOCAL_PLANE_UNIT) or 2))
    orig_long = max(_num(exif_ifd.get(EXIF_PIXEL_X)) or 0, _num(exif_ifd.get(EXIF_PIXEL_Y)) or 0) or long_side
    if fl and xres and unit:
        px_um = unit / xres * 1000.0 * (orig_long / long_side)   # bigger pixels if the picture was shrunk
        if 0.5 < px_um < 60:
            h.pixel_size_um = px_um
            h.source["pixel_size"] = "EXIF (sensor resolution)"
            return h
    f35 = _num(exif_ifd.get(EXIF_FOCAL_35MM))
    if f35:
        # The 35 mm equivalent is defined by the diagonal (43.27 mm on full frame), so it is also right
        # for 4:3 phone pictures. Solvers want the scale at the centre of the picture (tangent
        # projection), not the average angle per pixel, which is up to 15 % smaller for wide lenses.
        import math
        pitch_mm = FULL_FRAME_DIAGONAL_MM / math.hypot(width, height)
        h.pixel_scale_arcsec = 206264.8 * pitch_mm / f35
        h.source["scale"] = f"EXIF ({f35:g} mm full-frame equivalent)"
    return h


def read_exif(im, header: dict) -> dict:
    """The EXIF block of an opened Pillow image (JPEG or TIFF); camera make/model go into header."""
    exif_ifd = {}
    try:
        exif = im.getexif()
        exif_ifd = dict(exif.get_ifd(EXIF_IFD))
        for tag in (EXIF_FOCAL_LENGTH, EXIF_FOCAL_35MM, EXIF_FOCAL_PLANE_XRES, EXIF_FOCAL_PLANE_UNIT):
            if tag not in exif_ifd and exif.get(tag):      # some TIFF writers put them in the main IFD
                exif_ifd[tag] = exif.get(tag)
        for tag, name in ((0x010F, "Make"), (0x0110, "Camera"), (0x0131, "Software"), (0x0132, "DateTime")):
            if exif.get(tag) and name not in header:
                header[name] = str(exif.get(tag)).strip()
    except Exception:
        pass
    return exif_ifd


HEIF_EXTENSIONS = (".heic", ".heif", ".hif")
_heif_ready = None


def enable_heif() -> bool:
    """Teach Pillow to open HEIC/HEIF files (needs the pillow-heif library)."""
    global _heif_ready
    if _heif_ready is None:
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
            _heif_ready = True
        except Exception:
            _heif_ready = False
    return _heif_ready


class JpegLoader(ImageLoader):
    plugin_id = "jpeg"
    name = "JPEG / PNG / HEIC"
    format_name = "JPEG / PNG / HEIC"
    description = ("Reads JPG, PNG and HEIC (iPhone) images. For camera photos the image scale is worked out from the EXIF "
                   "data (focal length and sensor, or the 35 mm-equivalent focal length).")
    extensions = (".jpg", ".jpeg", ".png") + HEIF_EXTENSIONS
    priority = 30

    def load(self, path: Path) -> ImageData:
        from PIL import Image, ImageOps

        if path.suffix.lower() in HEIF_EXTENSIONS and not enable_heif():
            raise ValueError("HEIC images need the pillow-heif library. Start PlateSolver with run.bat to "
                             "install it, or run: pip install pillow-heif")
        with Image.open(path) as im:
            fmt = im.format or path.suffix.upper().lstrip(".")
            hints = SolveHints()
            header = {}
            exif_ifd = read_exif(im, header)
            im = ImageOps.exif_transpose(im)
            try:
                hints = camera_hints(exif_ifd, im.width, im.height)
            except Exception:
                hints = SolveHints()
            if im.mode not in ("L", "RGB", "I;16", "I"):
                im = im.convert("RGB")
            raw = np.asarray(im)
        raw = to_hwc(raw)
        return ImageData(
            path=path, format="JPEG" if fmt in ("JPEG", "MPO") else ("HEIC" if fmt in ("HEIF", "AVIF") else fmt),
            data=np.ascontiguousarray(normalize(raw), dtype=np.float32),
            is_linear=False, bit_depth=f"{raw.dtype.itemsize * 8}-bit",
            header=header, hints=hints,
        )
