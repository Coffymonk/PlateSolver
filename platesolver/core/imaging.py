# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Pixel helpers: normalising, debayering and the display auto-stretch."""
from __future__ import annotations

import numpy as np


def normalize(data: np.ndarray) -> np.ndarray:
    """Any numeric array -> float32 in 0..1 (integer types by their full range)."""
    if np.issubdtype(data.dtype, np.integer):
        info = np.iinfo(data.dtype)
        out = (data.astype(np.float32) - info.min) / float(info.max - info.min)
        return out
    out = np.nan_to_num(data.astype(np.float32, copy=False), nan=0.0, posinf=0.0, neginf=0.0)
    lo, hi = float(out.min()), float(out.max())
    if hi > lo and (lo < 0.0 or hi > 1.0):
        out = (out - lo) / (hi - lo)
    return out


def to_hwc(data: np.ndarray) -> np.ndarray:
    """(3,H,W) or (4,H,W) planar -> (H,W,3). Drops alpha. Mono stays (H,W)."""
    if data.ndim == 3:
        if data.shape[0] in (3, 4) and data.shape[2] not in (3, 4):
            data = np.moveaxis(data, 0, -1)
        if data.shape[2] == 4:
            data = data[..., :3]
        elif data.shape[2] == 1:
            data = data[..., 0]
        elif data.shape[2] == 2:
            data = data[..., 0]
    return data


BAYER_OFFSETS = {  # position of (R, B) in the 2x2 cell as (row, col)
    "RGGB": ((0, 0), (1, 1)),
    "BGGR": ((1, 1), (0, 0)),
    "GRBG": ((0, 1), (1, 0)),
    "GBRG": ((1, 0), (0, 1)),
}


def debayer_superpixel(raw: np.ndarray, pattern: str, x_off: int = 0, y_off: int = 0) -> np.ndarray:
    """Simple colour from a raw one-shot-colour frame, same size as the input.

    Each 2x2 cell becomes one colour pixel which is repeated 2x2, so pixel
    positions (and therefore the WCS) stay unchanged.
    """
    pattern = pattern.strip().upper()
    if pattern not in BAYER_OFFSETS:
        raise ValueError(f"Unknown Bayer pattern {pattern}")
    h, w = raw.shape
    src = raw[y_off % 2:, x_off % 2:]
    hh, ww = (src.shape[0] // 2) * 2, (src.shape[1] // 2) * 2
    src = src[:hh, :ww].astype(np.float32)
    (rr, rc), (br, bc) = BAYER_OFFSETS[pattern]
    gr1, gc1 = rr, 1 - rc
    gr2, gc2 = 1 - rr, rc
    r = src[rr::2, rc::2]
    b = src[br::2, bc::2]
    g = (src[gr1::2, gc1::2] + src[gr2::2, gc2::2]) / 2.0
    rgb = np.stack([r, g, b], axis=-1)
    rgb = rgb.repeat(2, axis=0).repeat(2, axis=1)
    out = np.empty((h, w, 3), dtype=np.float32)
    out[...] = np.median(rgb[::8, ::8], axis=(0, 1))
    out[y_off % 2:y_off % 2 + hh, x_off % 2:x_off % 2 + ww] = rgb
    return out


def _mtf(m: float, x: np.ndarray | float):
    """Midtones transfer function (as used by PixInsight's screen stretch)."""
    return ((m - 1.0) * x) / ((2.0 * m - 1.0) * x - m)


def auto_stretch(data: np.ndarray, target_bkg: float = 0.20, shadows_clip: float = -2.8,
                 linked: bool = False) -> np.ndarray:
    """Non-linear display stretch for linear data in 0..1. Returns float32 0..1."""
    def params(ch: np.ndarray) -> tuple[float, float]:
        sample = ch[:: max(1, ch.shape[0] // 512), :: max(1, ch.shape[1] // 512)]
        med = float(np.median(sample))
        mad = float(np.median(np.abs(sample - med))) * 1.4826
        c0 = min(max(med + shadows_clip * mad, 0.0), 1.0)
        x = (med - c0) / (1.0 - c0) if c0 < 1.0 else 0.5
        m = float(_mtf(target_bkg, x)) if 0.0 < x < 1.0 else 0.5
        return c0, m

    def apply(ch: np.ndarray, c0: float, m: float) -> np.ndarray:
        x = np.clip((ch - c0) / max(1e-6, 1.0 - c0), 0.0, 1.0)
        return np.clip(_mtf(m, x), 0.0, 1.0).astype(np.float32)

    if data.ndim == 2:
        return apply(data, *params(data))
    if linked:
        c0, m = params(data.mean(axis=2))
        return apply(data, c0, m)
    out = np.empty_like(data, dtype=np.float32)
    for i in range(data.shape[2]):
        out[..., i] = apply(data[..., i], *params(data[..., i]))
    return out


def to_display_rgb8(data: np.ndarray, is_linear: bool, stretch: bool, target_bkg: float) -> np.ndarray:
    """Image data (0..1 float) -> contiguous (H, W, 3) uint8 ready for the screen."""
    img = auto_stretch(data, target_bkg) if (is_linear and stretch) else np.clip(data, 0, 1)
    if img.ndim == 2:
        img = np.repeat(img[..., None], 3, axis=2)
    return np.ascontiguousarray((img * 255.0 + 0.5).astype(np.uint8))
