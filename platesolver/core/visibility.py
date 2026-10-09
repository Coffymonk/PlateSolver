# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Which objects can actually be seen in the image, and the stars in the image that have no marker yet.

After solving, the stars in the image are found (the same detection the lens distortion fit uses) and
compared with the objects from the catalogues:

* a star counts as visible when a detected star sits on its position;
* a galaxy, nebula or cluster counts as visible when its centre is clearly brighter than the sky around it;
* very large objects and objects outside the frame are left undecided (never dimmed or hidden).

Visible stars without a marker can be added, brightest first, named from SIMBAD (proper name, Bayer or
Flamsteed, HD, HIP, TYC, …) with a parallax for the distance. On by default; switched off, the labels come
straight from the catalogues, without looking at the image.
"""
from __future__ import annotations

import math

import numpy as np

from platesolver.core.models import ImageData, SkyObject, SolveResult
from platesolver.core.settings import BOOL, CHOICE, INT, SettingField, SettingsSection

SECTION = "visibility"


class VisibilitySettings(SettingsSection):
    section_id = SECTION
    name = "Visible stars and label order"
    description = ("PlateSolver can look at which stars are actually visible in your image: mark and label "
                   "them first, add visible stars that have no marker yet, and dim or hide objects that are "
                   "too faint to be seen. Switched off, all objects from the catalogues are marked and labelled "
                   "the same way, without looking at the image.")

    def settings_schema(self) -> list[SettingField]:
        return [
            SettingField("enabled", "Find the stars that are visible in the image", BOOL, True,
                         help="After solving, PlateSolver finds the stars in your image and checks which of the "
                              "marked objects can actually be seen. Off: every catalogue object is marked and labelled the "
                              "same way, without looking at the image. The choices below only apply when this is on."),
            SettingField("order", "Label order", CHOICE, "visible", enabled_by="enabled", choices=[
                ("visible", "Visible objects first, then the best-known of the rest"),
                ("catalogue", "Best-known objects first (Messier, named, largest, brightest), visible or not")],
                help="Objects that are labelled first get the space; with 'Hide labels that would overlap' "
                     "the others may have to wait until you zoom in."),
            SettingField("add_stars", "Add visible stars that have no marker", INT, 50, minimum=0, maximum=500, enabled_by="enabled",
                         step=10, suffix=" stars at most",
                         help="The brightest visible stars that no catalogue marked, named after their catalogue "
                              "(proper name, Bayer/Flamsteed, HD, HIP, TYC, …). Names and distances come from "
                              "SIMBAD and need an internet connection. 0 adds none."),
            SettingField("others", "Objects not visible in the image", CHOICE, "dim", enabled_by="enabled", choices=[
                ("show", "Show them like the visible objects"),
                ("dim", "Show them dimmed, so the visible objects stand out"),
                ("hide", "Hide them on the image, keep them in the object list")],
                help="Objects that are too faint to be seen in this image are still really there. Large objects "
                     "and objects outside the frame are never dimmed or hidden."),
        ]


# --------------------------------------------------------------------------- measuring
def tolerance_px(solution: SolveResult, width: int, height: int) -> float:
    """How far (pixels) a detected star may be from a catalogue position and still be the same star."""
    scale = solution.pixel_scale_arcsec or 1.0
    return float(min(max(4.0, 8.0 / scale), max(4.0, 0.01 * min(width, height))))


def detect(image: ImageData, max_stars: int = 1500):
    """Stars in the image: (x, y, flux), brightest first.

    Two passes: the normal one finds compact stars; a second one on a 4 × smaller copy finds the brightest
    stars, whose large saturated discs the first pass skips on purpose (it was made for measuring positions).
    """
    from platesolver.core.distortion import detect_stars
    lum = image.luminance()
    if image.format.upper() in ("JPEG", "JPG", "HEIC", "HEIF"):
        from platesolver.core.phoneaids import soften
        lum = soften(lum)
    xs, ys, fl = detect_stars(lum, max_stars=max_stars, sigma=5.0)
    k = 4
    a = np.asarray(lum, np.float32)
    h2, w2 = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    if h2 >= 200 and w2 >= 200:
        small = a[:h2, :w2].reshape(h2 // k, k, w2 // k, k).mean(axis=(1, 3))
        small = _smooth(_smooth(small))         # a flat (saturated) top gets one clear peak in its middle
        bx, by, bf = detect_stars(small, max_stars=150, sigma=8.0)
        f0 = max(1, int(math.ceil(max(a.shape) / 2000)))      # the first pass's own reduction
        bx, by = bx * k + (k - 1) / 2.0, by * k + (k - 1) / 2.0
        bf = bf * (k / f0) ** 2                                # comparable with the first pass's totals
        near = 3.0 * k
        add = [i for i in range(len(bx))
               if len(xs) == 0 or float(np.min((xs - bx[i]) ** 2 + (ys - by[i]) ** 2)) > near * near]
        if add:
            xs, ys, fl = np.concatenate([xs, bx[add]]), np.concatenate([ys, by[add]]), np.concatenate([fl, bf[add]])
            order = np.argsort(fl)[::-1]
            xs, ys, fl = xs[order], ys[order], fl[order]
    return (xs, ys, fl), lum


def _smooth(a: np.ndarray) -> np.ndarray:
    """3 × 3 box blur."""
    p = np.pad(a, 1, mode="edge")
    return sum(p[dy:dy + a.shape[0], dx:dx + a.shape[1]] for dy in range(3) for dx in range(3)) / 9.0


def _nearest(xs: np.ndarray, ys: np.ndarray, x: float, y: float) -> tuple[int, float]:
    if len(xs) == 0:
        return -1, math.inf
    d2 = (xs - x) ** 2 + (ys - y) ** 2
    i = int(np.argmin(d2))
    return i, float(math.sqrt(d2[i]))


def _sky_ring(width: int, height: int, x: float, y: float, r: float):
    """Pixel window, core radius and ring radii used to compare an object with the sky around it."""
    core_r = min(max(2.0, 0.5 * r), 60.0)
    inner = min(1.5 * r, 400.0)
    outer = max(min(2.5 * r, 600.0), inner + 4)
    x0, x1 = int(max(0, x - outer)), int(min(width, x + outer + 1))
    y0, y1 = int(max(0, y - outer)), int(min(height, y + outer + 1))
    return x0, x1, y0, y1, core_r, inner, outer


def extended_visible(lum: np.ndarray, x: float, y: float, radius_px: float, xs=None, ys=None,
                     star_r: float = 4.0) -> bool | None:
    """Is a galaxy or nebula brighter than the sky around it (stars masked out)? None when it can't be judged."""
    h, w = lum.shape
    if not (0 <= x < w and 0 <= y < h):
        return None
    r = max(3.0, radius_px)
    if r > 0.4 * min(w, h):
        return None                                   # fills the frame: no sky left to compare with
    x0, x1, y0, y1, core_r, inner, outer = _sky_ring(w, h, x, y, r)
    step = max(1, int(math.sqrt(max(1.0, (x1 - x0) * (y1 - y0) / 250_000))))
    patch = np.asarray(lum[y0:y1:step, x0:x1:step], np.float32)
    yy, xx = np.mgrid[y0:y1:step, x0:x1:step]
    d = np.hypot(xx - x, yy - y)
    ok = np.ones(patch.shape, bool)
    if xs is not None and len(xs):
        m = star_r + 2.0
        sel = np.nonzero((xs > x0 - m) & (xs < x1 + m) & (ys > y0 - m) & (ys < y1 + m))[0]
        for k in sel[:3000]:
            ok &= (xx - xs[k]) ** 2 + (yy - ys[k]) ** 2 > m * m
    in_ring = (d >= inner) & (d <= outer) & ok
    core = patch[(d <= core_r) & ok]
    ring = patch[in_ring]
    if core.size < 4 or ring.size < 20:
        return None
    bg = float(np.median(ring))
    noise = float(np.median(np.abs(ring - bg))) * 1.4826 + 1e-9
    signal = float(np.median(core)) - bg
    # a gradient (Milky Way, light pollution, vignetting) makes one side of the ring brighter than the other:
    # the object must stand out more than that
    quads = []
    for qx in (xx < x, xx >= x):
        for qy in (yy < y, yy >= y):
            part = patch[in_ring & qx & qy]
            if part.size >= 5:
                quads.append(float(np.median(part)))
    spread = (max(quads) - min(quads)) if len(quads) >= 3 else 0.0
    return bool(signal > max(2.5 * noise, 2.0 * spread))


