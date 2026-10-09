# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Saving results: object lists (CSV), plate solutions (.wcs files, FITS headers). No Qt here."""
from __future__ import annotations

import csv
import datetime
import math
from pathlib import Path
from typing import Sequence

from platesolver import APP_NAME, __version__
from platesolver.core.formatting import format_dec, format_ra
from platesolver.core.models import ImageData, SkyObject, SolveResult, light_left_text
from platesolver.core.settings import BOOL, CHOICE, INT, SettingField, SettingsSection


class ExportSettings(SettingsSection):
    section_id = "export"
    name = "Export"
    description = "Defaults for File › Export: the annotated image, the object list and the plate solution."

    def settings_schema(self) -> list[SettingField]:
        return [
            SettingField("image_format", "Image format", CHOICE, "png", choices=[
                ("png", "PNG (lossless, larger)"), ("jpg", "JPEG (smaller, for sharing)")]),
            SettingField("jpeg_quality", "JPEG quality", INT, 92, minimum=50, maximum=100, step=1),
            SettingField("image_size", "Image size", CHOICE, "full", choices=[
                ("full", "Full resolution"), ("4000", "At most 4000 pixels wide"),
                ("2000", "At most 2000 pixels wide (web / forums)"), ("1200", "At most 1200 pixels wide")]),
            SettingField("label_scale", "Label size in exports", INT, 100, minimum=25, maximum=400, step=10,
                         suffix=" %", help="100 % makes labels about as large, relative to the picture, as "
                                           "they look on a typical screen."),
            SettingField("caption", "Add a caption strip with coordinates, scale and field of view", BOOL, True),
            SettingField("only_filtered", "Only label objects that pass the list's search and filter", BOOL, True),
            SettingField("csv_separator", "CSV separator", CHOICE, "comma", choices=[
                ("comma", "Comma  ( , )  – international"),
                ("semicolon", "Semicolon  ( ; ) with decimal comma – Excel with Swedish/European settings")]),
        ]


# --------------------------------------------------------------------------- objects CSV
CSV_COLUMNS = ["name", "common_name", "type", "category", "ra_deg", "dec_deg", "ra_hms", "dec_dms", "magnitude",
               "size_arcmin", "size_minor_arcmin", "distance_ly", "distance_uncertainty_ly", "distance_method",
               "distance_source", "true_size_ly", "light_left", "x_px", "y_px", "catalogue", "wikipedia",
               "simbad", "aliases"]


def object_rows(objects: Sequence[SkyObject]) -> list[dict]:
    rows = []
    for o in objects:
        wiki = next((l.url for l in o.links if l.source == "Wikipedia"), "")
        simbad = next((l.url for l in o.links if l.source == "SIMBAD"), "")
        rows.append({
            "name": o.name, "common_name": o.common_name, "type": o.object_type, "category": o.category,
            "ra_deg": o.ra_deg, "dec_deg": o.dec_deg, "ra_hms": format_ra(o.ra_deg), "dec_dms": format_dec(o.dec_deg),
            "magnitude": o.magnitude, "size_arcmin": o.size_arcmin, "size_minor_arcmin": o.size_minor_arcmin,
            "distance_ly": o.distance.light_years if o.distance else None,
            "distance_uncertainty_ly": o.distance.uncertainty_ly if o.distance else None,
            "distance_method": o.distance.method if o.distance else "",
            "distance_source": o.distance.source if o.distance else "",
            "true_size_ly": o.physical_size_ly(), "light_left": light_left_text(o.light_travel_years()),
            "x_px": o.x, "y_px": o.y, "catalogue": o.catalog, "wikipedia": wiki, "simbad": simbad,
            "aliases": "; ".join(o.aliases),
        })
    return rows


def write_objects_csv(path: Path, objects: Sequence[SkyObject], decimals: int = 1,
                      separator: str = "comma") -> None:
    semi = separator == "semicolon"

    def fmt(v, key):
        if v is None:
            return ""
        if isinstance(v, float):
            if not math.isfinite(v):
                return ""
            if key in ("distance_ly", "distance_uncertainty_ly", "true_size_ly"):
                text = f"{v:.{decimals}f}"
            elif key in ("ra_deg", "dec_deg"):
                text = f"{v:.6f}"
            else:
                text = f"{v:.2f}"
            return text.replace(".", ",") if semi else text
        return str(v)

    with open(path, "w", newline="", encoding="utf-8-sig") as fh:   # BOM so Excel detects UTF-8
        w = csv.writer(fh, delimiter=";" if semi else ",")
        w.writerow(CSV_COLUMNS)
        for r in object_rows(objects):
            w.writerow([fmt(r[c], c) for c in CSV_COLUMNS])


# --------------------------------------------------------------------------- plate solution
WCS_KEYS_TO_CLEAR = ("CTYPE1", "CTYPE2", "CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2", "CD1_1", "CD1_2", "CD2_1",
                     "CD2_2", "CDELT1", "CDELT2", "CROTA1", "CROTA2", "PC1_1", "PC1_2", "PC2_1", "PC2_2",
                     "CUNIT1", "CUNIT2", "LONPOLE", "LATPOLE", "WCSAXES", "A_ORDER", "B_ORDER", "AP_ORDER",
                     "BP_ORDER", "EQUINOX", "RADESYS", "PLTSOLVD")


