# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Refresh PlateSolver's bundled OpenNGC catalogue from the newest OpenNGC release (run before a release build).

    python tools/update_openngc.py            # newest release
    python tools/update_openngc.py v20260501  # a specific release

OpenNGC by Mattia Verga, https://github.com/mattiaverga/OpenNGC – licence CC-BY-SA-4.0. The release's
NGC.csv and addendum.csv are converted to platesolver/data/openngc.csv (degrees, duplicate and
non-existent entries removed) and the version is written to platesolver/data/openngc_version.json, which
PlateSolver shows in Settings › Object catalogues › OpenNGC. The files are only replaced when the new
catalogue passes the checks, so a failed download never leaves a broken catalogue behind.
Only the Python standard library is used.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = "mattiaverga/OpenNGC"
RAW = "https://raw.githubusercontent.com/" + REPO + "/{ref}/database_files/{name}"
DATA = Path(__file__).resolve().parents[1] / "platesolver" / "data"
OUT_COLUMNS = ["name", "type", "ra", "dec", "const", "majax", "minax", "pa", "bmag", "vmag", "parallax",
               "redshift", "messier", "ngc", "ic", "identifiers", "commonnames"]
SOURCE_COLUMNS = {"name": "Name", "type": "Type", "const": "Const", "majax": "MajAx", "minax": "MinAx",
                  "pa": "PosAng", "bmag": "B-Mag", "vmag": "V-Mag", "parallax": "Pax", "redshift": "Redshift",
                  "messier": "M", "ngc": "NGC", "ic": "IC", "identifiers": "Identifiers",
                  "commonnames": "Common names"}
SKIP_TYPES = {"Dup", "NonEx"}                 # duplicates of another entry, and objects that don't exist
NUMERIC = {"majax", "minax", "pa", "bmag", "vmag", "parallax", "redshift"}
MUST_HAVE = ("NGC0224", "NGC1976", "IC0434", "NGC5194")   # M31, M42, Flame Nebula, M51
_TAG = re.compile(r"^v(\d{8})$")


def _get(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "PlateSolver-build (catalogue refresh)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_release() -> str:
    """Newest release tag (vYYYYMMDD): GitHub's API, else git, else the development version."""
    tags: list[str] = []
    try:
        data = json.loads(_get(f"https://api.github.com/repos/{REPO}/tags?per_page=100", 30))
        tags = [t["name"] for t in data]
    except Exception:
        try:
            out = subprocess.run(["git", "ls-remote", "--tags", f"https://github.com/{REPO}.git"],
                                 capture_output=True, text=True, timeout=60).stdout
            tags = [line.rsplit("/", 1)[-1] for line in out.splitlines()]
        except Exception:
            pass
    tags = sorted(t for t in tags if _TAG.match(t))
    return tags[-1] if tags else "master"


def hms_to_deg(text: str, hours: bool) -> float:
    s = text.strip()
    sign = -1.0 if s.startswith("-") else 1.0
    h, m, sec = (float(p) for p in s.lstrip("+-").split(":"))
    return sign * (h + m / 60.0 + sec / 3600.0) * (15.0 if hours else 1.0)


def _num(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    v = float(text)
    return str(int(v)) if v.is_integer() and "." not in text and "e" not in text.lower() else repr(v)


def convert(*sources: str) -> list[dict]:
    """OpenNGC source CSV texts (semicolon separated) -> PlateSolver rows."""
    rows, seen = [], set()
    for text in sources:
        for r in csv.DictReader(io.StringIO(text), delimiter=";"):
            name = (r.get("Name") or "").strip()
            if not name or (r.get("Type") or "").strip() in SKIP_TYPES or name in seen:
                continue
            if not (r.get("RA") or "").strip() or not (r.get("Dec") or "").strip():
                continue
            seen.add(name)
            out = {k: (r.get(src) or "").strip() for k, src in SOURCE_COLUMNS.items()}
            for k in NUMERIC:
                out[k] = _num(out[k])
            out["ra"] = repr(round(hms_to_deg(r["RA"], True), 6))
            out["dec"] = repr(round(hms_to_deg(r["Dec"], False), 6))
            rows.append(out)
    return rows


def check(rows: list[dict]) -> None:
    names = {r["name"] for r in rows}
    missing = [n for n in MUST_HAVE if n not in names]
    if len(rows) < 13000 or missing:
        raise SystemExit(f"The downloaded catalogue looks wrong ({len(rows)} objects, missing {missing}); "
                         "nothing was changed.")
    for r in rows:
        ra, dec = float(r["ra"]), float(r["dec"])
        if not (0 <= ra < 360 and -90 <= dec <= 90):
            raise SystemExit(f"Bad position for {r['name']}; nothing was changed.")


def write(rows: list[dict], version: str, folder: Path = DATA) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=OUT_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    m = _TAG.match(version)
    info = {"version": version,
            "release_date": f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else "",
            "fetched": dt.date.today().isoformat(), "objects": len(rows),
            "source": f"https://github.com/{REPO}"}
    tmp = folder / "openngc.csv.new"
    tmp.write_text(buf.getvalue(), encoding="utf-8", newline="\n")   # same file on every system
    tmp.replace(folder / "openngc.csv")
    (folder / "openngc_version.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str]) -> int:
    version = argv[1] if len(argv) > 1 else latest_release()
    print(f"OpenNGC {version}: downloading…")
    try:
        texts = [_get(RAW.format(ref=version, name=n)).decode("utf-8") for n in ("NGC.csv", "addendum.csv")]
    except Exception as exc:
        print(f"Could not download OpenNGC {version} ({exc}). The bundled catalogue is unchanged.")
        return 1
    rows = convert(*texts)
    check(rows)
    old = DATA / "openngc_version.json"
    before = json.loads(old.read_text())["version"] if old.exists() else "unknown"
    write(rows, version)
    print(f"OpenNGC {version}: {len(rows)} objects written to {DATA / 'openngc.csv'} (was {before}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
