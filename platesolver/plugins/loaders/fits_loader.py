# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""FITS images (.fits, .fit, .fts), mono, colour or raw one-shot-colour."""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np

from platesolver.core.formatting import parse_sexagesimal
from platesolver.core.imaging import debayer_superpixel, normalize, to_hwc
from platesolver.core.interfaces import ImageLoader
from platesolver.core.models import ImageData, SolveHints
from platesolver.core.settings import BOOL, CHOICE, SettingField


def _num(header, *keys):
    for k in keys:
        v = header.get(k)
        if v is None or v == "":
            continue
        try:
            return float(v), k
        except (TypeError, ValueError):
            continue
    return None, None


def hints_from_header(header) -> SolveHints:
    """Pointing and optics hints from the usual capture-software keywords."""
    h = SolveHints()
    ra, k = _num(header, "RA", "RA_OBJ", "CRVAL1")
    if ra is not None:
        h.ra_deg, h.source["ra"] = ra % 360.0, k
    elif header.get("OBJCTRA"):
        hrs = parse_sexagesimal(header["OBJCTRA"])
        if hrs is not None:
            h.ra_deg, h.source["ra"] = (hrs * 15.0) % 360.0, "OBJCTRA"
    dec, k = _num(header, "DEC", "DEC_OBJ", "CRVAL2")
    if dec is not None and -90 <= dec <= 90:
        h.dec_deg, h.source["dec"] = dec, k
    elif header.get("OBJCTDEC"):
        d = parse_sexagesimal(header["OBJCTDEC"])
        if d is not None:
            h.dec_deg, h.source["dec"] = d, "OBJCTDEC"
    if not h.has_position:
        h.ra_deg = h.dec_deg = None
    fl, k = _num(header, "FOCALLEN", "FOCAL")
    if fl and fl > 0:
        h.focal_length_mm, h.source["focal_length"] = fl, k
    px, k = _num(header, "XPIXSZ", "PIXSIZE1", "PIXSIZE")
    if px and px > 0:
        h.pixel_size_um, h.source["pixel_size"] = px, k
    sc, k = _num(header, "SCALE", "PIXSCALE", "SECPIX")
    if sc and sc > 0:
        h.pixel_scale_arcsec, h.source["scale"] = sc, k
    return h


def flip_wcs_vertically(wcs, height: int):
    """Same sky mapping for an image whose rows have been reversed."""
    w = wcs.deepcopy()
    w.wcs.crpix[1] = height + 1 - w.wcs.crpix[1]
    if w.wcs.has_cd():
        w.wcs.cd[:, 1] *= -1
    else:
        w.wcs.pc[:, 1] *= -1
    if w.sip is not None:
        # with v' = -v about the reference pixel: A'_pq = A_pq (-1)^q and B'_pq = -B_pq (-1)^q (same for AP/BP)
        import numpy as np
        from astropy.wcs import Sip

        def flip(m, sign):
            if m is None:
                return None
            m = np.array(m, float)
            q = np.arange(m.shape[1])[None, :]
            return sign * m * (-1.0) ** q
        s = w.sip
        w.sip = Sip(flip(s.a, 1), flip(s.b, -1), flip(s.ap, 1), flip(s.bp, -1), w.wcs.crpix)
    w.wcs.set()
    return w


def header_wcs(header, height: int, flipped: bool):
    try:
        from astropy.wcs import WCS
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if not str(header.get("CTYPE1", "")).startswith("RA"):
                return None
            w = WCS(header, naxis=2)
        if not w.has_celestial:
            return None
        return flip_wcs_vertically(w, height) if flipped else w
    except Exception:
        return None


class FitsLoader(ImageLoader):
    plugin_id = "fits"
    name = "FITS"
    format_name = "FITS"
    description = "Reads FITS files from capture and stacking software, including raw colour (Bayer) frames."
    extensions = (".fits", ".fit", ".fts")
    priority = 10

    def settings_schema(self):
        return [
            SettingField("row_order", "Row order", CHOICE, "auto", choices=[
                ("auto", "Automatic (use ROWORDER keyword)"),
                ("bottom-up", "Bottom-up (FITS standard)"),
                ("top-down", "Top-down"),
            ], help="Controls which way up the image is shown. Automatic follows the ROWORDER "
                    "keyword and otherwise assumes the FITS standard (first row at the bottom)."),
            SettingField("debayer", "Show raw colour frames in colour", BOOL, True,
                         help="Frames with a BAYERPAT keyword are debayered for display."),
        ]

    def load(self, path: Path) -> ImageData:
        from astropy.io import fits

        with fits.open(path, memmap=False) as hdul:
            hdu = next((h for h in hdul if h.data is not None and getattr(h.data, "ndim", 0) >= 2), None)
            if hdu is None:
                raise ValueError("No image data in this FITS file")
            header = hdu.header.copy()
            raw = np.asarray(hdu.data)
        bitpix = header.get("BITPIX")
        notes = []

        raw = to_hwc(raw)
        if raw.ndim == 3 and raw.shape[2] != 3:
            raw = raw[..., 0]
        bayer = str(header.get("BAYERPAT", "")).strip().upper()
        data = normalize(raw)
        if raw.ndim == 2 and bayer and self.setting("debayer"):
            try:
                data = debayer_superpixel(data, bayer, int(header.get("XBAYROFF", 0) or 0),
                                          int(header.get("YBAYROFF", 0) or 0))
                notes.append(f"Raw colour frame ({bayer}) shown with a simple debayer")
            except ValueError as exc:
                notes.append(str(exc))

        order = self.setting("row_order", "auto")
        if order == "auto":
            flip = str(header.get("ROWORDER", "BOTTOM-UP")).strip().upper() != "TOP-DOWN"
        else:
            flip = order == "bottom-up"
        if flip:
            data = data[::-1]
        data = np.ascontiguousarray(data, dtype=np.float32)

        hdr = {k: header[k] for k in header.keys() if k not in ("", "COMMENT", "HISTORY")}
        return ImageData(
            path=path, format="FITS", data=data, is_linear=True,
            bit_depth={8: "8-bit", 16: "16-bit", 32: "32-bit int", -32: "32-bit float",
                       -64: "64-bit float"}.get(bitpix, str(bitpix)),
            header=hdr, header_wcs=header_wcs(header, data.shape[0], flip),
            hints=hints_from_header(header), notes=notes, rows_flipped=flip,
        )