def cluster_visible(xs, ys, x: float, y: float, radius_px: float, width: int, height: int) -> bool | None:
    """Is a star cluster visible: clearly more stars inside it than the same area of sky around it?"""
    r = max(3.0, radius_px)
    if r > 0.4 * min(width, height) or len(xs) == 0:
        return None
    x0, x1, y0, y1, _core, inner, outer = _sky_ring(width, height, x, y, r)
    d = np.hypot(xs - x, ys - y)
    n_in = int(np.sum(d <= r))
    ring_n = int(np.sum((d >= inner) & (d <= outer)))
    # the part of the ring that lies on the image (approximate, by sampling)
    t = np.linspace(0, 2 * np.pi, 72, endpoint=False)
    rr = np.linspace(inner, outer, 6)
    px = x + np.outer(rr, np.cos(t))
    py = y + np.outer(rr, np.sin(t))
    on = float(np.mean((px >= 0) & (px < width) & (py >= 0) & (py < height)))
    ring_area = math.pi * (outer ** 2 - inner ** 2) * on
    if ring_area <= 0:
        return None
    expected = ring_n / ring_area * math.pi * r * r
    return bool(n_in >= 3 and n_in > expected + 3.0 * math.sqrt(max(expected, 1.0)))


def classify_objects(objects: list[SkyObject], solution: SolveResult, xs, ys, lum, width: int,
                     height: int) -> set[int]:
    """Set obj.extra['visible'] (True, False or None). Returns the detected stars that were used."""
    tol = tolerance_px(solution, width, height)
    scale = solution.pixel_scale_arcsec or 1.0
    used: set[int] = set()
    for obj in objects:
        if obj.x is None or obj.y is None:
            continue
        inside = 0 <= obj.x < width and 0 <= obj.y < height
        if not inside:
            obj.extra["visible"] = None
            continue
        if obj.category == "star":
            i, dist = _nearest(xs, ys, obj.x, obj.y)
            obj.extra["visible"] = dist <= tol
            if dist <= tol:
                used.add(i)
        else:
            radius = (obj.size_arcmin or 0.0) * 60.0 / 2.0 / scale
            if radius < 2.0 * tol:
                # smaller than a few pixels here (e.g. a planetary nebula in a phone photo): it is only
                # visible if it shows up as a point, like a star
                i, dist = _nearest(xs, ys, obj.x, obj.y)
                obj.extra["visible"] = dist <= tol
            elif obj.category == "cluster":
                obj.extra["visible"] = cluster_visible(xs, ys, obj.x, obj.y, radius, width, height)
            else:
                obj.extra["visible"] = extended_visible(lum, obj.x, obj.y, radius, xs, ys, tol)
    return used


