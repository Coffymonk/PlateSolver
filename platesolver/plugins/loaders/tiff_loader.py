# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""TIFF images (.tif, .tiff), 8/16/32-bit, mono or colour."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from platesolver.core.imaging import normalize, to_hwc
from platesolver.core.interfaces import ImageLoader
from platesolver.core.models import ImageData, SolveHints
from platesolver.core.settings import BOOL, SettingField


class TiffLoader(ImageLoader):
    plugin_id = "tiff"
    name = "TIFF"
    format_name = "TIFF"
    description = ("Reads 8, 16 and 32-bit TIFF files, e.g. exports from stacking software. Camera EXIF data, "
                   "if present, gives the image scale.")
    extensions = (".tif", ".tiff")
    priority = 20

    def settings_schema(self):
        return [
            SettingField("linear_16bit", "Treat 16/32-bit TIFFs as unstretched (linear)", BOOL, True,
                         help="Linear images are auto-stretched for display. Turn off if your "
                              "TIFFs are already processed and look too bright."),
        ]

    def load(self, path: Path) -> ImageData:
        header: dict = {}
        try:
            import tifffile
            with tifffile.TiffFile(path) as tif:
                page = tif.pages[0]
                raw = page.asarray()
                for tag in ("Software", "ImageDescription", "DateTime", "Artist"):
                    t = page.tags.get(tag)
                    if t is not None and isinstance(t.value, str) and len(t.value) < 2000:
                        header[tag] = t.value
        except ImportError:
            from PIL import Image
            with Image.open(path) as im:
                raw = np.asarray(im)
        raw = to_hwc(np.asarray(raw))
        depth = raw.dtype.itemsize * 8
        is_float = np.issubdtype(raw.dtype, np.floating)
        data = np.ascontiguousarray(normalize(raw), dtype=np.float32)
        linear = bool(self.setting("linear_16bit")) and (depth > 8 or is_float)
        hints = SolveHints()
        try:   # photos converted to TIFF usually keep the camera's EXIF data
            from PIL import Image
            from platesolver.plugins.loaders.jpeg_loader import camera_hints, read_exif
            with Image.open(path) as im:
                exif_ifd = read_exif(im, header)
            if exif_ifd:
                hints = camera_hints(exif_ifd, data.shape[1], data.shape[0])
        except Exception:
            pass
        return ImageData(
            path=path, format="TIFF", data=data, is_linear=linear,
            bit_depth=f"{depth}-bit{' float' if is_float else ''}", header=header, hints=hints,
        )
