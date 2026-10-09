# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""A star catalogue PlateSolver can read itself, used to measure and correct lens distortion.

Built in: about 41,000 Hipparcos stars down to magnitude 8 (XHIP compilation, Anderson & Francis 2012,
taken from the d3-celestial project, BSD licence). Enough for phone photos and camera lenses.

"Update" downloads Tycho-2 (Høg et al. 2000; mean positions at J2000) from CDS VizieR down to the chosen
magnitude, and stores it in PlateSolver's own folder. "Use the built-in catalogue" deletes that copy.
"""
from __future__ import annotations

import datetime as _dt
import io
import math
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent.parent / "data"
BUNDLED = DATA / "stars.npz"
VIZIER_MIRRORS = ("https://vizier.cds.unistra.fr", "https://vizier.cfa.harvard.edu")
DEPTHS = {8.0: "about 45,000 stars, 1 MB", 9.0: "about 125,000 stars, 3 MB", 10.0: "about 350,000 stars, 9 MB"}

_cache: dict | None = None


def user_file() -> Path:
    from platesolver.core.paths import app_data_dir
    d = app_data_dir() / "catalogs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "stars.npz"


def _read(path: Path) -> dict:
    with np.load(path, allow_pickle=False) as z:
        ra, dec, mag = z["ra"].astype(np.float64), z["dec"].astype(np.float64), z["mag"].astype(np.float32)
        info = str(z["info"]) if "info" in z else path.name
    if len(ra) < 1000 or not (len(ra) == len(dec) == len(mag)):
        raise ValueError("the star catalogue file is incomplete")
    r, d = np.radians(ra), np.radians(dec)
    xyz = np.column_stack([np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)])
    return {"ra": ra, "dec": dec, "mag": mag, "xyz": xyz, "info": info, "path": path}


def load() -> dict:
    """The downloaded catalogue if there is a good one, otherwise the built-in one."""
    global _cache
    if _cache is None:
        user = user_file()
        if user.exists():
            try:
                _cache = _read(user)
            except Exception:
                _cache = None
        if _cache is None:
            _cache = _read(BUNDLED)
    return _cache


def describe() -> str:
    try:
        cat = load()
    except Exception as exc:
        return f"No star catalogue could be read ({exc})"
    return f"{cat['info']} – {_count(len(cat['ra']))} stars"


def _count(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def in_field(wcs, width: int, height: int, max_stars: int = 3000, margin: float = 20.0):
    """Catalogue stars that fall on the image, brightest first: (ra, dec, mag, x, y)."""
    cat = load()
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    ra0, dec0 = wcs.pixel_to_world_values(cx, cy)
    corners = [wcs.pixel_to_world_values(x, y) for x, y in ((0, 0), (width - 1, 0), (0, height - 1),
                                                             (width - 1, height - 1))]
    radius = max(_sep(float(ra0), float(dec0), float(a), float(b)) for a, b in corners) * 1.1 + 0.5
    r, d = math.radians(float(ra0)), math.radians(float(dec0))
    centre = np.array([math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d)])
    near = np.nonzero(cat["xyz"] @ centre >= math.cos(math.radians(min(radius, 89.0))))[0]
    near = near[np.argsort(cat["mag"][near], kind="stable")]       # brightest first
    ra, dec = cat["ra"][near], cat["dec"][near]
    x, y = wcs.world_to_pixel_values(ra, dec)
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = (np.isfinite(x) & np.isfinite(y) & (x > -margin) & (x < width - 1 + margin) &
          (y > -margin) & (y < height - 1 + margin))
    idx = np.nonzero(ok)[0][:max_stars]
    return ra[idx], dec[idx], cat["mag"][near][idx], x[idx], y[idx]


def _sep(ra1, dec1, ra2, dec2) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


# --------------------------------------------------------------------------- update / reset
def parse_vizier_tsv(text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """VizieR 'asu-tsv' output with the columns RAmdeg, DEmdeg, VTmag."""
    ra, dec, mag = [], [], []
    header_seen = 0
    for line in io.StringIO(text):
        line = line.rstrip("\r\n")
        if not line or line.startswith("#"):
            continue
        if header_seen < 3:              # column names, units, dashes
            header_seen += 1
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            a, b, m = float(parts[0]), float(parts[1]), float(parts[2])
        except ValueError:
            continue                     # stars without a mean position or magnitude
        ra.append(a)
        dec.append(b)
        mag.append(m)
    return np.array(ra), np.array(dec), np.array(mag, np.float32)


def download(limit: float, log=lambda msg: None) -> str:
    """Download Tycho-2 down to `limit` (V_T magnitude) from VizieR and make it the catalogue in use."""
    from platesolver.core import net

    limit = float(limit)
    params = {"-source": "I/259/tyc2", "-out": "RAmdeg,DEmdeg,VTmag", "VTmag": f"<{limit:g}",
              "-out.max": "unlimited", "-oc.form": "d"}
    last = None
    text = ""
    for host in VIZIER_MIRRORS:
        log(f"Downloading Tycho-2 stars to magnitude {limit:g} from {host.split('//')[1]}…")
        try:
            text = net.get(f"{host}/viz-bin/asu-tsv", params, timeout=300).decode("latin-1")
            break
        except Exception as exc:          # try the next mirror
            last = exc
            text = ""
    if not text:
        raise RuntimeError(f"the star catalogue could not be downloaded ({last})")
    ra, dec, mag = parse_vizier_tsv(text)
    if len(ra) < 1000:
        raise RuntimeError(f"the download contained only {len(ra)} stars, so the catalogue was not changed")
    order = np.argsort(mag, kind="stable")
    today = _dt.date.today().isoformat()
    info = f"Downloaded {today}: Tycho-2 stars to magnitude {limit:g} (CDS VizieR I/259)"
    target = user_file()
    tmp = target.with_suffix(".part.npz")
    np.savez_compressed(tmp, ra=ra[order], dec=dec[order], mag=mag[order], info=np.array(info),
                        limit=np.array(limit), date=np.array(today))
    _read(tmp)                            # check it before replacing anything
    tmp.replace(target)
    reset_cache()
    return f"{info} – {_count(len(ra))} stars"


def use_builtin() -> str:
    f = user_file()
    if f.exists():
        f.unlink()
    reset_cache()
    return describe()


def reset_cache() -> None:
    global _cache
    _cache = None