def reorder(objects: list[SkyObject]) -> list[SkyObject]:
    """Visible first, undecided next, invisible last; the catalogue order is kept within each group."""
    rank = {True: 0, None: 1, False: 2}
    return sorted(objects, key=lambda o: rank.get(o.extra.get("visible"), 1))


# --------------------------------------------------------------------------- adding the unmarked stars
def unmarked_stars(objects: list[SkyObject], xs, ys, flux, used: set[int], tol: float, limit: int):
    """Detected stars (brightest first) that no object marks: indices into xs/ys."""
    if limit <= 0 or len(xs) == 0:
        return []
    ox = np.array([o.x for o in objects if o.x is not None], float)
    oy = np.array([o.y for o in objects if o.y is not None], float)
    out = []
    for i in np.argsort(flux)[::-1]:
        i = int(i)
        if i in used:
            continue
        if len(ox) and float(np.min((ox - xs[i]) ** 2 + (oy - ys[i]) ** 2)) <= (2 * tol) ** 2:
            continue
        out.append(i)
        if len(out) >= limit * 3:          # some won't be identified; look at a few more than needed
            break
    return out


def _magnitude_limit(solution: SolveResult, n_wanted: int) -> float:
    """A V magnitude that includes about n_wanted catalogue stars in the field (keeps the SIMBAD query small)."""
    try:
        from platesolver.core import starcatalog
        _ra, _dec, mag, _x, _y = starcatalog.in_field(solution.wcs, solution.width, solution.height,
                                                     max_stars=100_000, margin=0)
    except Exception:
        return 16.0
    if len(mag) > n_wanted:
        return float(min(16.0, mag[n_wanted] + 0.7))
    return 16.0


