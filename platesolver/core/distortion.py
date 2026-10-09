# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Measuring and correcting lens distortion, so labels sit on their stars all the way to the corners.

A plate solution maps pixels to the sky with an ideal-lens model (TAN). Wide lenses, phone lenses in
particular, bend the image slightly more towards the edges, and a solution found only from the centre
of the photo (the phone aids) also carries small scale and rotation errors outwards. This module:

1. finds the stars in the image,
2. pairs them with catalogue stars, starting in the centre where the solution is right and working
   outwards ring by ring, so each pairing is unambiguous,
3. fits a TAN projection plus SIP distortion polynomial (the FITS standard for lens distortion) to the
   pairs, refitting as more of the image is covered.

Only numpy is used (no SciPy).
"""
from __future__ import annotations

import dataclasses
import math

import numpy as np

from platesolver.core.settings import ACTION, CHOICE, SettingField, SettingsSection


class DistortionSettings(SettingsSection):
    section_id = "distortion"
    name = "Star catalogue and lens distortion"
    description = ("After solving a wide-field or phone photo, PlateSolver can compare the stars in the image with "
                   "its own star catalogue and correct for lens distortion, so the labels sit on their stars all the "
                   "way to the corners. A Hipparcos catalogue (stars to magnitude 8) is built in; you can download a "
                   "deeper Tycho-2 catalogue here.")

    def settings_schema(self) -> list[SettingField]:
        from platesolver.core import starcatalog as sc
        return [
            SettingField("correct", "Correct lens distortion", CHOICE, "wide", choices=[
                ("wide", "For fields wider than 5° (camera lenses and phones)"),
                ("phone", "Only after the phone and wide-field aids"),
                ("never", "Never")],
                help="Telescope fields have hardly any distortion, so they are left as they are."),
            SettingField("depth", "Catalogue to download", CHOICE, "9", choices=[
                (f"{k:g}", f"Tycho-2 to magnitude {k:g} ({v})") for k, v in sc.DEPTHS.items()],
                help="Deeper catalogues help longer lenses (50–135 mm). Magnitude 8 or 9 is plenty for phones."),
            SettingField("update", "Update the star catalogue", ACTION,
                         help="Downloads Tycho-2 from CDS VizieR (Strasbourg) down to the magnitude chosen above.",
                         action=lambda values, log: sc.download(float(values.get("depth") or 9), log),
                         status=sc.describe),
            SettingField("builtin", "Use the built-in catalogue again", ACTION,
                         help="Removes the downloaded catalogue.",
                         action=lambda values, log: sc.use_builtin(), status=lambda: ""),
        ]


# --------------------------------------------------------------------------- finding stars
def detect_stars(lum: np.ndarray, max_stars: int = 800, sigma: float = 5.0):
    """Star positions (x, y) and brightness, brightest first, with sub-pixel centroids.

    Large images are reduced first (2 × 2 or more), so the bloated, slightly trailed stars of phone and
    camera photos become compact points. Stars are ranked by their total light, not their peak, so
    bright (saturated, flat-topped) stars come first, as they should.
    """
    from platesolver.core.stars import _block_background

    full = np.asarray(lum, np.float32)
    f = max(1, int(math.ceil(max(full.shape) / 2000)))
    if f > 1:
        h2, w2 = (full.shape[0] // f) * f, (full.shape[1] // f) * f
        a = full[:h2, :w2].reshape(h2 // f, f, w2 // f, f).mean(axis=(1, 3))
    else:
        a = full
    res = a - _block_background(a)
    # the noise differs across the photo (vignetted corners, flattened copies, the Milky Way): judge each
    # candidate against the noise of its own neighbourhood
    blk = 48
    gh, gw = max(1, a.shape[0] // blk), max(1, a.shape[1] // blk)
    cells = res[: gh * blk, : gw * blk].reshape(gh, blk, gw, blk).transpose(0, 2, 1, 3).reshape(gh, gw, -1)
    local = np.median(np.abs(cells - np.median(cells, axis=2)[..., None]), axis=2) * 1.4826
    local = np.maximum(local, max(float(np.median(local)) * 0.2, 1e-6))
    m = 5
    core = res[m:-m, m:-m]
    is_max = core > 0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                is_max &= core >= res[m + dy: res.shape[0] - m + dy, m + dx: res.shape[1] - m + dx]
    ys, xs = np.nonzero(is_max)
    if len(ys) == 0:
        return np.empty(0), np.empty(0), np.empty(0)
    ys, xs = ys + m, xs + m
    ln = local[np.minimum(ys // blk, gh - 1), np.minimum(xs // blk, gw - 1)]
    strong = res[ys, xs] > sigma * ln
    ys, xs = ys[strong], xs[strong]
    peaks = res[ys, xs]
    # compact sources only: much dimmer 4 pixels out in every direction (not nebula detail or JPEG edges)
    far = np.max(np.stack([res[np.clip(ys + dy, 0, a.shape[0] - 1), np.clip(xs + dx, 0, a.shape[1] - 1)]
                           for dy, dx in ((4, 0), (-4, 0), (0, 4), (0, -4), (3, 3), (-3, -3), (3, -3), (-3, 3))]),
                 axis=0)
    keep = far < 0.5 * peaks
    ys, xs = ys[keep], xs[keep]
    # centroid and total light in a 7 x 7 box, re-centred once on the first estimate
    off = np.arange(-3, 4)
    cy, cx = ys.astype(float), xs.astype(float)
    for _ in range(2):
        iy, ix = np.round(cy).astype(int), np.round(cx).astype(int)
        wy = np.clip(iy[:, None] + off[None, :], 0, a.shape[0] - 1)
        wx = np.clip(ix[:, None] + off[None, :], 0, a.shape[1] - 1)
        box = np.clip(res[wy[:, :, None], wx[:, None, :]], 0, None)
        tot = box.sum(axis=(1, 2)) + 1e-12
        cy = iy + (box.sum(axis=2) * off[None, :]).sum(axis=1) / tot
        cx = ix + (box.sum(axis=1) * off[None, :]).sum(axis=1) / tot
    order = np.argsort(tot)[::-1]
    cx, cy, tot = cx[order], cy[order], tot[order]
    # one detection per star: drop detections within 3 pixels of a brighter one
    taken: set[tuple[int, int]] = set()
    single = []
    for k in range(len(cx)):
        cell = (int(cy[k]) // 3, int(cx[k]) // 3)
        if any((cell[0] + i, cell[1] + j) in taken for i in (-1, 0, 1) for j in (-1, 0, 1)):
            continue
        taken.add(cell)
        single.append(k)
        if len(single) >= max_stars:
            break
    single = np.array(single, int)
    cx, cy, tot = cx[single], cy[single], tot[single]
    if f > 1:                       # back to whole-image pixels
        cx, cy = cx * f + (f - 1) / 2.0, cy * f + (f - 1) / 2.0
    return cx.astype(float), cy.astype(float), tot.astype(float)


# --------------------------------------------------------------------------- the TAN + SIP fit
def tan_project(ra, dec, ra0, dec0):
    """Standard coordinates (degrees) of sky positions on the plane touching the sky at (ra0, dec0)."""
    a, d = np.radians(ra), np.radians(dec)
    a0, d0 = math.radians(ra0), math.radians(dec0)
    cosc = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(a - a0)
    xi = np.cos(d) * np.sin(a - a0) / cosc
    eta = (math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc
    return np.degrees(xi), np.degrees(eta)


def tan_deproject(xi, eta, ra0, dec0):
    x, y = math.radians(xi), math.radians(eta)
    d0 = math.radians(dec0)
    rho = math.hypot(x, y)
    if rho == 0:
        return ra0, dec0
    c = math.atan(rho)
    dec = math.asin(math.cos(c) * math.sin(d0) + y * math.sin(c) * math.cos(d0) / rho)
    ra = ra0 + math.degrees(math.atan2(x * math.sin(c), rho * math.cos(d0) * math.cos(c) - y * math.sin(d0) * math.sin(c)))
    return ra % 360.0, math.degrees(dec)


def _terms(degree: int, minimum: int = 0):
    return [(p, t - p) for t in range(minimum, degree + 1) for p in range(t, -1, -1)]


def _design(u, v, terms):
    return np.column_stack([u ** p * v ** q for p, q in terms])


def fit_tan_sip(px, py, ra, dec, width: int, height: int, degree: int, ra0: float, dec0: float):
    """A TAN-SIP WCS (reference pixel at the image centre) fitted to star pairs. Returns (wcs, residuals px)."""
    from astropy.wcs import WCS, Sip

    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    u, v = np.asarray(px, float) - cx, np.asarray(py, float) - cy
    s = math.hypot(cx, cy) or 1.0
    un, vn = u / s, v / s
    terms = _terms(degree)
    M = _design(un, vn, terms)
    for _ in range(4):
        xi, eta = tan_project(ra, dec, ra0, dec0)
        cxi = np.linalg.lstsq(M, xi, rcond=None)[0]
        ceta = np.linalg.lstsq(M, eta, rcond=None)[0]
        if abs(cxi[0]) < 1e-9 and abs(ceta[0]) < 1e-9:
            break
        ra0, dec0 = tan_deproject(cxi[0], ceta[0], ra0, dec0)    # move the tangent point to the centre
    coef = {pq: (cxi[i] / s ** sum(pq), ceta[i] / s ** sum(pq)) for i, pq in enumerate(terms)}
    cd = np.array([[coef[(1, 0)][0], coef[(0, 1)][0]], [coef[(1, 0)][1], coef[(0, 1)][1]]])
    cdi = np.linalg.inv(cd)
    w = WCS(naxis=2)
    w.wcs.crpix = [cx + 1.0, cy + 1.0]
    w.wcs.crval = [ra0, dec0]
    w.wcs.cd = cd
    if degree >= 2:
        order = degree
        A = np.zeros((order + 1, order + 1))
        B = np.zeros((order + 1, order + 1))
        for (p, q), (hx, hy) in coef.items():
            if p + q >= 2:
                A[p, q] = cdi[0, 0] * hx + cdi[0, 1] * hy
                B[p, q] = cdi[1, 0] * hx + cdi[1, 1] * hy
        # inverse polynomials (for sky -> pixel), fitted on a grid over the image
        gu, gv = np.meshgrid(np.linspace(-cx, cx, 32), np.linspace(-cy, cy, 32))
        gu, gv = gu.ravel(), gv.ravel()
        fu = gu + sum(A[p, q] * gu ** p * gv ** q for p, q in _terms(order, 2))
        fv = gv + sum(B[p, q] * gu ** p * gv ** q for p, q in _terms(order, 2))
        iorder = order + 1
        iterms = _terms(iorder, 1)
        Mi = _design(fu / s, fv / s, iterms)
        ap = np.linalg.lstsq(Mi, gu - fu, rcond=None)[0]
        bp = np.linalg.lstsq(Mi, gv - fv, rcond=None)[0]
        AP = np.zeros((iorder + 1, iorder + 1))
        BP = np.zeros((iorder + 1, iorder + 1))
        for i, (p, q) in enumerate(iterms):
            AP[p, q] = ap[i] / s ** (p + q)
            BP[p, q] = bp[i] / s ** (p + q)
        w.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
        w.sip = Sip(A, B, AP, BP, w.wcs.crpix)
    else:
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.set()
    fx, fy = w.all_world2pix(np.asarray(ra, float), np.asarray(dec, float), 0, maxiter=50, quiet=True)
    resid = np.hypot(np.asarray(fx) - px, np.asarray(fy) - py)
    return w, resid


# --------------------------------------------------------------------------- matching, ring by ring
@dataclasses.dataclass
class DistortionFit:
    wcs: object
    matched: int
    rms_px: float
    edge_before_px: float | None     # typical error near the edges with the original solution
    edge_after_px: float | None
    degree: int

    def summary(self) -> str:
        text = f"Lens distortion corrected using {self.matched} catalogue stars (accuracy {self.rms_px:.1f} px)"
        if self.edge_before_px is not None and self.edge_after_px is not None:
            text += f"; near the edges labels moved from about {self.edge_before_px:.0f} px off to " \
                    f"{self.edge_after_px:.1f} px"
        return text


def _pair(dx, dy, sx, sy, tol):
    """Pair detected stars (dx, dy) with predicted catalogue positions (sx, sy): mutual nearest and clear."""
    if len(dx) == 0 or len(sx) == 0:
        return np.empty(0, int), np.empty(0, int)
    d2 = (dx[:, None] - sx[None, :]) ** 2 + (dy[:, None] - sy[None, :]) ** 2
    j = np.argmin(d2, axis=1)
    best = d2[np.arange(len(dx)), j]
    d2s = d2.copy()
    d2s[np.arange(len(dx)), j] = np.inf
    second = d2s.min(axis=1)
    back = np.argmin(d2, axis=0)
    i = np.arange(len(dx))
    ok = (best < tol * tol) & (back[j] == i) & (second > 4 * best) & (second > (0.7 * tol) ** 2)
    return i[ok], j[ok]


def correct(wcs, lum: np.ndarray, start_fraction: float = 0.5, log=lambda msg: None) -> DistortionFit | None:
    """Improve a solution with a distortion fit. Returns None when it can't be done reliably."""
    from platesolver.core import starcatalog as sc

    h, w = lum.shape
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    half = math.hypot(cx, cy)
    dx, dy, _ = detect_stars(lum, max_stars=1500)
    if len(dx) < 25:
        log(f"Distortion: only {len(dx)} stars found in the image – not corrected")
        return None
    ra, dec, mag, x0, y0 = sc.in_field(wcs, w, h, max_stars=max(400, 3 * len(dx)))
    if len(ra) < 25:
        log(f"Distortion: only {len(ra)} catalogue stars in the field – not corrected")
        return None
    rd = np.hypot(dx - cx, dy - cy)
    # first pass: a small central area, a generous tolerance (the starting solution may be a little off
    # in scale and rotation), only very clear pairs, and only a linear fit
    radius = 0.3 * half
    tol = max(8.0, 0.02 * half)
    first = True
    current = wcs
    sx, sy = x0, y0
    accepted = None          # (wcs, ii, jj, resid, degree) of the last good fit
    full_passes = 0
    for _ in range(14):
        inside = np.nonzero(rd <= radius)[0]
        cand = np.arange(len(sx))
        if first:
            # only the brightest stars on both sides at first: they are the same stars, and few enough
            # to pair safely even when the starting solution is a little off
            inside = inside[:120]
            in_cat = np.nonzero(np.hypot(sx - cx, sy - cy) <= radius * 1.05)[0]
            cand = in_cat[:max(250, 2 * len(inside))]
        a, b = _pair(dx[inside], dy[inside], sx[cand], sy[cand], tol)
        ii, jj = inside[a], cand[b]
        n = len(ii)
        if n < 8:
            if accepted is None:
                log(f"Distortion: only {n} stars could be paired with the catalogue – not corrected")
                return None
            break
        cover = radius / half
        degree = 1 if (n < 12 or first) else (2 if n < 40 or cover < 0.6 else 3)
        r0, d0 = current.wcs.crval
        fit, resid = fit_tan_sip(dx[ii], dy[ii], ra[jj], dec[jj], w, h, degree, float(r0), float(d0))
        for _ in range(3):                           # drop doubtful pairs and fit again (robust)
            sigma = 1.4826 * float(np.median(resid)) + 1e-6
            good = resid < max(3 * sigma, 1.0)
            if good.sum() < 8 or good.all():
                break
            ii, jj = ii[good], jj[good]
            fit, resid = fit_tan_sip(dx[ii], dy[ii], ra[jj], dec[jj], w, h, degree, float(r0), float(d0))
        rms = float(np.sqrt(np.mean(resid ** 2)))
        first = False
        current = fit
        accepted = (fit, ii, jj, resid, degree)
        px, py = current.all_world2pix(ra, dec, 0, maxiter=50, quiet=True)
        sx, sy = np.asarray(px, float), np.asarray(py, float)
        if radius >= half:
            full_passes += 1
            tol = max(3.0, 4 * rms)
            if full_passes >= 2:
                break
        else:
            radius = min(radius * 1.35, half * 1.01)
            tol = max(4.0, 4 * rms, 0.006 * half)
    if accepted is None:
        return None
    current, ii, jj, resid, degree = accepted
    n = len(ii)
    rms = float(np.sqrt(np.mean(resid ** 2))) if n else math.inf
    if n < 20 or rms > max(3.0, 0.002 * half):
        log(f"Distortion: the fit was not reliable ({n} stars, {rms:.1f} px) – not corrected")
        return None
    # how far off were the labels near the edges before, and after?
    outer = np.hypot(dx[ii] - cx, dy[ii] - cy) > 0.6 * half
    before = after = None
    if outer.sum() >= 3:
        bx, by = wcs.all_world2pix(ra[jj][outer], dec[jj][outer], 0, maxiter=50, quiet=True)
        before = float(np.median(np.hypot(np.asarray(bx) - dx[ii][outer], np.asarray(by) - dy[ii][outer])))
        after = float(np.median(resid[outer]))
        if after > before:
            log(f"Distortion: the correction would not improve the edges ({before:.1f} → {after:.1f} px) – kept "
                "the original solution")
            return None
    return DistortionFit(current, n, rms, before, after, degree)
