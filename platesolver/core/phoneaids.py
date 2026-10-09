# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Phone and wide-field aids: a last try for photos taken with a phone (or a camera lens) that don't solve.

Plate solvers expect an almost undistorted field. A phone's very wide lens stretches star patterns
more and more towards the edges, so the patterns there no longer match the catalogue. The centre of
the photo is almost undistorted, so the aids:

1. black out the foreground (trees, houses, the horizon) that would be mistaken for stars,
2. flatten the light-pollution gradient,
3. solve the central part only (by default the middle 50 %, then the middle 30 %),
4. combine 2 × 2 pixels of large images (clearer stars from noisy phone sensors, and faster),
5. map the solution back onto the whole photo, and if possible solve the whole photo again near
   the position found, for better accuracy at the edges.

Rotating the image would not help: the solvers' star patterns don't depend on rotation (or mirroring).
"""
from __future__ import annotations

import copy
import dataclasses
import math

import numpy as np

from platesolver.core.models import ImageData, SolveHints
from platesolver.core.settings import BOOL, CHOICE, SettingField, SettingsSection

PHONE_MAKES = ("apple", "google", "samsung", "huawei", "xiaomi", "oneplus", "oppo", "vivo", "sony mobile",
               "motorola", "nokia", "honor", "realme", "fairphone", "nothing", "asus")


class PhoneAidSettings(SettingsSection):
    section_id = "phone_aids"
    name = "Phone and wide-field aids"
    description = ("A last try for phone photos (and other very wide-field photos) that don't solve: the foreground "
                   "is blacked out, the background flattened, and only the less distorted centre of the photo is "
                   "solved; the solution is then extended to the whole photo. Used only when every normal attempt "
                   "has failed. Your image is never changed.")

    def settings_schema(self) -> list[SettingField]:
        return [
            SettingField("mode", "When nothing else solves the image", CHOICE, "ask", choices=[
                ("ask", "Ask whether it is a phone photo, then try the aids"),
                ("auto", "Try the aids automatically (also in batch mode)"),
                ("never", "Never use the aids")]),
            SettingField("centres", "Parts of the photo to solve", CHOICE, "50,30", choices=[
                ("100,50,30", "Whole photo (cleaned), then the middle 50 %, then the middle 30 %"),
                ("50,30", "The middle 50 %, then the middle 30 %"),
                ("60,40", "The middle 60 %, then the middle 40 %"),
                ("50", "Only the middle 50 %")],
                help="A smaller part is less distorted but holds fewer stars."),
            SettingField("mask_foreground", "Black out the foreground (trees, buildings, horizon)", BOOL, True,
                         help="Dark silhouettes and lit ground along the edges of the photo, with no stars in them."),
            SettingField("bin2", "Combine 2 × 2 pixels in large images", BOOL, True,
                         help="For parts larger than 2000 pixels, and for strongly compressed JPEGs: clearer stars "
                              "from noisy phone sensors and fewer false 'stars' from JPEG blocks; also faster. Never "
                              "below 600 pixels on the short side."),
            SettingField("refine", "Then solve a larger part near the position found", BOOL, True,
                         help="The whole photo, then the middle 75 %: a quick try each, now that position and scale "
                              "are known. Gives a more accurate solution towards the edges when it works; otherwise "
                              "the solution of the centre is extended to the whole photo."),
        ]


# --------------------------------------------------------------------------- is it a phone photo?
def phone_clue(image: ImageData) -> str:
    """A short reason to believe the photo comes from a phone, or '' when there is no clue."""
    make = str(image.header.get("Make") or "").strip()
    model = str(image.header.get("Camera") or image.header.get("Model") or "").strip()
    if make and any(make.lower().startswith(m) for m in PHONE_MAKES):
        return f"camera: {make} {model}".strip()
    if any(k in model.lower() for k in ("iphone", "pixel", "galaxy")):
        return f"camera: {model}"
    if image.format.upper() in ("HEIC", "HEIF"):
        return "a HEIC file, the format iPhones use"
    src = " ".join(str(v) for v in image.hints.source.values()).lower()
    if "full-frame equivalent" in src:
        return "it has no camera information, and your profile assumes a phone or camera lens"
    return ""


def question_text(image: ImageData, clue: str) -> str:
    why = f"It looks like a phone photo ({clue}). " if clue else ""
    return (f"<b>{image.path.name}</b> could not be solved.<br><br>{why}"
            "Was it taken with a <b>mobile phone</b>, or another camera with a very wide lens?<br><br>"
            "If so, PlateSolver can try its <b>phone and wide-field aids</b>: it blacks out the foreground, "
            "flattens the background and solves only the less distorted centre of the photo, then extends the "
            "solution to the whole photo. This takes a little longer. Your image is not changed.")


# --------------------------------------------------------------------------- image steps
def foreground_mask(lum: np.ndarray, block: int = 0) -> np.ndarray:
    """True where the photo shows foreground: starless dark silhouettes or lit ground touching an edge.

    Works on blocks of a reduced copy: a block is foreground when it has (almost) no stars and its
    brightness is far from that of the sky, and it is connected to the edge of the photo.
    """
    h, w = lum.shape
    f = max(1, math.ceil(max(h, w) / 800))
    small = lum[: (h // f) * f, : (w // f) * f].reshape(h // f, f, w // f, f).mean(axis=(1, 3)) if f > 1 else lum
    sh, sw = small.shape
    b = block or max(8, min(sh, sw) // 40)
    gh, gw = sh // b, sw // b
    if gh < 4 or gw < 4:
        return np.zeros((h, w), bool)
    cells = small[: gh * b, : gw * b].reshape(gh, b, gw, b).transpose(0, 2, 1, 3).reshape(gh, gw, b * b)
    med = np.median(cells, axis=2)
    # stars: pixels well above their block's median
    mad = np.median(np.abs(cells - med[..., None]), axis=2) * 1.4826
    noise = max(float(np.median(mad)), 1e-4)
    peaks = (cells > med[..., None] + 6 * noise).sum(axis=2)
    starry = peaks >= 2
    sky_cells = med[starry]
    if sky_cells.size < 4:
        return np.zeros((h, w), bool)
    sky = float(np.median(sky_cells))
    spread = float(np.median(np.abs(sky_cells - sky))) * 1.4826 + 2 * noise
    dark = med < sky - max(4 * spread, 0.35 * sky)
    lit = (med > sky + max(6 * spread, 0.5 * sky + 0.05)) & (peaks <= 1)
    candidate = (dark | lit) & (peaks <= 1)
    # keep only areas connected to the edge of the photo
    keep = np.zeros_like(candidate)
    stack = [(y, x) for y in range(gh) for x in range(gw)
             if candidate[y, x] and (y in (0, gh - 1) or x in (0, gw - 1))]
    for y, x in stack:
        keep[y, x] = True
    while stack:
        y, x = stack.pop()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < gh and 0 <= nx < gw and candidate[ny, nx] and not keep[ny, nx]:
                keep[ny, nx] = True
                stack.append((ny, nx))
    if keep.sum() < 0.02 * gh * gw:
        return np.zeros((h, w), bool)
    # grow by one block so tree tops and roof edges are covered too
    grown = keep.copy()
    grown[1:] |= keep[:-1]
    grown[:-1] |= keep[1:]
    grown[:, 1:] |= keep[:, :-1]
    grown[:, :-1] |= keep[:, 1:]
    grown &= candidate | keep | _neighbours(keep)
    cell = b * f
    full = np.repeat(np.repeat(grown, cell, axis=0), cell, axis=1)
    out = np.zeros((h, w), bool)
    out[: min(h, full.shape[0]), : min(w, full.shape[1])] = full[:h, :w]
    # the strip beyond the last whole block takes the value of the block next to it
    if full.shape[0] < h:
        out[full.shape[0]:, : full.shape[1]] = full[-1:, :w]
    if full.shape[1] < w:
        out[:, full.shape[1]:] = out[:, full.shape[1] - 1: full.shape[1]]
    return out


def _neighbours(m: np.ndarray) -> np.ndarray:
    n = np.zeros_like(m)
    n[1:] |= m[:-1]
    n[:-1] |= m[1:]
    n[:, 1:] |= m[:, :-1]
    n[:, :-1] |= m[:, 1:]
    return n


def soften(a: np.ndarray) -> np.ndarray:
    """A light blur (5-tap binomial, about 1 pixel) that hides JPEG block edges but keeps stars."""
    k = np.array([1, 4, 6, 4, 1], np.float32) / 16.0
    out = np.asarray(a, np.float32)
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (2, 2)
        p = np.pad(out, pad, mode="edge")
        n = out.shape[axis]
        out = sum(k[i] * np.take(p, np.arange(i, i + n), axis=axis) for i in range(5)).astype(np.float32)
    return out


def bin2(a: np.ndarray) -> np.ndarray:
    h, w = (a.shape[0] // 2) * 2, (a.shape[1] // 2) * 2
    return a[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3)).astype(np.float32)


@dataclasses.dataclass
class Variant:
    """A prepared copy of part of the photo, and how its pixels map back onto the whole photo."""
    image: ImageData
    x0: int            # left column of the part, in the whole photo
    y0: int            # top row
    factor: int        # 1, or 2 when 2 × 2 pixels were combined
    fraction: float    # 1.0 = whole photo, 0.5 = middle 50 %
    notes: str

    @property
    def label(self) -> str:
        part = "whole photo" if self.fraction >= 0.999 else f"middle {self.fraction * 100:.0f} %"
        return f"phone aids, {part}" + (", 2×2 binned" if self.factor > 1 else "")


def prepare_base(image: ImageData, mask_foreground: bool = True) -> tuple[np.ndarray, str]:
    """Mono copy with the foreground blacked out and the background flattened."""
    from platesolver.core.preprocess import flatten, remove_hot_pixels

    lum = image.luminance().astype(np.float32)
    notes = []
    lum, n = remove_hot_pixels(lum)
    if n:
        notes.append(f"{n:,} hot pixels removed")
    mask = foreground_mask(lum) if mask_foreground else None
    if mask is not None and mask.any():
        sky = float(np.median(lum[~mask])) if (~mask).any() else 0.0
        lum = lum.copy()
        lum[mask] = sky
        notes.append(f"foreground blacked out ({mask.mean() * 100:.0f} % of the photo)")
    if image.format.upper() in ("JPEG", "JPG", "HEIC", "HEIF"):
        lum = soften(lum)
        if heavily_compressed(image):
            lum = soften(lum)
            notes.append("strong JPEG compression smoothed")
        else:
            notes.append("JPEG blocks smoothed")
    flat, uneven = flatten(lum)
    if mask is not None and mask.any():
        flat[mask] = float(np.median(flat[~mask]))
    notes.insert(0, f"background flattened (it varied {uneven * 100:.0f} %)")
    return flat, ", ".join(notes)


def heavily_compressed(image: ImageData) -> bool:
    """A JPEG saved with very few bytes per pixel (e.g. sent through a messaging app): 8 × 8 blocks show."""
    if image.format.upper() not in ("JPEG", "JPG"):
        return False
    try:
        size = image.path.stat().st_size
    except OSError:
        return False
    return size / max(1, image.width * image.height) < 0.05


def make_variant(image: ImageData, base: np.ndarray, base_notes: str, fraction: float,
                 allow_bin: bool = True) -> Variant:
    h, w = base.shape
    ch, cw = max(32, int(round(h * fraction))), max(32, int(round(w * fraction)))
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    part = base[y0: y0 + ch, x0: x0 + cw]
    # ASTAP needs a reasonable number of pixels: never bin below about 600 pixels on the short side
    worth = max(part.shape) > 2000 or heavily_compressed(image)
    factor = 2 if allow_bin and worth and min(part.shape) >= 1200 else 1
    if factor > 1:
        part = bin2(part)
    hints = scaled_hints(image.hints, factor)
    if hints.source.get("scale", "").startswith("Settings"):
        # not "a guess from Settings" any more: ASTAP's any-scale retry only covers 10°–0.3° fields
        hints.source["scale"] = "phone aids, from " + hints.source["scale"]
    for k in ("focal_length", "pixel_size"):
        if str(hints.source.get(k, "")).startswith("Settings"):
            hints.source[k] = "phone aids, from " + hints.source[k]
    notes = base_notes + (f", middle {fraction * 100:.0f} %" if fraction < 0.999 else "") + \
        (", 2×2 pixels combined" if factor > 1 else "")
    copy_ = dataclasses.replace(image, data=np.ascontiguousarray(part, dtype=np.float32), header_wcs=None,
                                hints=hints, notes=list(image.notes))
    return Variant(copy_, x0, y0, factor, fraction, notes)


def scaled_hints(h: SolveHints, factor: int) -> SolveHints:
    """Hints for a copy whose pixels are `factor` times larger (cropping doesn't change the scale)."""
    new = copy.deepcopy(h)
    if factor == 1:
        return new
    if new.pixel_scale_arcsec:
        new.pixel_scale_arcsec *= factor
    elif new.pixel_size_um:
        new.pixel_size_um *= factor
    return new


# --------------------------------------------------------------------------- mapping back
def wcs_to_full(wcs, x0: float, y0: float, factor: int):
    """The WCS of a part (cropped at x0, y0 and binned by `factor`) turned into one for the whole photo.

    Whole-photo pixel = x0 + factor * part pixel + (factor - 1) / 2 (0-based, pixel centres).
    Distortion terms (SIP) are rescaled too.
    """
    w = wcs.deepcopy()
    a = float(factor)
    off = np.array([x0 + (a - 1) / 2.0, y0 + (a - 1) / 2.0])
    crpix0 = np.array(w.wcs.crpix, float) - 1.0                     # FITS is 1-based
    w.wcs.crpix = a * crpix0 + off + 1.0
    if w.wcs.has_cd():
        w.wcs.cd = np.array(w.wcs.cd) / a
    else:
        w.wcs.cdelt = np.array(w.wcs.cdelt) / a
    if w.sip is not None:
        from astropy.wcs import Sip

        def rescale(m, inverse=False):
            if m is None:
                return None
            m = np.array(m, float)
            out = np.zeros_like(m)
            for p in range(m.shape[0]):
                for q in range(m.shape[1]):
                    out[p, q] = m[p, q] * a ** (1 - p - q)
            return out
        s = w.sip
        w.sip = Sip(rescale(s.a), rescale(s.b), rescale(s.ap), rescale(s.bp), w.wcs.crpix)
    w.wcs.set()
    return w


def refine_hints(image: ImageData, centre_ra: float, centre_dec: float, scale_arcsec: float,
                 factor: int) -> SolveHints:
    """Hints for solving the whole photo again: the position and scale just found."""
    h = copy.deepcopy(image.hints)
    h.ra_deg, h.dec_deg = centre_ra, centre_dec
    h.pixel_scale_arcsec = scale_arcsec * factor
    h.source = dict(h.source, scale="the centre solve (phone aids)", ra="the centre solve (phone aids)")
    h.position_hint = h.position_hint or "the position found in the centre"
    h.position_exact = True
    return h
