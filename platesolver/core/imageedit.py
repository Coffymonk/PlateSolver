# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Crop and rotate the image shown in PlateSolver (the file itself is never changed).

The edited copy replaces the shown image until the original is restored: it is solved, labelled and
exported like any other image. Rotating and cropping don't change the scale of the stars (no resizing),
so the scale hints from the file and the profile stay valid; a stored plate solution (FITS/XISF header)
no longer fits the pixels and is dropped, so the edited view is solved again.

Edits are made in two steps that both work on the shown (display-oriented) pixels:

1. rotation: quarter turns (exact) and an optional straightening angle (bilinear, canvas enlarged so
   nothing is cut off; the new corners are filled with the sky background);
2. crop: a rectangle in the pixels of the rotated image.

`rotate_array` is used both for the preview (8-bit display pixels) and for the real data, with the same
geometry, so the crop rectangle drawn on the preview selects the same pixels.
"""
from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass

import numpy as np

from platesolver.core.models import ImageData

MIN_SIDE = 32          # smaller crops can't hold enough stars to solve


@dataclass
class Edit:
    quarter_turns: int = 0             # 90° steps clockwise
    angle_deg: float = 0.0             # straightening, positive = clockwise, -45…45
    crop: tuple[int, int, int, int] | None = None   # x, y, width, height in the rotated image

    @property
    def rotation_deg(self) -> float:
        return (self.quarter_turns % 4) * 90.0 + self.angle_deg

    def is_identity(self) -> bool:
        return self.quarter_turns % 4 == 0 and abs(self.angle_deg) < 1e-6 and self.crop is None


def _rotate_plane(plane: np.ndarray, angle_deg: float, fill: float) -> np.ndarray:
    """Rotate one 2-D float plane clockwise by angle_deg around its centre, enlarging the canvas."""
    from PIL import Image
    im = Image.fromarray(np.ascontiguousarray(plane, dtype=np.float32), mode="F")
    out = im.rotate(-angle_deg, resample=Image.BILINEAR, expand=True, fillcolor=float(fill))
    return np.asarray(out, dtype=np.float32)


def _fill_for(plane: np.ndarray, fill, ch: int = 0) -> float:
    if fill is None:
        return float(np.nanmedian(plane[::4, ::4]))       # the sky background, so the new corners look like sky
    return float(fill if np.isscalar(fill) else fill[min(ch, len(fill) - 1)])


def rotate_array(data: np.ndarray, quarter_turns: int, angle_deg: float,
                 fill: float | tuple | None = None) -> np.ndarray:
    """Rotate an image array (H, W) or (H, W, C) clockwise; quarter turns are exact."""
    from PIL import Image
    k = quarter_turns % 4
    out = np.rot90(data, -k, axes=(0, 1)) if k else data
    if abs(angle_deg) < 1e-6:
        return np.ascontiguousarray(out)
    if out.dtype == np.uint8 and out.ndim == 3 and out.shape[2] == 3:     # display pixels: fast path
        f = tuple(int(round(_fill_for(out[..., c], fill, c))) for c in range(3))
        im = Image.fromarray(np.ascontiguousarray(out), mode="RGB")
        return np.asarray(im.rotate(-angle_deg, resample=Image.BILINEAR, expand=True, fillcolor=f))
    dtype = out.dtype
    if out.ndim == 2:
        res = _rotate_plane(out, angle_deg, _fill_for(out, fill))
    else:
        res = np.stack([_rotate_plane(out[..., c], angle_deg, _fill_for(out[..., c], fill, c))
                        for c in range(out.shape[2])], axis=2)
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        res = np.clip(np.rint(res), info.min, info.max)
    return res.astype(dtype)


def clamp_crop(crop, width: int, height: int) -> tuple[int, int, int, int] | None:
    if crop is None:
        return None
    x, y, w, h = (int(round(v)) for v in crop)
    x0, y0 = max(0, min(width, x)), max(0, min(height, y))
    x1, y1 = max(0, min(width, x + w)), max(0, min(height, y + h))
    if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE:
        raise ValueError(f"The selection is too small (at least {MIN_SIDE} × {MIN_SIDE} pixels are needed).")
    if (x0, y0, x1 - x0, y1 - y0) == (0, 0, width, height):
        return None
    return x0, y0, x1 - x0, y1 - y0


def apply_array(data: np.ndarray, edit: Edit, fill=None) -> np.ndarray:
    out = rotate_array(data, edit.quarter_turns, edit.angle_deg, fill)
    if edit.crop is not None:
        x, y, w, h = edit.crop
        out = np.ascontiguousarray(out[y:y + h, x:x + w])
    return out


def describe(edit: Edit) -> str:
    parts = []
    rot = edit.rotation_deg % 360
    if rot > 180:
        rot -= 360
    if abs(rot) > 1e-6:
        parts.append(f"rotated {abs(rot):g}° {'clockwise' if rot > 0 else 'anticlockwise'}")
    if edit.crop is not None:
        parts.append(f"cropped to {edit.crop[2]} × {edit.crop[3]} px")
    return ", ".join(parts) if parts else "unchanged"


def edited_image(image: ImageData, edit: Edit) -> ImageData:
    """A new ImageData with the edit applied. Scale hints stay; a stored solution is dropped."""
    rotated = rotate_array(image.data, edit.quarter_turns, edit.angle_deg)
    edit = dataclasses.replace(edit, crop=clamp_crop(edit.crop, rotated.shape[1], rotated.shape[0]))
    data = rotated
    if edit.crop is not None:
        x, y, w, h = edit.crop
        data = np.ascontiguousarray(rotated[y:y + h, x:x + w])
    text = describe(edit)
    notes = list(image.notes) + [f"Edited view ({text}); the file itself is unchanged"]
    if image.header_wcs is not None:
        notes.append("The plate solution stored in the file doesn't fit the edited view; it is solved again")
    return dataclasses.replace(image, data=data.astype(np.float32, copy=False), header_wcs=None,
                               hints=copy.deepcopy(image.hints), notes=notes, rows_flipped=False,
                               star_count=None, background_unevenness=None, profile_note="")
