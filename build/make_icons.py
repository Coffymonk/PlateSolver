# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Draws the PlateSolver app icon and writes it in every format the builds need.

    python build/make_icons.py

Writes build/icons/platesolver.png (1024 px), .ico (Windows), .icns (macOS) and
platesolver/data/icon.png (window icon). The icon is drawn here, so there is no image file to license.

    python build/make_icons.py --logos

also writes the logo for websites and documents in several sizes to build/dist/logo/ (transparent PNGs).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "icons"
S = 1024
LOGO_SIZES = (2048, 1024, 512, 300, 64)


def draw(size: int = S) -> Image.Image:
    """The icon at `size` pixels; every size shows exactly the same design (drawn, not enlarged)."""
    S = size
    k = size / 1024.0

    def w(px: float) -> int:                  # a line width that scales with the size
        return max(1, int(round(px * k)))
    rng = np.random.default_rng(7)
    yy, xx = np.mgrid[0:S, 0:S]
    r = np.hypot(xx - S / 2, yy - S / 2) / (S / 2)
    # night-sky disc: deep blue centre fading to near black, with a soft nebula glow
    base = np.zeros((S, S, 4), np.float32)
    base[..., 0] = 12 + 18 * (1 - r)
    base[..., 1] = 18 + 30 * (1 - r)
    base[..., 2] = 40 + 70 * (1 - r)
    glow = np.exp(-(((xx - S * 0.62) / (S * 0.22)) ** 2 + ((yy - S * 0.40) / (S * 0.16)) ** 2))
    base[..., 0] += 150 * glow
    base[..., 1] += 40 * glow
    base[..., 2] += 70 * glow
    base[..., 3] = np.where(r <= 0.94, 255, 0)
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGBA")
    d = ImageDraw.Draw(img)
    for _ in range(170):
        x, y = rng.uniform(0.12, 0.88, 2) * S
        if math.hypot(x - S / 2, y - S / 2) > S * 0.43:
            continue
        rad = (1.5 + rng.power(6) * 6) * k
        b = int(140 + rng.random() * 115)
        d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=(b, b, min(255, b + 20), 255))
    # a bright star with spikes
    cx, cy = S * 0.38, S * 0.60
    for L, wdt in ((S * 0.16, w(6)), (S * 0.10, w(10))):
        d.line([cx - L, cy, cx + L, cy], fill=(220, 235, 255, 255), width=wdt)
        d.line([cx, cy - L, cx, cy + L], fill=(220, 235, 255, 255), width=wdt)
    d.ellipse([cx - 22 * k, cy - 22 * k, cx + 22 * k, cy + 22 * k], fill=(255, 255, 255, 255))
    img = img.filter(ImageFilter.GaussianBlur(1.2 * k))
    d = ImageDraw.Draw(img)
    # plate-solving reticle: ring with corner brackets
    accent = (90, 169, 255, 255)
    d.ellipse([S * 0.06, S * 0.06, S * 0.94, S * 0.94], outline=accent, width=w(26))
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x0, y0 = S / 2 + sx * S * 0.20, S / 2 + sy * S * 0.20
        d.line([x0, y0, x0 + sx * S * 0.10, y0], fill=(255, 179, 71, 255), width=w(22))
        d.line([x0, y0, x0, y0 + sy * S * 0.10], fill=(255, 179, 71, 255), width=w(22))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img = draw()
    img.save(OUT / "platesolver.png")
    img.save(OUT / "platesolver.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(OUT / "platesolver.icns")
    img.resize((256, 256), Image.LANCZOS).save(ROOT / "platesolver" / "data" / "icon.png")
    print("Icons written to", OUT)


def logos(folder: Path = ROOT / "build" / "dist" / "logo") -> list[Path]:
    """The logo for a website and documents: transparent PNGs, each drawn at its own size."""
    folder.mkdir(parents=True, exist_ok=True)
    big = draw(2048)
    out = []
    for size in LOGO_SIZES:
        # large sizes are drawn directly; small ones are reduced from the 2048 px drawing, which keeps
        # the fine stars smooth instead of letting them disappear
        img = draw(size) if size >= 512 else big.resize((size, size), Image.LANCZOS)
        path = folder / f"PlateSolver-logo-{size}.png"
        img.save(path, optimize=True)
        out.append(path)
    return out


if __name__ == "__main__":
    import sys
    main()
    if "--logos" in sys.argv:
        for p in logos():
            print("Logo:", p)
