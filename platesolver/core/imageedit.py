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
2. perspective (optional): four corners of a photographed screen in the rotated image are mapped onto a
   rectangle ("squared up"), so a screen photographed a little from the side gets its true shape back;
3. crop: a rectangle in the pixels of the (squared-up) image, and areas to hide inside it.

`rotate_array` is used both for the preview (8-bit display pixels) and for the real data, with the same
geometry, so the crop rectangle drawn on the preview selects the same pixels.
"""
from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass, field

import numpy as np

from platesolver.core.models import ImageData

MIN_SIDE = 32          # smaller crops can't hold enough stars to solve


@dataclass
class Edit:
    quarter_turns: int = 0             # 90° steps clockwise
    angle_deg: float = 0.0             # straightening, positive = clockwise, -45…45
    crop: tuple[int, int, int, int] | None = None   # x, y, width, height in the rotated image
    hide: list = field(default_factory=list)          # areas (x, y, w, h) in the rotated image to fill with sky
    quad: tuple | None = None          # screen corners (TL, TR, BR, BL) as (x, y) in the rotated image
    aspect: float | None = None        # true width / height of the screen (None: from its longest sides)

    @property
    def rotation_deg(self) -> float:
        return (self.quarter_turns % 4) * 90.0 + self.angle_deg

    def is_identity(self) -> bool:
        return (self.quarter_turns % 4 == 0 and abs(self.angle_deg) < 1e-6 and self.crop is None
                and not self.hide and self.quad is None)


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


# --------------------------------------------------------------------------- perspective
def _homography(src, dst) -> np.ndarray:
    """3 x 3 matrix mapping the four points src onto dst (projective transform)."""
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b += [u, v]
    h = np.linalg.solve(np.array(A, float), np.array(b, float))
    return np.append(h, 1.0).reshape(3, 3)


def square_size(quad, aspect: float | None = None) -> tuple[int, int]:
    """Width and height of the rectangle a quad is mapped onto: its longest top/bottom side, and its longest
    left/right side or, when the true proportions (width / height) are known, the width divided by them."""
    q = np.asarray(quad, float)
    w = max(np.hypot(*(q[1] - q[0])), np.hypot(*(q[2] - q[3])))
    h = w / aspect if aspect else max(np.hypot(*(q[3] - q[0])), np.hypot(*(q[2] - q[1])))
    return max(1, int(round(w))), max(1, int(round(h)))


def _square_matrix(quad, aspect: float | None = None) -> np.ndarray:
    w, h = square_size(quad, aspect)
    return _homography(quad, [(0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)])


def screen_aspect(quad, width: int, height: int, focal35: float | None = None) -> float | None:
    """The true width / height of a rectangle photographed as `quad` (TL, TR, BR, BL) in a picture of
    width x height pixels (Zhang & He 2007, "Whiteboard scanning and image enhancement").

    The camera's focal length decides how much a side is foreshortened; it comes from the 35 mm-equivalent
    focal length in the photo's EXIF, or, without it, from the quad itself when its perspective is strong
    enough. None when neither works (then the longest sides are used)."""
    c = np.array([width / 2.0, height / 2.0])
    tl, tr, br, bl = (np.r_[np.asarray(p, float) - c, 1.0] for p in quad)
    m1, m2, m3, m4 = tl, tr, bl, br
    try:
        k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
        k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
    except (ZeroDivisionError, FloatingPointError):
        return None
    n2, n3 = k2 * m2 - m1, k3 * m3 - m1
    diag = float(np.hypot(width, height))
    f = None
    if focal35:
        f = focal35 * diag / 43.2666                      # pixels, from the full-frame diagonal
    elif abs(n2[2] * n3[2]) > 1e-9:
        f2 = -(n2[0] * n3[0] + n2[1] * n3[1]) / (n2[2] * n3[2])
        if f2 > 0 and 0.4 * diag < np.sqrt(f2) < 4 * diag:
            f = float(np.sqrt(f2))
    if f is None:
        return None
    num = (n2[0] ** 2 + n2[1] ** 2) / f ** 2 + n2[2] ** 2
    den = (n3[0] ** 2 + n3[1] ** 2) / f ** 2 + n3[2] ** 2
    if not (num > 0 and den > 0):
        return None
    r = float(np.sqrt(num / den))
    return r if 0.2 < r < 5 else None


def map_points(points, quad, aspect: float | None = None) -> np.ndarray:
    """Points (x, y) of the rotated image in the squared-up image."""
    M = _square_matrix(quad, aspect)
    p = np.c_[np.asarray(points, float).reshape(-1, 2), np.ones(len(points))] @ M.T
    return p[:, :2] / p[:, 2:3]


def map_rect(rect, quad, width: int, height: int, aspect: float | None = None):
    """A rectangle (x, y, w, h) of the rotated image as the bounding rectangle of its squared-up corners,
    limited to the squared-up image; None if nothing of it is left."""
    x, y, w, h = rect
    p = map_points([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], quad, aspect)
    x0, y0 = max(0, int(np.floor(p[:, 0].min()))), max(0, int(np.floor(p[:, 1].min())))
    x1, y1 = min(width, int(np.ceil(p[:, 0].max()))), min(height, int(np.ceil(p[:, 1].max())))
    return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def keystone(quad) -> float:
    """How far a quad is from a rectangle: the largest relative difference of opposite sides (0.03 = 3 %)."""
    q = np.asarray(quad, float)
    top, bottom = np.hypot(*(q[1] - q[0])), np.hypot(*(q[2] - q[3]))
    left, right = np.hypot(*(q[3] - q[0])), np.hypot(*(q[2] - q[1]))
    return float(max(abs(top - bottom) / max(top, bottom), abs(left - right) / max(left, right)))


def square_up(data: np.ndarray, quad, aspect: float | None = None) -> np.ndarray:
    """Map the quad (TL, TR, BR, BL in pixels of `data`) onto a rectangle (bilinear). (H, W) or (H, W, C).
    Stars squashed by the viewing angle get their round shape and true spacing back."""
    from PIL import Image
    w, h = square_size(quad, aspect)
    inv = np.linalg.inv(_square_matrix(quad, aspect))          # PIL wants output -> input
    inv = inv / inv[2, 2]
    coeffs = tuple(float(v) for v in inv.flatten()[:8])
    if data.dtype == np.uint8 and data.ndim == 3 and data.shape[2] == 3:     # display pixels: fast path
        im = Image.fromarray(np.ascontiguousarray(data), mode="RGB")
        return np.asarray(im.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BILINEAR))
    dtype = data.dtype

    def plane(p):
        im = Image.fromarray(np.ascontiguousarray(p, dtype=np.float32), mode="F")
        return np.asarray(im.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BILINEAR,
                                       fillcolor=float(np.nanmedian(p[::4, ::4]))), dtype=np.float32)
    res = plane(data) if data.ndim == 2 else np.stack([plane(data[..., c]) for c in range(data.shape[2])], axis=2)
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
    if edit.quad is not None:
        out = square_up(out, edit.quad, edit.aspect)
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
    if edit.quad is not None:
        parts.append(f"perspective corrected ({keystone(edit.quad) * 100:.1f} %)")
    if edit.crop is not None:
        parts.append(f"cropped to {edit.crop[2]} × {edit.crop[3]} px")
    if edit.hide:
        n = len(edit.hide)
        parts.append(f"{n} area{'s' if n != 1 else ''} hidden")
    return ", ".join(parts) if parts else "unchanged"


def hide_areas(data: np.ndarray, areas) -> np.ndarray:
    """Fill rectangles (x, y, w, h) with the sky colour around each, so app text and buttons aren't seen
    as stars. Works on (H, W) and (H, W, C) arrays; returns a copy."""
    out = np.array(data, copy=True)
    h, w = out.shape[:2]
    for (x, y, aw, ah) in areas or []:
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(w, int(x + aw)), min(h, int(y + ah))
        if x1 <= x0 or y1 <= y0:
            continue
        m = max(4, int(0.5 * max(x1 - x0, y1 - y0)))          # a ring around the area gives the sky colour
        rx0, ry0, rx1, ry1 = max(0, x0 - m), max(0, y0 - m), min(w, x1 + m), min(h, y1 + m)
        around = np.ones((ry1 - ry0, rx1 - rx0), bool)
        around[y0 - ry0:y1 - ry0, x0 - rx0:x1 - rx0] = False          # only the pixels around the area
        ring = data[ry0:ry1, rx0:rx1][around] if around.any() else data[y0:y1, x0:x1].reshape(-1, *out.shape[2:])
        sky = np.median(ring, axis=0)
        out[y0:y1, x0:x1] = sky
    return out


def edited_image(image: ImageData, edit: Edit) -> ImageData:
    """A new ImageData with the edit applied. Scale hints stay; a stored solution is dropped."""
    rotated = rotate_array(image.data, edit.quarter_turns, edit.angle_deg)
    if edit.quad is not None:
        rotated = square_up(rotated, edit.quad, edit.aspect)
    edit = dataclasses.replace(edit, crop=clamp_crop(edit.crop, rotated.shape[1], rotated.shape[0]))
    data = rotated
    hide = list(edit.hide or [])
    if edit.crop is not None:
        x, y, w, h = edit.crop
        data = np.ascontiguousarray(data[y:y + h, x:x + w])
        hide = [(hx - x, hy - y, hw, hh) for (hx, hy, hw, hh) in hide]     # into the cropped image
    if hide:
        data = hide_areas(data, hide)          # after cropping, so only sky inside the selection is used
    text = describe(edit)
    notes = list(image.notes) + [f"Edited view ({text}); the file itself is unchanged"]
    if image.header_wcs is not None:
        notes.append("The plate solution stored in the file doesn't fit the edited view; it is solved again")
    return dataclasses.replace(image, data=data.astype(np.float32, copy=False), header_wcs=None,
                               hints=copy.deepcopy(image.hints), notes=notes, rows_flipped=False,
                               star_count=None, background_unevenness=None, profile_note="")
