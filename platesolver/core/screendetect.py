# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Photos of a screen: find the tilt, the star area and the app's own text and buttons.

Used by Crop and rotate (*Find the star area*, *Find app text and buttons*) and to suggest the profile
*Photo of a screen or print* when a phone photo looks like one. Only numpy is used.

* **Tilt.** A screen has long straight edges; the angle at which the image's horizontal and vertical edges
  line up best with the rows and columns is the tilt. Photos of the sky have no such edges: angle 0.
* **Star area.** Where the star-like points are dense, trimmed to the screen's own edges when they are clear.
* **Perspective.** A screen photographed a little from the side is a quadrilateral, not a rectangle. A line
  is fitted to each of the four screen edges (sharp brightness steps sampled along the edge); their
  crossings are the corners, which Crop and rotate maps back onto a rectangle.
* **App text and buttons.** Text lines (several letter-sized marks side by side on one baseline), panels of
  text, toolbars along the edges and hollow shapes (round buttons). A clump of stars in the middle of a rich
  field, a soft glow and a satellite trail are left alone. They are hidden by filling them with the sky colour.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

WORK = 900            # long side of the reduced copy used for detection, pixels


@dataclass
class ScreenGuess:
    angle: float = 0.0                         # clockwise straightening, as in Crop and rotate
    crop: tuple[int, int, int, int] | None = None   # x, y, w, h in the image rotated by `angle`
    looks_like_screen: bool = False            # confident enough to suggest the screen profile
    sharp_sides: int = 0                       # sides of the star area with a clear screen edge
    notes: list[str] = field(default_factory=list)
    quad: tuple | None = None                  # screen corners (TL, TR, BR, BL) in the image rotated by `angle`
    keystone: float = 0.0                      # how far the screen is from a rectangle (0.03 = 3 %)
    edges: dict = field(default_factory=dict)  # side -> position of the clear screen edges (rotated image)
    span: tuple = ()                           # x0, x1, y0, y1 of the star-like points (rotated image)


# --------------------------------------------------------------------------- helpers
def _reduce(lum: np.ndarray, long_side: int = WORK) -> tuple[np.ndarray, float]:
    """A smaller copy by block averaging, and the factor (original pixels per reduced pixel)."""
    a = np.asarray(lum, np.float32)
    f = max(1, int(np.ceil(max(a.shape) / long_side)))
    if f > 1:
        h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
        a = a[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3))
    lo, hi = np.percentile(a, [1, 99.8])
    a = np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1).astype(np.float32)
    return a, float(f)


def _box(a: np.ndarray, ry: int, rx: int) -> np.ndarray:
    """Mean over a (2ry+1) x (2rx+1) box, edges handled by clipping (cumulative sums)."""
    def run(v, r, axis):
        if r <= 0:
            return v
        c = np.cumsum(np.pad(v, [(r + 1, r) if i == axis else (0, 0) for i in range(v.ndim)], mode="edge"),
                      axis=axis, dtype=np.float64)
        n = v.shape[axis]
        hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
        lo = np.take(c, np.arange(0, n), axis=axis)
        return ((hi - lo) / (2 * r + 1)).astype(np.float32)
    return run(run(a, ry, 0), rx, 1)


def _background(a: np.ndarray, r: int) -> np.ndarray:
    """A smooth background: a box mean of the image with bright points clipped first."""
    m = _box(a, r, r)
    clipped = np.minimum(a, m + 0.05)
    return _box(clipped, r, r)


