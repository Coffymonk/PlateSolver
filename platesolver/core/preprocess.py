# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Solving aids: a cleaned-up copy of the image used only for plate solving.

Images that were not flat-field corrected (vignetting, dust donuts), have strong gradients
(light pollution, moonlight) or were not dark-calibrated (hot pixels) can make the solver's star
detection fail even though plenty of stars are visible. The prepared copy has:

* a flattened background: a smooth background model is divided out, like a synthetic flat,
* isolated hot pixels replaced by the median of their neighbours.

Positions are unchanged, so a solution found on the copy is valid for the original image.
numpy only.
"""
from __future__ import annotations

import dataclasses
import math

import numpy as np

from platesolver.core.models import ImageData
from platesolver.core.settings import BOOL, CHOICE, INT, SettingField, SettingsSection


class SolvePrepSettings(SettingsSection):
    section_id = "solve_prep"
    name = "Solving aids"
    description = ("Help for images that have plenty of stars but still fail to solve. The solvers can be given "
                   "a cleaned-up copy of the image with its background flattened (for images that were not "
                   "flat-field corrected, or have strong gradients) and hot pixels removed (for images without "
                   "dark frames). Your image and its display are never changed.")

    def settings_schema(self) -> list[SettingField]:
        return [
            SettingField("flatten", "Flatten the background for solving", CHOICE, "auto", choices=[
                ("auto", "Automatic: at once if the background is uneven, otherwise as a second try"),
                ("retry", "Only as a second try when solving fails"),
                ("always", "Always"),
                ("never", "Never")]),
            SettingField("uneven_percent", "Background counts as uneven above", INT, 20, minimum=2, maximum=200,
                         step=5, suffix=" %", help="How much the sky background may vary across the image "
                                                    "before it is flattened straight away (Automatic mode)."),
            SettingField("hot_pixels", "Remove hot pixels in the solving copy", BOOL, True,
                         help="Isolated single bright pixels can be mistaken for stars."),
        ]


# --------------------------------------------------------------------------- background
def _interp_grid(grid: np.ndarray, shape: tuple[int, int], cell_h: float, cell_w: float) -> np.ndarray:
    h, w = shape
    gh, gw = grid.shape
    yc = (np.arange(gh) + 0.5) * cell_h
    xc = (np.arange(gw) + 0.5) * cell_w
    rows = np.empty((h, gw), dtype=np.float32)
    ys = np.arange(h)
    for j in range(gw):
        rows[:, j] = np.interp(ys, yc, grid[:, j])
    out = np.empty((h, w), dtype=np.float32)
    xs = np.arange(w)
    for i in range(h):
        out[i] = np.interp(xs, xc, rows[i])
    return out


def background_model(lum: np.ndarray, cells: int = 24) -> tuple[np.ndarray, float]:
    """Smooth sky background (stars and small nebulae ignored) and how uneven it is.

    Returns (background at full size, unevenness) where unevenness is the spread of the
    background (10th to 90th percentile) relative to its median, e.g. 0.35 = 35 %.
    """
    a = np.asarray(lum, dtype=np.float32)
    h, w = a.shape
    n_y = max(4, min(cells, h // 16))
    n_x = max(4, min(int(round(cells * w / max(h, 1))), w // 16))
    ch, cw = h // n_y, w // n_x
    blocks = a[: n_y * ch, : n_x * cw].reshape(n_y, ch, n_x, cw).transpose(0, 2, 1, 3).reshape(n_y, n_x, -1)
    if blocks.shape[-1] > 4096:   # a random subset is plenty for a median
        idx = np.random.default_rng(0).choice(blocks.shape[-1], 4096, replace=False)
        blocks = blocks[..., idx]
    med = np.median(blocks, axis=-1)
    mad = np.median(np.abs(blocks - med[..., None]), axis=-1) * 1.4826 + 1e-9
    clipped = np.where(np.abs(blocks - med[..., None]) < 2.5 * mad[..., None], blocks, np.nan)
    grid = np.nanmedian(clipped, axis=-1).astype(np.float32)
    grid = np.where(np.isfinite(grid), grid, med)
    # 3x3 median of the grid removes cells dominated by a big star or a small bright nebula
    padded = np.pad(grid, 1, mode="edge")
    stack = np.stack([padded[dy:dy + n_y, dx:dx + n_x] for dy in range(3) for dx in range(3)])
    grid = np.median(stack, axis=0)
    bg = _interp_grid(grid, (h, w), ch, cw)
    level = float(np.median(grid))
    # 10th-90th percentile: a galaxy or small nebula covering a few cells doesn't count as unevenness.
    # Relative to at least 5 % of full scale, so a processed image with a black sky doesn't give
    # huge percentages from tiny differences.
    spread = float(np.percentile(grid, 90) - np.percentile(grid, 10))
    unevenness = spread / max(level, 0.05)
    return bg, unevenness


def flatten(lum: np.ndarray) -> tuple[np.ndarray, float]:
    """Divide out the background (a synthetic flat). Returns (flattened, unevenness before)."""
    a = np.asarray(lum, dtype=np.float32)
    bg, uneven = background_model(a)
    level = float(np.median(bg))
    if level <= 1e-9:   # (nearly) black background: subtract instead of divide
        return (a - bg + max(level, 0.0)).astype(np.float32), uneven
    safe = np.maximum(bg, 0.05 * level)
    return (a / safe * level).astype(np.float32), uneven


def remove_hot_pixels(lum: np.ndarray, sigma: float = 8.0, chunk: int = 512) -> tuple[np.ndarray, int]:
    """Replace isolated hot pixels with the median of their 8 neighbours. Returns (cleaned, count)."""
    a = np.asarray(lum, dtype=np.float32)
    h, w = a.shape
    if h < 3 or w < 3:
        return a, 0
    sample = a[::4, ::4]
    noise = float(np.median(np.abs(sample - np.median(sample)))) * 1.4826 + 1e-9
    out = a.copy()
    count = 0
    padded = np.pad(a, 1, mode="edge")
    offsets = [(dy, dx) for dy in range(3) for dx in range(3) if (dy, dx) != (1, 1)]
    for y0 in range(0, h, chunk):
        y1 = min(h, y0 + chunk)
        neigh = np.stack([padded[y0 + dy: y1 + dy, dx: dx + w] for dy, dx in offsets])
        med = np.median(neigh, axis=0)
        brightest = neigh.max(axis=0)
        centre = a[y0:y1]
        # much brighter than every neighbour, and the neighbours themselves are dark -> not a star
        hot = (centre > med + sigma * noise) & (brightest < med + 0.25 * (centre - med))
        out[y0:y1][hot] = med[hot]
        count += int(hot.sum())
    return out, count


def prepared_copy(image: ImageData, hot_pixels: bool = True) -> tuple[ImageData, str]:
    """A mono copy of the image for solving, with flattened background (and hot pixels removed)."""
    lum = image.luminance()
    notes = []
    if hot_pixels:
        lum, n = remove_hot_pixels(lum)
        if n:
            notes.append(f"{n:,} hot pixels removed")
    flat, uneven = flatten(lum)
    notes.insert(0, f"background flattened (it varied {uneven * 100:.0f} %)")
    copy = dataclasses.replace(image, data=flat, header_wcs=None, notes=list(image.notes))
    return copy, ", ".join(notes)


def unevenness(image: ImageData) -> float:
    """Quick measure on a reduced copy (for deciding and for display)."""
    lum = image.luminance()
    f = max(1, math.ceil(max(lum.shape) / 1200))
    if f > 1:
        h, w = (lum.shape[0] // f) * f, (lum.shape[1] // f) * f
        lum = lum[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3))
    return background_model(lum)[1]
