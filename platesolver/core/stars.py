# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Quick star count, used to warn before solving an image that has (almost) no stars.

Plate solvers match star patterns, so a starless image (e.g. processed with StarNet or
StarXTerminator) cannot be solved. This counts compact point sources:

* bright enough: well above the local background noise,
* a local maximum,
* not a hot pixel: the neighbouring pixels are lit too (a star is spread over a few pixels),
* compact: the light has dropped clearly a few pixels away (nebula structure is broad).

numpy only, works on a reduced copy, typically well under a second.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from platesolver.core.settings import BOOL, INT, SettingField, SettingsSection


class StarCheckSettings(SettingsSection):
    section_id = "star_check"
    name = "Star check"
    description = ("Before solving, PlateSolver counts the stars in the image. Plate solving needs stars, so an "
                   "image with very few (for example a starless image from StarNet or StarXTerminator) will "
                   "almost certainly fail. Images that already contain a plate solution are not checked.")

    def settings_schema(self) -> list[SettingField]:
        return [
            SettingField("enabled", "Check for stars before solving", BOOL, True),
            SettingField("min_stars", "Warn when fewer stars than", INT, 15, minimum=1, maximum=500, step=5,
                         help="Solvers usually need at least 10–20 stars. Raise it to be warned earlier."),
            SettingField("batch_skip", "In batch mode, skip images with too few stars", BOOL, True,
                         help="When off, they are tried anyway (batch mode never stops to ask)."),
        ]


@dataclass
class StarCount:
    count: int
    noise: float
    reduced_by: int


def _block_background(a: np.ndarray, block: int = 32) -> np.ndarray:
    """Smooth background: medians of blocks, interpolated bilinearly back to full size."""
    h, w = a.shape
    bh, bw = max(1, h // block), max(1, w // block)
    crop = a[: bh * block, : bw * block].reshape(bh, block, bw, block)
    med = np.median(crop, axis=(1, 3))
    yc = (np.arange(bh) + 0.5) * block
    xc = (np.arange(bw) + 0.5) * block
    rows = np.empty((h, bw), dtype=np.float32)
    ys = np.arange(h)
    for j in range(bw):
        rows[:, j] = np.interp(ys, yc, med[:, j])
    out = np.empty((h, w), dtype=np.float32)
    xs = np.arange(w)
    for i in range(h):
        out[i] = np.interp(xs, xc, rows[i])
    return out


def count_stars(lum: np.ndarray, max_size: int = 1600, sigma: float = 6.0, max_candidates: int = 20000) -> StarCount:
    a = np.asarray(lum, dtype=np.float32)
    f = max(1, math.ceil(max(a.shape) / max_size))
    if f > 1:
        h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
        a = a[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3))
    if min(a.shape) < 32:
        return StarCount(0, 0.0, f)
    res = a - _block_background(a)
    sample = res[::2, ::2]
    noise = float(np.median(np.abs(sample - np.median(sample)))) * 1.4826
    noise = max(noise, float(np.std(sample)) * 0.05, 1e-6)

    # local maxima above the threshold (3x3 neighbourhood), away from the edges
    m = 6
    core = res[m:-m, m:-m]
    is_max = core > sigma * noise
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                is_max &= core >= res[m + dy: res.shape[0] - m + dy, m + dx: res.shape[1] - m + dx]
    ys, xs = np.nonzero(is_max)
    if len(ys) == 0:
        return StarCount(0, noise, f)
    peaks = core[ys, xs]
    if len(ys) > max_candidates:
        keep = np.argsort(peaks)[-max_candidates:]
        ys, xs, peaks = ys[keep], xs[keep], peaks[keep]
    ys, xs = ys + m, xs + m

    def ring(r):
        pts = [(r, 0), (-r, 0), (0, r), (0, -r)]
        if r > 1:
            d = int(round(r * 0.7071))
            pts += [(d, d), (d, -d), (-d, d), (-d, -d)]
        return np.stack([res[ys + dy, xs + dx] for dy, dx in pts], axis=1)

    near = ring(1).mean(axis=1)                 # lit neighbours -> not a hot pixel
    far = ring(4).max(axis=1)                   # dark a few pixels out in EVERY direction -> compact
    stars = (near > 0.15 * peaks) & (far < 0.5 * peaks) & (peaks - far > 3 * noise)
    return StarCount(int(stars.sum()), noise, f)


def starless_warning(name: str, found: int) -> str:
    return (f"Only <b>{found}</b> star{'s' if found != 1 else ''} found in <b>{name}</b>.<br><br>"
            "This looks like a <b>starless</b> image (for example after StarNet or StarXTerminator), or one with "
            "very few stars. Plate solving matches star patterns, so it will most likely fail.<br><br>"
            "Tip: solve the version of the image that still has its stars.<br><br>"
            "Try to solve it anyway?")