BATCH = 25          # stars per SIMBAD question


def identify_with_simbad(solution: SolveResult, cand_x, cand_y, tol: float, limit: int, log) -> list[SkyObject]:
    """Name the candidate stars from SIMBAD: brightest catalogue star within the tolerance of each."""
    from platesolver.plugins.catalogs.simbad_catalog import COLUMNS, ID_PATTERNS, _cone, _like_any
    from platesolver.services import simbad
    from platesolver.services.simbad import best_common_name, classify, clean_id, num

    # Ask only about small circles around the stars that need a name, a few dozen per question: quick for
    # any field size (a phone photo covers thousands of square degrees) and no sorting needed.
    scale = solution.pixel_scale_arcsec or 1.0
    circle_deg = max(1.5 * tol * scale, 15.0) / 3600.0
    vlim = _magnitude_limit(solution, max(200, 4 * len(cand_x))) + 1.5
    rows: list[dict] = []
    points = []
    for cx, cy in zip(cand_x, cand_y):
        ra, dec = solution.pixel_to_radec(float(cx), float(cy))
        if math.isfinite(ra) and math.isfinite(dec):
            points.append((ra % 360.0, dec))
    for i in range(0, len(points), BATCH):
        circles = " OR ".join(_cone(ra, dec, circle_deg) for ra, dec in points[i:i + BATCH])
        rows += simbad.query(f"SELECT TOP 2000 {COLUMNS} FROM basic AS b JOIN allfluxes AS f ON f.oidref = b.oid "
                             f"WHERE ({circles}) AND f.\"V\" <= {vlim:.2f}")
    stars = []
    seen_oids: set[str] = set()
    for r in rows:
        if r.get("oid") in seen_oids or classify(r.get("otype", ""))[0] != "star":
            continue
        seen_oids.add(r.get("oid"))
        ra, dec, v = num(r.get("ra")), num(r.get("dec")), num(r.get("vmag", r.get("V")))
        if ra is None or dec is None or v is None:
            continue
        x, y = solution.radec_to_pixel(ra, dec)
        if math.isfinite(x) and math.isfinite(y):
            stars.append((v, x, y, r))
    if not stars:
        return []
    sx = np.array([s[1] for s in stars])
    sy = np.array([s[2] for s in stars])
    taken: set[int] = set()
    chosen: list[tuple[dict, float, float]] = []
    for cx, cy in zip(cand_x, cand_y):
        near = np.nonzero((sx - cx) ** 2 + (sy - cy) ** 2 <= tol * tol)[0]
        near = [int(k) for k in near if int(k) not in taken]
        if not near:
            continue
        k = min(near, key=lambda j: stars[j][0])          # the brightest one there
        taken.add(k)
        chosen.append((stars[k][3], stars[k][1], stars[k][2]))
        if len(chosen) >= limit:
            break
    if not chosen:
        return []
    oids = [int(float(r["oid"])) for r, _x, _y in chosen]
    ids: dict[int, list[str]] = {}
    for part in simbad.chunks(sorted(oids), 400):
        for row in simbad.query(f"SELECT oidref, id FROM ident WHERE oidref IN ({simbad.in_list(part)}) AND "
                                f"{_like_any('id', ID_PATTERNS + ('TYC %', 'Gaia DR3 %', '2MASS J%'))}"):
            ids.setdefault(int(float(row["oidref"])), []).append(" ".join(row["id"].split()))
    out = []
    for (r, x, y), oid in zip(chosen, oids):
        main_id = " ".join(r.get("main_id", "").split())
        all_ids = list(dict.fromkeys(ids.get(oid, []) + [main_id]))
        names = [i for i in all_ids if i.startswith("NAME ")]
        common = best_common_name(names)
        desig = next((clean_id(i) for p in ("* ", "V* ", "HD ", "HIP ", "TYC ", "Gaia DR3 ", "2MASS J")
                      for i in all_ids if i.startswith(p)), clean_id(main_id))
        name, common = (common, desig) if common else (desig, "")
        _cat, type_label = classify(r.get("otype", ""))
        out.append(SkyObject(
            name=name, ra_deg=num(r.get("ra")), dec_deg=num(r.get("dec")), object_type=type_label,
            aliases=[clean_id(i) for i in all_ids if clean_id(i) not in (name, common)][:10],
            magnitude=num(r.get("vmag", r.get("V"))), x=x, y=y, catalog="SIMBAD", category="star",
            common_name=common,
            extra={"simbad_oid": oid, "simbad_main_id": main_id, "otype": r.get("otype", ""),
                   "plx_mas": num(r.get("plx_value")), "plx_err_mas": num(r.get("plx_err")),
                   "redshift": num(r.get("rvz_redshift")), "identifiers": all_ids, "famous": False,
                   "catalogued": True, "visible": True, "added_visible": True}))
    log(f"Visible stars: added {len(out)} stars that had no marker (names from SIMBAD)")
    return out