def file_orientation_wcs(solution: SolveResult, image: ImageData):
    """The solution in the pixel orientation of the original file (undoing the display flip for FITS)."""
    if image.rows_flipped:
        from platesolver.plugins.loaders.fits_loader import flip_wcs_vertically
        return flip_wcs_vertically(solution.wcs, image.height)
    return solution.wcs


def solution_header(solution: SolveResult, image: ImageData):
    """FITS header cards describing the solution (usable as a .wcs file or merged into a FITS file)."""
    from astropy.io import fits

    w = file_orientation_wcs(solution, image)
    hdr = fits.Header()
    hdr["WCSAXES"] = 2
    hdr["CTYPE1"] = "RA---TAN"
    hdr["CTYPE2"] = "DEC--TAN"
    hdr["EQUINOX"] = 2000.0
    hdr["RADESYS"] = "ICRS"
    hdr["CRPIX1"], hdr["CRPIX2"] = float(w.wcs.crpix[0]), float(w.wcs.crpix[1])
    hdr["CRVAL1"], hdr["CRVAL2"] = float(w.wcs.crval[0]), float(w.wcs.crval[1])
    cd = w.wcs.cd if w.wcs.has_cd() else (w.wcs.get_pc() * w.wcs.cdelt[:, None])
    hdr["CD1_1"], hdr["CD1_2"] = float(cd[0][0]), float(cd[0][1])
    hdr["CD2_1"], hdr["CD2_2"] = float(cd[1][0]), float(cd[1][1])
    hdr["CUNIT1"] = "deg"
    hdr["CUNIT2"] = "deg"
    if w.sip is not None:            # lens distortion (SIP polynomials)
        hdr["CTYPE1"], hdr["CTYPE2"] = "RA---TAN-SIP", "DEC--TAN-SIP"
        for name, m in (("A", w.sip.a), ("B", w.sip.b), ("AP", w.sip.ap), ("BP", w.sip.bp)):
            if m is None:
                continue
            hdr[f"{name}_ORDER"] = m.shape[0] - 1
            for p_ in range(m.shape[0]):
                for q in range(m.shape[1]):
                    if m[p_, q] != 0:
                        hdr[f"{name}_{p_}_{q}"] = float(m[p_, q])
    hdr["IMAGEW"] = image.width
    hdr["IMAGEH"] = image.height
    hdr["PLTSOLVD"] = (True, f"Plate solved by {APP_NAME} ({solution.solver_name})")
    hdr["SCALE"] = (round(solution.pixel_scale_arcsec, 5), "arcsec/pixel")
    hdr["HISTORY"] = (f"{APP_NAME} {__version__} solution by {solution.solver_name}, "
                      f"{datetime.datetime.now().isoformat(timespec='seconds')}")
    return hdr


def write_wcs_file(path: Path, solution: SolveResult, image: ImageData) -> Path:
    """A '.wcs' side-car file: a FITS header only, the same format astrometry.net and ASTAP produce."""
    from astropy.io import fits

    hdu = fits.PrimaryHDU(header=solution_header(solution, image))
    hdu.writeto(path, overwrite=True)
    return path


def write_solution_into_fits(path: Path, solution: SolveResult, image: ImageData) -> None:
    """Store the plate solution in the FITS file's own header (the image data is not touched)."""
    from astropy.io import fits

    new = solution_header(solution, image)
    with fits.open(path, mode="update", memmap=False) as hdul:
        hdu = next((h for h in hdul if h.data is not None and getattr(h.data, "ndim", 0) >= 2), hdul[0])
        hdr = hdu.header
        for key in list(hdr.keys()):
            if key in WCS_KEYS_TO_CLEAR or key.startswith(("A_", "B_", "AP_", "BP_")) and key[-1].isdigit():
                del hdr[key]
        for card in new.cards:
            if card.keyword == "HISTORY":
                hdr.add_history(card.value)
            elif card.keyword not in ("IMAGEW", "IMAGEH"):
                hdr[card.keyword] = (card.value, card.comment)
        hdul.flush()


def caption_lines(image: ImageData, solution: SolveResult, n_objects: int) -> list[str]:
    from platesolver.core.formatting import format_angle
    line1 = (f"{image.path.name}   ·   Centre RA {format_ra(solution.center_ra_deg)}  "
             f"Dec {format_dec(solution.center_dec_deg)}   ·   Field {format_angle(solution.fov_width_deg)} × "
             f"{format_angle(solution.fov_height_deg)}   ·   {solution.pixel_scale_arcsec:.2f}″/px   ·   "
             f"Up = {solution.rotation_deg:.1f}° E of N{' (mirrored)' if solution.mirrored else ''}")
    line2 = (f"{n_objects} objects labelled   ·   Solved with {solution.solver_name}   ·   "
             f"{APP_NAME} {__version__}   ·   Objects: SIMBAD (CDS) / OpenNGC")
    return [line1, line2]
