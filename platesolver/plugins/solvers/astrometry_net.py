# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Online plate solving with astrometry.net (nova.astrometry.net or a local server with the same API).

Used after ASTAP, so it only runs when ASTAP is missing or fails. It can solve almost anything
("blind", with no hints), but it is slower (often 1-5 minutes) and needs a free API key.
API documentation: https://nova.astrometry.net/api_help
"""
from __future__ import annotations

import io
import json
import math
import time

import numpy as np

from platesolver.core import net
from platesolver.core.interfaces import Solver
from platesolver.core.models import ImageData, SolveResult
from platesolver.core.plugin import TaskContext
from platesolver.core.settings import BOOL, FLOAT, INT, STR, SettingField


def binned_fits_bytes(image: ImageData, max_size: int) -> tuple[bytes, int]:
    """16-bit mono FITS of the image, binned by an integer factor so neither side exceeds max_size."""
    from astropy.io import fits

    lum = image.luminance()
    b = max(1, math.ceil(max(lum.shape) / max(256, max_size)))
    if b > 1:
        h, w = (lum.shape[0] // b) * b, (lum.shape[1] // b) * b
        lum = lum[:h, :w].reshape(h // b, b, w // b, b).mean(axis=(1, 3))
    lo, hi = np.percentile(lum[::2, ::2], [0.01, 99.99])
    if hi <= lo:
        lo, hi = float(lum.min()), float(lum.max()) or 1.0
    data = (np.clip((lum - lo) / (hi - lo), 0, 1) * 65535).astype(np.uint16)
    buf = io.BytesIO()
    fits.PrimaryHDU(data).writeto(buf)
    return buf.getvalue(), b


def unbin_wcs(wcs, b: int):
    """WCS of a b×b binned image -> WCS of the full-size image (same sky, same pixel convention)."""
    w = wcs.deepcopy()
    w.sip = None   # distortion terms are for the binned pixels; the linear part is enough for overlays
    if b == 1:
        w.wcs.set()
        return w
    w.wcs.crpix = [b * c - (b - 1) / 2.0 for c in w.wcs.crpix]
    if w.wcs.has_cd():
        w.wcs.cd = w.wcs.cd / b
    else:
        w.wcs.cdelt = np.asarray(w.wcs.cdelt) / b
    w.wcs.set()
    return w


class AstrometryNetSolver(Solver):
    plugin_id = "astrometry_net"
    name = "astrometry.net (online)"
    description = ("Online plate solver, tried when ASTAP isn't available or fails. It can solve images with no "
                   "hints at all, but takes longer (usually 1–5 minutes) and needs internet and a free API key: "
                   "create an account at nova.astrometry.net, then copy the key from 'My Profile'. "
                   "Images are uploaded as private (not publicly visible).")
    priority = 30

    def settings_schema(self):
        return [
            SettingField("api_key", "API key", STR, "", help="From nova.astrometry.net › My Profile. "
                                                             "Without a key this solver is skipped."),
            SettingField("ask_before_upload", "Ask before uploading an image", BOOL, True,
                         help="Shows what will be sent and lets you decide each time. In batch mode you can "
                              "answer once for the whole batch."),
            SettingField("server", "Server address", STR, "https://nova.astrometry.net",
                         help="Change only if you run your own astrometry.net server with the same API."),
            SettingField("use_hints", "Send position and scale hints", BOOL, True,
                         help="Much faster when the file or Settings › Equipment knows them."),
            SettingField("scale_error", "Allowed scale error", FLOAT, 20.0, minimum=1, maximum=100, step=5,
                         decimals=0, suffix=" %"),
            SettingField("search_radius", "Search radius around the hint", FLOAT, 10.0, minimum=0.5, maximum=180,
                         step=1, decimals=1, suffix="°"),
            SettingField("max_upload", "Largest image side uploaded", INT, 2000, minimum=500, maximum=8000, step=250,
                         suffix=" px", help="Bigger images are binned before uploading, which saves time and "
                                            "makes no difference to the result for overlays."),
            SettingField("timeout", "Give up after", INT, 600, minimum=60, maximum=3600, step=60, suffix=" s"),
        ]

    def is_available(self) -> tuple[bool, str]:
        if not str(self.setting("api_key") or "").strip():
            return False, "no API key – add one in Settings › Plate solvers › astrometry.net"
        return True, ""

    # ------------------------------------------------------------------ API
    def _api(self, path: str) -> str:
        return str(self.setting("server")).rstrip("/") + "/api/" + path.lstrip("/")

    def _json(self, raw: bytes) -> dict:
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise RuntimeError(f"astrometry.net sent an unexpected answer: {raw[:120]!r}") from exc

    def login(self) -> str:
        data = self._json(net.post_form(self._api("login"), {
            "request-json": json.dumps({"apikey": str(self.setting("api_key")).strip()})}, timeout=60))
        if data.get("status") != "success":
            raise RuntimeError(f"astrometry.net login failed: {data.get('errormessage', data)}")
        return data["session"]

    def upload_params(self, session: str, image: ImageData) -> dict:
        p = {"session": session, "publicly_visible": "n", "allow_modifications": "n",
             "allow_commercial_use": "n", "crpix_center": True}
        h = image.hints
        if self.setting("use_hints"):
            scale = h.scale_arcsec()
            guessed = any(str(v).startswith("Settings") for k, v in h.source.items()
                          if k in ("focal_length", "pixel_size", "scale"))
            if scale and guessed:
                # from Settings › Equipment: the image may have been resized, drizzled or binned since
                p.update({"scale_units": "arcsecperpix", "scale_type": "ul",
                          "scale_lower": round(scale / 4, 4), "scale_upper": round(scale * 4, 4)})
            elif scale:
                p.update({"scale_units": "arcsecperpix", "scale_type": "ev", "scale_est": round(scale, 4),
                          "scale_err": float(self.setting("scale_error"))})
            if h.has_position:
                p.update({"center_ra": round(h.ra_deg, 5), "center_dec": round(h.dec_deg, 5),
                          "radius": float(self.setting("search_radius"))})
        return p

    def upload_question(self, image: ImageData) -> str:
        return (f"The local solvers could not solve <b>{image.path.name}</b>.<br><br>"
                f"Upload a reduced copy to astrometry.net to solve it online?<ul>"
                f"<li>Monochrome, at most {int(self.setting('max_upload'))} pixels on the longest side</li>"
                f"<li>No file information: no date, camera, location or object name"
                + (" (only a rough position and image scale, to speed up solving)" if self.setting("use_hints")
                   else "") + "</li>"
                f"<li>Stored privately in your astrometry.net account. It is <b>not</b> deleted afterwards; "
                f"the submission number is shown in the Log tab.</li></ul>"
                f"Solving online usually takes 1–5 minutes.")

    def solve(self, image: ImageData, ctx: TaskContext) -> SolveResult:
        if self.setting("ask_before_upload"):
            if not ctx.confirm("Upload to astrometry.net?", self.upload_question(image), "astrometry_upload",
                               yes="Upload", no="Don't upload"):
                return SolveResult.failed("upload declined", self.plugin_id, self.name)
        t0 = time.monotonic()
        deadline = t0 + float(self.setting("timeout"))
        ctx.log("astrometry.net: logging in…")
        session = self.login()
        content, b = binned_fits_bytes(image, int(self.setting("max_upload")))
        params = self.upload_params(session, image)
        if b > 1:   # the uploaded pixels are b× larger
            for k in ("scale_est", "scale_lower", "scale_upper"):
                if params.get(k):
                    params[k] = round(params[k] * b, 4)
        ctx.log(f"astrometry.net: uploading {len(content) / 1e6:.1f} MB" + (f" (binned {b}×{b})" if b > 1 else ""))
        up = self._json(net.post_multipart(self._api("upload"), {"request-json": json.dumps(params)},
                                           {"file": ("image.fits", content, "application/octet-stream")},
                                           timeout=300))
        if up.get("status") != "success":
            raise RuntimeError(f"upload refused: {up.get('errormessage', up)}")
        subid = up["subid"]
        ctx.log(f"astrometry.net: submission {subid} queued, waiting for a solver…")
        job_id = None
        last = ""
        while True:
            ctx.check_cancel()
            if time.monotonic() > deadline:
                return SolveResult.failed(f"no answer within {int(self.setting('timeout'))} s "
                                          f"(submission {subid})", self.plugin_id, self.name)
            if job_id is None:
                sub = self._json(net.get(self._api(f"submissions/{subid}"), timeout=60))
                jobs = [j for j in sub.get("jobs") or [] if j]
                if jobs:
                    job_id = jobs[0]
                    ctx.log(f"astrometry.net: solving (job {job_id})…")
            else:
                status = self._json(net.get(self._api(f"jobs/{job_id}"), timeout=60)).get("status", "")
                if status != last:
                    last = status
                if status == "success":
                    break
                if status == "failure":
                    return SolveResult.failed("astrometry.net could not solve the image", self.plugin_id, self.name)
            self._sleep(ctx, 5.0)
        raw = net.get(str(self.setting("server")).rstrip("/") + f"/wcs_file/{job_id}", timeout=120)
        wcs = unbin_wcs(wcs_from_header_bytes(raw), b)
        return SolveResult.from_wcs(wcs, image.width, image.height, self.plugin_id, self.name,
                                    time.monotonic() - t0, message=f"astrometry.net job {job_id}")

    @staticmethod
    def _sleep(ctx: TaskContext, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ctx.check_cancel()
            time.sleep(0.2)


def wcs_from_header_bytes(raw: bytes):
    from astropy.io import fits
    from astropy.wcs import WCS
    import warnings

    try:
        header = fits.Header.fromstring(raw.decode("ascii", errors="replace"))
    except Exception:
        header = fits.getheader(io.BytesIO(raw))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return WCS(header, naxis=2)