def identify_offline(solution: SolveResult, cand_x, cand_y, tol: float, limit: int) -> list[SkyObject]:
    """Without internet: use PlateSolver's own star catalogue (positions and magnitudes, no names)."""
    from platesolver.core import starcatalog
    from platesolver.core.formatting import format_dec, format_ra
    ra, dec, mag, x, y = starcatalog.in_field(solution.wcs, solution.width, solution.height,
                                             max_stars=100_000, margin=0)
    taken: set[int] = set()
    out = []
    for cx, cy in zip(cand_x, cand_y):
        near = np.nonzero((x - cx) ** 2 + (y - cy) ** 2 <= tol * tol)[0]
        near = [int(k) for k in near if int(k) not in taken]
        if not near:
            continue
        k = min(near, key=lambda j: mag[j])
        taken.add(k)
        out.append(SkyObject(
            name=f"Star mag {float(mag[k]):.1f}", ra_deg=float(ra[k]),
            aliases=[f"{format_ra(float(ra[k]))} {format_dec(float(dec[k]))}"],
            dec_deg=float(dec[k]), object_type="Star", magnitude=float(mag[k]), x=float(x[k]), y=float(y[k]),
            catalog="Star catalogue", category="star",
            extra={"visible": True, "added_visible": True, "famous": False, "catalogued": False}))
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- the whole step
def apply(objects: list[SkyObject], image: ImageData, solution: SolveResult, settings, log,
          errors: list[str] | None = None) -> list[SkyObject]:
    """Mark what is visible, add unmarked visible stars and put visible objects first (as set)."""
    if not settings.get("enabled") or solution is None or solution.wcs is None:
        return objects
    log("Looking for the stars that are visible in the image…")
    (xs, ys, flux), lum = detect(image)
    w, h = image.width, image.height
    used = classify_objects(objects, solution, xs, ys, lum, w, h)
    tol = tolerance_px(solution, w, h)
    limit = int(settings.get("add_stars") or 0)
    added: list[SkyObject] = []
    if limit > 0:
        cand = unmarked_stars(objects, xs, ys, flux, used, tol, limit)
        cx, cy = xs[cand], ys[cand]
        if len(cand):
            try:
                added = identify_with_simbad(solution, cx, cy, tol, limit, log)
            except Exception as exc:
                log(f"Visible stars: SIMBAD could not name them ({exc}); using the built-in star catalogue")
                if errors is not None:
                    errors.append(f"Visible stars: {exc}")
                try:
                    added = identify_offline(solution, cx, cy, tol, limit)
                    log(f"Visible stars: added {len(added)} stars without names (names need an internet "
                        "connection)")
                except Exception as exc2:
                    log(f"Visible stars: none added ({exc2})")
    seen = sum(o.extra.get("visible") is True for o in objects)
    faint = sum(o.extra.get("visible") is False for o in objects)
    log(f"Visible stars: {len(xs)} stars found in the image; {seen} marked objects visible, {faint} too faint "
        "to be seen")
    objects = objects + added
    if str(settings.get("order") or "visible") == "visible":
        objects = reorder(objects)
    return objects