def _labels(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """Connected components (4-neighbour) of a boolean image, by union of horizontal runs."""
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    parent = [0]

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    prev_runs: list[tuple[int, int, int]] = []
    for y in range(h):
        row = mask[y]
        if not row.any():
            prev_runs = []
            continue
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
        runs = []
        j = 0
        for s, e in zip(starts, ends):
            lbl = 0
            while j < len(prev_runs) and prev_runs[j][1] <= s:
                j += 1
            k = j
            while k < len(prev_runs) and prev_runs[k][0] < e:
                other = find(prev_runs[k][2])
                if lbl == 0:
                    lbl = other
                elif other != lbl:
                    parent[max(other, lbl)] = min(other, lbl)
                    lbl = min(other, lbl)
                k += 1
            if lbl == 0:
                lbl = len(parent)
                parent.append(lbl)
            lab[y, s:e] = lbl
            runs.append((s, e, lbl))
        prev_runs = runs
    roots = np.array([find(i) for i in range(len(parent))], np.int32)
    uniq, inv = np.unique(roots, return_inverse=True)
    return inv.astype(np.int32)[lab], len(uniq) - 1


def _components(mask: np.ndarray):
    """(label image, list of (y0, x0, y1, x1, area)) for each component."""
    lab, n = _labels(mask)
    if n == 0:
        return lab, []
    ys, xs = np.nonzero(lab)
    ids = lab[ys, xs]
    out = []
    order = np.argsort(ids, kind="stable")
    ids, ys, xs = ids[order], ys[order], xs[order]
    cuts = np.nonzero(np.diff(ids))[0] + 1
    for gy, gx in zip(np.split(ys, cuts), np.split(xs, cuts)):
        out.append((int(gy.min()), int(gx.min()), int(gy.max()) + 1, int(gx.max()) + 1, len(gy)))
    return lab, out


def _points(a: np.ndarray, sigma: float = 4.0) -> tuple[np.ndarray, np.ndarray]:
    """Star-like points (compact local maxima well above the local noise) in a reduced image."""
    res = a - _background(a, 12)
    noise = 1.4826 * float(np.median(np.abs(res - np.median(res)))) + 1e-6
    m = 3
    core = res[m:-m, m:-m]
    ok = core > sigma * noise
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                ok &= core >= res[m + dy: res.shape[0] - m + dy, m + dx: res.shape[1] - m + dx]
    ys, xs = np.nonzero(ok)
    ys, xs = ys + m, xs + m
    peak = res[ys, xs]
    far = np.max(np.stack([res[ys + dy, xs + dx] for dy, dx in ((3, 0), (-3, 0), (0, 3), (0, -3))]), axis=0)
    keep = far < 0.6 * peak
    return xs[keep].astype(float), ys[keep].astype(float)


# --------------------------------------------------------------------------- tilt
def estimate_tilt(lum: np.ndarray, max_angle: float = 15.0) -> float:
    """Clockwise angle that lines up a photographed screen with the image edges; 0 without straight edges."""
    from platesolver.core.imageedit import rotate_array
    a, _ = _reduce(lum, 700)
    h, w = a.shape
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[:, 1:-1] = np.abs(a[:, 2:] - a[:, :-2])
    gy[1:-1] = np.abs(a[2:] - a[:-2])

    def score(angle):
        rx = rotate_array(gx, 0, float(angle), fill=0.0)
        ry = rotate_array(gy, 0, float(angle), fill=0.0)
        H, W = rx.shape
        ch, cw = int(h * 0.8), int(w * 0.8)
        y0, x0 = (H - ch) // 2, (W - cw) // 2
        cx = rx[y0:y0 + ch, x0:x0 + cw].sum(axis=0)
        cy = ry[y0:y0 + ch, x0:x0 + cw].sum(axis=1)
        return float((cx ** 2).sum() / max(cx.sum() ** 2, 1e-12) * len(cx)
                     + (cy ** 2).sum() / max(cy.sum() ** 2, 1e-12) * len(cy))
    coarse = {float(t): score(t) for t in np.arange(-max_angle, max_angle + 0.01, 1.0)}
    best = max(coarse, key=coarse.get)
    fine = {float(t): score(t) for t in np.arange(best - 0.75, best + 0.76, 0.25)}
    fine.update(coarse)
    best = max(fine, key=fine.get)
    if fine[best] < coarse[0.0] * 1.04:       # no clear straight edges: leave the image as it is
        return 0.0
    return round(best, 2)


# --------------------------------------------------------------------------- star area
def _edge_near(profile: np.ndarray, start: int, direction: int, reach: int, min_step: float) -> int | None:
    """The first sharp step in a profile, searching from `start` outwards (direction ±1) up to `reach`."""
    n = len(profile)
    d = 3
    best, best_i = 0.0, None
    for k in range(-reach // 3, reach):
        i = start + direction * k
        j = i + direction * d
        if not (0 <= i < n and 0 <= j < n):
            continue
        step = abs(profile[j] - profile[i])
        if step > best:
            best, best_i = step, i + direction * (d // 2)
    return best_i if best >= min_step else None


def find_star_area(lum: np.ndarray, angle: float | None = None, perspective: bool = False) -> ScreenGuess:
    """Where the stars are: the area to crop to, in the image straightened by the returned angle.

    With `perspective`, the screen's corners are found too when all four edges are clear; then
    `guess.quad` is set and `guess.crop` is in the squared-up image (see imageedit.square_up)."""
    guess = _star_area(lum, angle)
    if perspective and guess.crop is not None and guess.sharp_sides >= 3:
        from platesolver.core.imageedit import keystone, rotate_array, square_up
        rot = rotate_array(np.asarray(lum, np.float32), 0, guess.angle) if guess.angle else np.asarray(lum, np.float32)
        quad = find_screen_quad(rot, guess)
        if quad is not None:
            squared = _star_area(square_up(rot, quad), 0.0)
            if squared.crop is not None:
                guess.quad, guess.crop, guess.keystone = quad, squared.crop, keystone(quad)
    return guess


def _fit_line(t: np.ndarray, p: np.ndarray, tol: float):
    """Robust straight line p = m*t + c through samples (pairs tried, most inliers wins, then least squares);
    (m, c, inlier fraction) or None."""
    n = len(t)
    if n < 4:
        return None
    best = None
    for i in range(n):
        for j in range(i + 1, n):
            if t[j] == t[i]:
                continue
            m = (p[j] - p[i]) / (t[j] - t[i])
            c = p[i] - m * t[i]
            inl = np.abs(p - (m * t + c)) < tol
            if best is None or inl.sum() > best.sum():
                best = inl
    if best is None or best.sum() < 3:
        return None
    m, c = np.polyfit(t[best], p[best], 1)
    return float(m), float(c), float(best.mean())


def find_screen_quad(rot: np.ndarray, guess: ScreenGuess, samples: int = 24):
    """Corners (TL, TR, BR, BL) of the screen in the straightened image `rot`.

    In strips across each side of the star area, the outermost strong brightness step near the screen's
    edge is taken (the edge of the screen, or of the tablet around it – both are parallel to the picture),
    and a straight line is fitted robustly to each side. None unless all four sides give a clear line."""
    if not guess.span or len(guess.edges) < 3:
        return None
    a, f = _reduce(rot)
    H, W = a.shape
    bg = _background(a, 3)                                   # stars removed, edges kept fairly sharp
    x0, x1, y0, y1 = (v / f for v in guess.span)
    reach = max(6, int(0.05 * max(H, W)))
    d = max(2, int(0.01 * max(H, W)))                       # photographed edges are soft: compare a bit apart
    lines = {}
    for side in ("left", "right", "top", "bottom"):
        vertical = side in ("left", "right")
        outward = -1 if side in ("left", "top") else 1
        if side in guess.edges:
            centre = guess.edges[side] / f
        else:
            centre = {"left": x0, "right": x1, "top": y0, "bottom": y1}[side]
        lo, hi = (y0, y1) if vertical else (x0, x1)
        ts, ps = [], []
        for k in range(samples):
            t = lo + (hi - lo) * (0.06 + 0.88 * k / (samples - 1))
            ti = int(t)
            w0, w1 = max(0, ti - 2), ti + 3
            prof = bg[w0:w1, :].mean(axis=0) if vertical else bg[:, w0:w1].mean(axis=1)
            i0, i1 = max(0, int(centre) - reach), min(len(prof) - d, int(centre) + reach)
            if i1 - i0 < 3:
                continue
            idx = np.arange(i0, i1)
            step = np.abs(prof[idx + d] - prof[idx])
            top = float(step.max())
            if top < 0.06:
                continue
            strong = idx[step >= 0.5 * top]
            e = (strong.max() if outward > 0 else strong.min()) + d / 2      # the outermost strong step
            ts.append(t)
            ps.append(float(e))
        fit = _fit_line(np.array(ts), np.array(ps), tol=max(1.5, 0.004 * max(H, W)))
        if fit is None or fit[2] < 0.5 or len(ts) < samples // 2 or abs(fit[0]) > 0.12:
            return None
        lines[side] = (fit[0], fit[1] * f)          # vertical: x = m*y + c; horizontal: y = m*x + c (full pixels)

    def cross(v, hz):
        (mv, cv), (mh, ch) = lines[v], lines[hz]
        y = (mh * cv + ch) / (1 - mh * mv)
        return (mv * y + cv, y)
    quad = (cross("left", "top"), cross("right", "top"), cross("right", "bottom"), cross("left", "bottom"))
    q = np.asarray(quad)
    w, h = rot.shape[1], rot.shape[0]
    if (q[:, 0] < -0.05 * w).any() or (q[:, 0] > 1.05 * w).any() or (q[:, 1] < -0.05 * h).any() \
            or (q[:, 1] > 1.05 * h).any():
        return None
    return tuple((float(x), float(y)) for x, y in quad)


def _star_area(lum: np.ndarray, angle: float | None = None) -> ScreenGuess:
    from platesolver.core.imageedit import rotate_array
    guess = ScreenGuess()
    guess.angle = estimate_tilt(lum) if angle is None else float(angle)
    rot = rotate_array(np.asarray(lum, np.float32), 0, guess.angle) if guess.angle else np.asarray(lum, np.float32)
    a, f = _reduce(rot)
    H, W = a.shape
    xs, ys = _points(a)
    if len(xs) < 30:
        guess.notes.append("too few star-like points")
        return guess
    # density of points on a coarse grid; the star area is the largest dense patch
    c = max(H, W) / 40.0
    gh, gw = int(np.ceil(H / c)), int(np.ceil(W / c))
    cnt = np.zeros((gh, gw), np.float32)
    np.add.at(cnt, (np.minimum((ys / c).astype(int), gh - 1), np.minimum((xs / c).astype(int), gw - 1)), 1)
    sm = _box(cnt, 1, 1)
    dense = sm >= 0.3 * float(np.percentile(sm[sm > 0], 90))
    lab, comps = _components(dense)
    if not comps:
        return guess
    big = max(comps, key=lambda t: t[4])
    y0, x0, y1, x1 = big[0] * c, big[1] * c, big[2] * c, big[3] * c
    inside = (xs >= x0) & (xs < x1) & (ys >= y0) & (ys < y1)
    px, py = xs[inside], ys[inside]
    bx0, bx1 = np.percentile(px, [0.5, 99.5])
    by0, by1 = np.percentile(py, [0.5, 99.5])
    # clear screen edges near the star area: sharp brightness steps in the row and column means
    col = _box(a[int(by0):int(by1)].mean(axis=0, keepdims=True), 0, 1)[0]
    row = _box(a[:, int(bx0):int(bx1)].mean(axis=1, keepdims=True).T, 0, 1)[0]
    reach = int(max(H, W) * 0.06)
    sides = {"left": _edge_near(col, int(bx0), -1, reach, 0.06),
             "right": _edge_near(col, int(bx1), 1, reach, 0.06),
             "top": _edge_near(row, int(by0), -1, reach, 0.06),
             "bottom": _edge_near(row, int(by1), 1, reach, 0.06)}
    guess.sharp_sides = sum(v is not None for v in sides.values())
    guess.edges = {k: v * f for k, v in sides.items() if v is not None}
    guess.span = (bx0 * f, bx1 * f, by0 * f, by1 * f)
    L = sides["left"] + 2 if sides["left"] is not None else bx0
    R = sides["right"] - 2 if sides["right"] is not None else bx1
    T = sides["top"] + 2 if sides["top"] is not None else by0
    B = sides["bottom"] - 2 if sides["bottom"] is not None else by1
    if R - L < W * 0.1 or B - T < H * 0.1:
        L, R, T, B = bx0, bx1, by0, by1
    # trim bands along the edges with few stars (app bars, status lines)
    sel = (xs >= L) & (xs < R) & (ys >= T) & (ys < B)
    sx, sy = xs[sel], ys[sel]
    band = max(4.0, c / 2)
    for _ in range(12):
        changed = False
        n_all = max(1, len(sx))
        per = n_all / max((R - L) * (B - T), 1.0)
        for side in ("top", "bottom", "left", "right"):
            if side == "top":
                k = np.sum(sy < T + band); area = band * (R - L)
            elif side == "bottom":
                k = np.sum(sy >= B - band); area = band * (R - L)
            elif side == "left":
                k = np.sum(sx < L + band); area = band * (B - T)
            else:
                k = np.sum(sx >= R - band); area = band * (B - T)
            if k < 0.25 * per * area and (R - L) > W * 0.2 and (B - T) > H * 0.2:
                if side == "top":
                    T += band
                elif side == "bottom":
                    B -= band
                elif side == "left":
                    L += band
                else:
                    R -= band
                keep = (sx >= L) & (sx < R) & (sy >= T) & (sy < B)
                sx, sy = sx[keep], sy[keep]
                changed = True
        if not changed:
            break
    full_h, full_w = rot.shape[:2]
    x, y = int(round(L * f)), int(round(T * f))
    w, h = int(round((R - L) * f)), int(round((B - T) * f))
    x, y = max(0, x), max(0, y)
    w, h = min(w, full_w - x), min(h, full_h - y)
    guess.crop = (x, y, w, h)
    margins = (L / W, (W - R) / W, T / H, (H - B) / H)
    area = (R - L) * (B - T) / (W * H)
    guess.looks_like_screen = bool(guess.sharp_sides >= 3 and sum(m > 0.03 for m in margins) >= 3
                                   and 0.1 < area < 0.9)
    return guess


# --------------------------------------------------------------------------- app text and buttons
def find_app_marks(lum: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Rectangles (x, y, w, h) around text, icons and other app marks that aren't stars."""
    a, f = _reduce(lum)
    H, W = a.shape
    res = a - _background(a, 10)
    noise = 1.4826 * float(np.median(np.abs(res - np.median(res)))) + 1e-6
    on = res > max(5 * noise, 0.08)
    lab, comps = _components(on)
    if not comps:
        return []
    star_max = max(6, int(min(H, W) * 0.012))         # a star in a reduced screen photo is smaller than this

    def slanted_line(k, y0, x0, y1, x1):
        """A thin straight piece at a slant – part of a satellite or aircraft trail, not app graphics
        (which run along the rows and columns)."""
        ys, xs = np.nonzero(lab[y0:y1, x0:x1] == k + 1)
        if len(ys) < 6:
            return False
        cov = np.cov(np.vstack([xs, ys]).astype(float))
        ev, vec = np.linalg.eigh(cov)
        if ev[1] < 9 * max(ev[0], 1e-6):          # not elongated
            return False
        ang = abs(np.degrees(np.arctan2(vec[1, 1], vec[0, 1]))) % 180
        return min(ang % 90, 90 - ang % 90) > 12
    letters, marks = [], []
    for k, (y0, x0, y1, x1, area) in enumerate(comps):
        hh, ww = y1 - y0, x1 - x0
        fill = area / max(hh * ww, 1)
        if hh > star_max * 1.6 or ww > star_max * 1.6:
            if max(hh, ww) > min(H, W) * 0.25 and fill < 0.08:
                continue                              # a long thin line, e.g. a satellite trail: leave it
            if slanted_line(k, y0, x0, y1, x1):
                continue
            marks.append((x0, y0, ww, hh))            # an icon, button or graph
        elif 3 <= hh <= star_max * 1.6 and (fill < 0.55 or ww > hh * 1.6 or hh > ww * 1.6):
            if slanted_line(k, y0, x0, y1, x1):
                continue
            letters.append((x0, y0, ww, hh))          # letter-like: thin strokes or elongated
    # text lines: letter-like marks of similar height close together on one line
    lines = []
    used = [False] * len(letters)
    order = sorted(range(len(letters)), key=lambda i: letters[i][0])
    for i in order:
        if used[i]:
            continue
        x, y, w, h = letters[i]
        group = [i]
        used[i] = True
        cx1, cy, ch = x + w, y + h / 2, h
        for j in order:
            if used[j]:
                continue
            x2, y2, w2, h2 = letters[j]
            if x2 < x:
                continue
            if x2 - cx1 > ch * 1.8:
                continue
            if abs((y2 + h2 / 2) - cy) < ch * 0.5 and 0.5 < h2 / ch < 2.0:
                group.append(j)
                used[j] = True
                cx1 = max(cx1, x2 + w2)
        if len(group) >= 3:
            gx0 = min(letters[k][0] for k in group)
            gy0 = min(letters[k][1] for k in group)
            gx1 = max(letters[k][0] + letters[k][2] for k in group)
            gy1 = max(letters[k][1] + letters[k][3] for k in group)
            lines.append((gx0, gy0, gx1 - gx0, gy1 - gy0))
    # a large blob in the middle of the image is a bright star, a clump of stars or a glow, not an app mark.
    # Keep marks next to text (an icon with its label), marks along the edges that line up with others or
    # have neighbours (toolbars, panels), and hollow shapes (rings, buttons)
    near = star_max * 3
    edge_x, edge_y = W * 0.12, H * 0.12

    def centre(r):
        return r[0] + r[2] / 2, r[1] + r[3] / 2

    def gap(r1, r2):
        dx = max(0, max(r1[0], r2[0]) - min(r1[0] + r1[2], r2[0] + r2[2]))
        dy = max(0, max(r1[1], r2[1]) - min(r1[1] + r1[3], r2[1] + r2[3]))
        return max(dx, dy)
    kept = []
    for i, m in enumerate(marks):
        cx, cy = centre(m)
        labelled = any(gap(m, t) < near for t in lines)
        at_edge = cx < edge_x or cx > W - edge_x or cy < edge_y or cy > H - edge_y
        company = labelled or (at_edge and any(gap(m, o) < near for j, o in enumerate(marks) if j != i))
        reach = star_max * 12                      # toolbar neighbours are close to each other
        column = at_edge and sum(1 for o in marks if abs(centre(o)[0] - cx) < star_max * 1.5
                                 and abs(centre(o)[1] - cy) < reach) >= 3
        row = at_edge and sum(1 for o in marks if abs(centre(o)[1] - cy) < star_max * 1.5
                              and abs(centre(o)[0] - cx) < reach) >= 3
        y0, x0 = m[1], m[0]
        area = int(on[y0:y0 + m[3], x0:x0 + m[2]].sum())
        hollow = min(m[2], m[3]) > star_max * 2 and max(m[2], m[3]) > star_max * 3 \
            and area / max(m[2] * m[3], 1) < 0.35
        if company or column or row or hollow:
            kept.append(m)
    # words next to a kept mark belong to the same panel ("66", "Stacked" under "Light 01:34:29")
    rest = [m for m in marks if m not in kept and m[3] <= star_max * 2.5]
    grown = True
    while grown and rest:
        grown = False
        for m in list(rest):
            if any(gap(m, k) < star_max * 2 for k in kept + lines):
                kept.append(m)
                rest.remove(m)
                grown = True
    rects = lines + kept
    # merge marks that are close – nearer than a line of text is high – so a panel of text lines (with the
    # short numbers between them) or a toolbar of icons and labels becomes one box
    pad = max(2, int(star_max * 0.6))
    merged = [list(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out = []
        while merged:
            x, y, w, h = merged.pop()
            k = 0
            while k < len(merged):
                x2, y2, w2, h2 = merged[k]
                g = max(pad, int(0.9 * min(max(h, h2), star_max * 2.5)))
                if min(y + h, y2 + h2) - max(y, y2) > 0.5 * min(h, h2):     # on the same line of text
                    g = max(g, int(2 * min(max(h, h2), star_max * 2.5)))
                if x - g < x2 + w2 and x2 - g < x + w and y - g < y2 + h2 and y2 - g < y + h:
                    nx, ny = min(x, x2), min(y, y2)
                    w, h = max(x + w, x2 + w2) - nx, max(y + h, y2 + h2) - ny
                    x, y = nx, ny
                    merged.pop(k)
                    changed = True
                else:
                    k += 1
            out.append([x, y, w, h])
        merged = out
    result = []
    for x, y, w, h in merged:
        if w * h > 0.5 * W * H:                       # never hide most of the image
            continue
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
        result.append((int(x0 * f), int(y0 * f), int((x1 - x0) * f), int((y1 - y0) * f)))
    return result
