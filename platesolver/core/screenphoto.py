# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Photos of a screen or a print: stars shown on a tablet, monitor or paper, photographed with a phone.

Used when the profile "Photo of a screen or print" is active:

* the photo's own lens information (EXIF) is ignored – it describes the phone, while the stars' scale
  comes from the telescope that took the picture on the screen;
* lens distortion is not corrected (the distortion in such a photo comes from the viewing angle);
* when the photo doesn't solve as it is, a lightly softened copy is tried, which smooths moiré (the
  pattern a camera picks up from screen pixels) and JPEG blocks; the phone aids are not offered.
"""
from __future__ import annotations

import copy
import time

from platesolver.core.models import ImageData, SolveResult

COMPRESSED = ("JPEG", "JPG", "PNG", "HEIC", "HEIF", "TIFF")


def ignore_camera_scale(image: ImageData) -> None:
    """Forget the scale the photo's EXIF suggests; the solver then finds it (or uses the profile's field)."""
    h = image.hints
    had = h.scale_arcsec() is not None
    h.focal_length_mm = h.pixel_size_um = h.pixel_scale_arcsec = None
    for key in ("focal_length", "pixel_size", "scale"):
        h.source.pop(key, None)
    image.notes.append("Photo of a screen or print: the photo's own lens information is "
                       + ("ignored" if had else "not used") + "; the scale of the stars is found while solving")


def softened_copy(image: ImageData) -> ImageData:
    from platesolver.core.phoneaids import soften
    soft = ImageData(image.path, image.format, soften(image.luminance()), is_linear=image.is_linear,
                     header=dict(image.header))
    soft.hints = copy.deepcopy(image.hints)
    return soft


def solve_softened(pipeline, image: ImageData, ctx, attempts: list[str]) -> SolveResult | None:
    """Second try for a photo of a screen: a softened copy (moiré and JPEG blocks smoothed)."""
    if image.format.upper() not in COMPRESSED:
        return None
    solvers = [s for s in pipeline.solvers() if s.uses_pixels and s.is_available()[0]]
    if not solvers:
        return None
    ctx.log("Photo of a screen: trying a softened copy (smooths the screen's moiré pattern)…")
    soft = softened_copy(image)
    t0 = time.monotonic()
    for solver in solvers:
        ctx.check_cancel()
        label = f"{solver.name} (softened copy)"
        result = pipeline._run_solver(solver, soft, ctx)
        if result.success:
            attempts.append(f"{label}: solved in {result.elapsed_s:.1f} s")
            ctx.log(f"Solved by {label} in {result.elapsed_s:.1f} s")
            full = SolveResult.from_wcs(result.wcs, image.width, image.height, solver.plugin_id, solver.name)
            full.message = "Solved using a softened copy of the photo of a screen"
            full.elapsed_s = time.monotonic() - t0
            full.attempts = attempts
            return full
        attempts.append(f"{label}: {result.message}")
        ctx.log(f"{label}: {result.message}")
        if result.message == "upload declined":
            break
    return None
