# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Sky geometry helpers for overlays: densifying paths and projecting them onto the image."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np

from platesolver.core.models import SolveResult


def unit_vectors(ra_deg: np.ndarray, dec_deg: np.ndarray) -> np.ndarray:
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.column_stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


def to_radec(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    v = v / np.linalg.norm(v, axis=1, keepdims=True)
    return np.degrees(np.arctan2(v[:, 1], v[:, 0])) % 360.0, np.degrees(np.arcsin(np.clip(v[:, 2], -1, 1)))


def great_circle_path(points: Sequence[Sequence[float]], step_deg: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
    """Points joined by great-circle arcs (how star-to-star lines should look)."""
    ras, decs = [], []
    for (r1, d1), (r2, d2) in zip(points[:-1], points[1:]):
        a, b = unit_vectors(np.array([r1, r2]), np.array([d1, d2]))
        ang = math.acos(max(-1.0, min(1.0, float(a @ b))))
        n = max(2, int(math.degrees(ang) / step_deg) + 1)
        t = np.linspace(0.0, 1.0, n)
        if ang < 1e-9:
            seg = np.repeat(a[None, :], n, axis=0)
        else:
            seg = (np.sin((1 - t) * ang)[:, None] * a + np.sin(t * ang)[:, None] * b) / math.sin(ang)
        r, d = to_radec(seg)
        if ras:
            r, d = r[1:], d[1:]
        ras.append(r)
        decs.append(d)
    if not ras:
        return np.array([]), np.array([])
    return np.concatenate(ras), np.concatenate(decs)


def radec_path(points: Sequence[Sequence[float]], step_deg: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
    """Points joined by straight lines in RA/Dec (how constellation boundaries are defined)."""
    ras, decs = [], []
    for (r1, d1), (r2, d2) in zip(points[:-1], points[1:]):
        dr = ((r2 - r1 + 180.0) % 360.0) - 180.0
        n = max(2, int(max(abs(dr) * math.cos(math.radians((d1 + d2) / 2)), abs(d2 - d1)) / step_deg) + 1)
        t = np.linspace(0.0, 1.0, n)
        r = (r1 + t * dr) % 360.0
        d = d1 + t * (d2 - d1)
        if ras:
            r, d = r[1:], d[1:]
        ras.append(r)
        decs.append(d)
    if not ras:
        return np.array([]), np.array([])
    return np.concatenate(ras), np.concatenate(decs)


def angular_distance_deg(ra: np.ndarray, dec: np.ndarray, ra0: float, dec0: float) -> np.ndarray:
    v = unit_vectors(np.atleast_1d(ra), np.atleast_1d(dec))
    c = unit_vectors(np.array([ra0]), np.array([dec0]))[0]
    return np.degrees(np.arccos(np.clip(v @ c, -1.0, 1.0)))


def project(solution: SolveResult, ra: np.ndarray, dec: np.ndarray, margin: float = 0.5,
            max_angle_deg: float = 80.0) -> list[list[tuple[float, float]]]:
    """Sky path -> list of pixel polylines, split wherever the path leaves the (enlarged) image.

    `margin` enlarges the image by that fraction on every side so lines run cleanly to the edge.
    Points more than `max_angle_deg` from the image centre are dropped (the projection breaks down there).
    """
    if len(ra) == 0:
        return []
    ok = angular_distance_deg(ra, dec, solution.center_ra_deg, solution.center_dec_deg) < max_angle_deg
    x, y = solution.wcs.world_to_pixel_values(np.asarray(ra, float), np.asarray(dec, float))
    x, y = np.asarray(x, float), np.asarray(y, float)
    w, h = solution.width, solution.height
    mx, my = w * margin, h * margin
    ok &= np.isfinite(x) & np.isfinite(y) & (x > -mx) & (x < w + mx) & (y > -my) & (y < h + my)
    lines, cur = [], []
    for xi, yi, good in zip(x, y, ok):
        if good:
            cur.append((float(xi), float(yi)))
        elif cur:
            if len(cur) > 1:
                lines.append(cur)
            cur = []
    if len(cur) > 1:
        lines.append(cur)
    return lines


def visible_points(lines: Iterable[list[tuple[float, float]]], width: int, height: int) -> list[tuple[float, float]]:
    return [(x, y) for line in lines for x, y in line if 0 <= x < width and 0 <= y < height]
