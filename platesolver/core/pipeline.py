# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Runs the stages in order using whichever plugins are enabled."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from platesolver.core.general import GeneralSettings
from platesolver.core.interfaces import (CatalogProvider, DistanceResolver, ImageLoader,
                                         LinkProvider, OverlayLayer, Solver)
from platesolver.core.models import ImageData, SkyObject, SolveResult
from platesolver.core.plugin import Cancelled, TaskContext
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import SectionSettings

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, registry: PluginRegistry, general: SectionSettings,
                 equipment: SectionSettings | None = None):
        self.registry = registry
        self.general = general
        if equipment is None:
            from platesolver.core.equipment import EquipmentSettings
            EquipmentSettings.migrate(general.store)
            equipment = general.store.section(EquipmentSettings())
        self.equipment = equipment
        from platesolver.core.stars import StarCheckSettings
        from platesolver.core.preprocess import SolvePrepSettings
        self.star_check = general.store.section(StarCheckSettings())
        self.solve_prep = general.store.section(SolvePrepSettings())
        from platesolver.core.phoneaids import PhoneAidSettings
        self.phone_aids = general.store.section(PhoneAidSettings())
        from platesolver.core.distortion import DistortionSettings
        self.distortion = general.store.section(DistortionSettings())
        from platesolver.core.visibility import VisibilitySettings
        self.visibility = general.store.section(VisibilitySettings())
        from platesolver.core.objecthint import HintSettings
        self.hint_settings = general.store.section(HintSettings())
        from platesolver.core.profiles import ProfileManager
        self.profiles = ProfileManager(general.store, registry)
        self.object_errors: list[str] = []

    # ------------------------------------------------------------------ loading
    def loaders(self) -> list[ImageLoader]:
        return self.registry.of_kind(ImageLoader)

    def file_filters(self) -> list[str]:
        """Filters for the Open dialog: an 'all supported' entry, then one per format."""
        loaders = self.loaders()
        all_ext = " ".join(f"*{e}" for l in loaders for e in l.extensions)
        filters = [f"All supported images ({all_ext})"]
        filters += [f"{l.format_name or l.name} ({' '.join('*' + e for e in l.extensions)})" for l in loaders]
        return filters + ["All files (*)"]

    def can_load(self, path: Path) -> bool:
        return any(l.can_load(path) for l in self.loaders())

    def load(self, path: Path, auto_profile: bool = True) -> ImageData:
        path = Path(path)
        candidates = [l for l in self.loaders() if l.can_load(path)]
        if not candidates:
            raise ValueError(f"No image format plugin can read '{path.suffix}' files")
        errors = []
        for loader in candidates:
            try:
                image = loader.load(path)
                image.profile_note = self.profiles.auto_select(image) if auto_profile else ""
                self._apply_default_hints(image)
                return image
            except Exception as exc:
                log.warning("%s could not read %s: %s", loader.name, path.name, exc)
                errors.append(f"{loader.name}: {exc}")
        raise ValueError("Could not read the image. " + "; ".join(errors))

    def _apply_default_hints(self, image: ImageData) -> None:
        """Fill in focal length / pixel size from Settings › Equipment where the file is silent.

        Values from the file and from the settings are only combined when they evidently belong
        to the same equipment: a camera photo with a 24 mm focal length must not be given the
        telescope camera's pixel size, which would produce a badly wrong image scale.
        """
        h = image.hints
        if self.screen_photo():
            from platesolver.core.screenphoto import ignore_camera_scale
            ignore_camera_scale(image)
            return
        fl = float(self.equipment.get("focal_length") or 0)
        px = float(self.equipment.get("pixel_size") or 0)
        src = "Settings › Equipment"
        if self.equipment.get("override_file"):
            if fl > 0:
                h.focal_length_mm, h.source["focal_length"] = fl, src
            if px > 0:
                h.pixel_size_um, h.source["pixel_size"] = px, src
            if fl > 0 and px > 0 and h.pixel_scale_arcsec:
                h.pixel_scale_arcsec = None   # the file's own scale would win otherwise
                h.source.pop("scale", None)
            self._apply_drizzle(image)
            return
        if h.scale_arcsec():
            return                            # the file already gives a complete scale
        self._fill_from_equipment(image, fl, px, src)
        self._apply_drizzle(image)
        f35 = float(self.equipment.get("lens_35mm") or 0)
        if not h.scale_arcsec() and f35 > 0:      # e.g. a phone photo whose EXIF was stripped
            from platesolver.plugins.loaders.jpeg_loader import EXIF_FOCAL_35MM, camera_hints
            guess = camera_hints({EXIF_FOCAL_35MM: f35}, image.width, image.height)
            h.pixel_scale_arcsec = guess.pixel_scale_arcsec
            h.source["scale"] = f"Settings › Equipment ({f35:g} mm full-frame equivalent, the photo has no EXIF)"

    def screen_photo(self) -> bool:
        """True when the active profile is for photos of a screen or print."""
        prof = self.profiles.active()
        return bool(prof and prof.kind == "screen")

    def _apply_drizzle(self, image: ImageData) -> None:
        """A drizzled image (bigger than the sensor) has smaller pixels on the sky than the camera's."""
        h = image.hints
        if not self.equipment.get("detect_drizzle") or not h.pixel_size_um:
            return
        if not str(h.source.get("pixel_size", "")).startswith("Settings"):
            return
        from platesolver.core.equipment import drizzle_factor
        sw, sh = int(self.equipment.get("sensor_width") or 0), int(self.equipment.get("sensor_height") or 0)
        f = drizzle_factor(image.width, image.height, sw, sh)
        if f > 1:
            h.pixel_size_um = h.pixel_size_um / f
            h.source["pixel_size"] = f"Settings › Equipment ÷ {f:g} (image larger than the sensor: drizzled {f:g}×)"
            image.notes.append(f"The image ({image.width} × {image.height}) is larger than the sensor ({sw} × {sh}), "
                               f"so it is treated as {f:g}× drizzled: scale {h.scale_arcsec():.2f}″/px")

    def _fill_from_equipment(self, image: ImageData, fl: float, px: float, src: str) -> None:
        h = image.hints
        same = lambda a, b: a and b and abs(a - b) / b < 0.05
        if not h.focal_length_mm and not h.pixel_size_um:
            if fl > 0:
                h.focal_length_mm, h.source["focal_length"] = fl, src
            if px > 0:
                h.pixel_size_um, h.source["pixel_size"] = px, src
        elif h.focal_length_mm and not h.pixel_size_um and px > 0:
            if same(h.focal_length_mm, fl):
                h.pixel_size_um, h.source["pixel_size"] = px, src
            else:
                image.notes.append(f"Focal length {h.focal_length_mm:g} mm in the file differs from your "
                                   f"telescope ({fl:g} mm), so your camera's pixel size was not used")
        elif h.pixel_size_um and not h.focal_length_mm and fl > 0:
            if same(h.pixel_size_um, px):
                h.focal_length_mm, h.source["focal_length"] = fl, src
            else:
                image.notes.append(f"Pixel size {h.pixel_size_um:g} µm in the file differs from your camera "
                                   f"({px:g} µm), so your telescope's focal length was not used")

    # ------------------------------------------------------------------ solving
    def solvers(self) -> list[Solver]:
        return self.registry.of_kind(Solver)

    def check_stars(self, image: ImageData, ctx: TaskContext) -> int | None:
        """Count stars before solving (skipped for images that already carry a solution)."""
        if not self.star_check.get("enabled") or image.header_wcs is not None:
            return None
        from platesolver.core.stars import count_stars
        image.star_count = count_stars(image.luminance()).count
        ctx.log(f"Star check: {image.star_count} stars found")
        return image.star_count

    # ------------------------------------------------------------------ object hints
    def set_object_hint(self, image: ImageData, text: str, ctx: TaskContext | None = None,
                        source: str = "you"):
        """Look up an object name or coordinates and use it as the position hint. Raises LookupError."""
        from platesolver.core import objecthint
        hint = objecthint.resolve(text, source, bool(self.hint_settings.get("use_simbad")))
        objecthint.apply(image, hint)
        if ctx:
            ctx.log("Object hint: " + hint.describe())
        return hint

    def hint_from_filename(self, image: ImageData, ctx: TaskContext) -> None:
        """For images without a position: 'M101_final.jpg' -> search near M 101."""
        if image.hints.has_position or image.header_wcs is not None or not self.hint_settings.get("from_filename"):
            return
        from platesolver.core.objecthint import guess_from_filename
        name = guess_from_filename(image.path)
        if not name:
            return
        try:
            self.set_object_hint(image, name, ctx, "file name")
        except LookupError as exc:
            ctx.log(f"File name suggests {name}, but no position was found: {exc}")

    def solve(self, image: ImageData, ctx: TaskContext, interactive: bool = True) -> SolveResult:
        """interactive=False (batch mode) never asks; the 'skip in batch' setting decides instead."""
        n = self.check_stars(image, ctx)
        minimum = int(self.star_check.get("min_stars"))
        if n is not None and n < minimum:
            from platesolver.core.stars import starless_warning
            if interactive:
                go = ctx.confirm("Very few stars", starless_warning(image.path.name, n), "",
                                 yes="Solve anyway", no="Don't solve")
            else:
                go = not self.star_check.get("batch_skip")
            if not go:
                failed = SolveResult.failed(f"Not solved: only {n} star{'s' if n != 1 else ''} found – "
                                            "probably a starless image")
                failed.attempts = [f"Star check: {n} stars (fewer than {minimum}); solving skipped"]
                return failed
            ctx.log(f"Only {n} stars – trying anyway")
        # from here on, each question (upload? phone photo?) is asked only once for this image
        ctx = self._ask_once(image, ctx)
        attempts: list[str] = []
        self.hint_from_filename(image, ctx)
        solvers = self.solvers()
        if not solvers:
            return SolveResult.failed("No plate solver is enabled. Enable one in Settings.")

        # --- solving aids: a cleaned copy (flattened background, no hot pixels) when it may help
        mode = str(self.solve_prep.get("flatten") or "auto")
        if mode != "never" and image.header_wcs is None:
            from platesolver.core.preprocess import unevenness
            image.background_unevenness = unevenness(image)
            ctx.log(f"Sky background varies {image.background_unevenness * 100:.0f} % across the image")
        threshold = float(self.solve_prep.get("uneven_percent") or 20) / 100.0
        flatten_first = mode == "always" or (
            mode == "auto" and (image.background_unevenness or 0) > threshold)
        prepared: dict = {}

        def cleaned():
            if "image" not in prepared:
                from platesolver.core.preprocess import prepared_copy
                ctx.log("Preparing a cleaned copy for solving (flattened background, hot pixels removed)…")
                prepared["image"], prepared["notes"] = prepared_copy(image, bool(self.solve_prep.get("hot_pixels")))
                ctx.log("Cleaned copy: " + prepared["notes"])
            return prepared["image"]

        for solver in solvers:
            ctx.check_cancel()
            ok, why = solver.is_available()
            if not ok:
                attempts.append(f"{solver.name}: skipped – {why}")
                ctx.log(f"{solver.name} skipped: {why}")
                continue
            if not solver.uses_pixels:
                variants = [False]
            elif flatten_first:
                # the original is always tried too: a processed image with a large nebula or galaxy
                # looks "uneven" but usually solves best as it is
                variants = [True] if mode == "always" else ([False, True] if not image.is_linear else [True, False])
            else:
                variants = [False] + ([True] if mode in ("auto", "retry") and solver.retry_prepared else [])
            for use_clean in variants:
                ctx.check_cancel()
                label = solver.name + (" (cleaned copy)" if use_clean else "")
                ctx.log(f"Trying {label}…")
                t0 = time.monotonic()
                try:
                    result = solver.solve(cleaned() if use_clean else image, ctx)
                except Cancelled:
                    raise
                except Exception as exc:
                    log.exception("%s crashed", solver.name)
                    why = str(exc) if isinstance(exc, (RuntimeError, TimeoutError)) else f"{type(exc).__name__}: {exc}"
                    result = SolveResult.failed(why, solver.plugin_id, solver.name)
                if not result.elapsed_s:
                    result.elapsed_s = time.monotonic() - t0
                if result.success:
                    attempts.append(f"{label}: solved in {result.elapsed_s:.1f} s")
                    result.attempts = attempts
                    if use_clean:
                        note = "Solved using a cleaned copy: " + prepared.get("notes", "")
                        result.message = f"{note}. {result.message}" if result.message else note
                    ctx.log(f"Solved by {label} in {result.elapsed_s:.1f} s")
                    result.scale_note = scale_check(image, result)
                    if result.scale_note:
                        ctx.log(result.scale_note)
                    return self.correct_distortion(image, result, ctx)
                attempts.append(f"{label}: {result.message}")
                ctx.log(f"{label}: {result.message}")
                if result.message == "upload declined":
                    break
        if self.screen_photo():
            from platesolver.core.screenphoto import solve_softened
            soft = solve_softened(self, image, ctx, attempts)
            if soft is not None:
                return soft
            failed = SolveResult.failed("No solver could solve this photo of a screen. Crop it to the stars "
                                        "(Image › Crop and rotate), or use Image › Solve with object hint.")
            failed.attempts = attempts
            return failed
        aided = self.solve_with_phone_aids(image, ctx, attempts, interactive)
        if aided is not None:
            return aided
        failed = SolveResult.failed("No solver could solve this image.")
        failed.attempts = attempts
        return failed

    def _ask_once(self, image: ImageData, ctx: TaskContext) -> TaskContext:
        """Each question (upload to astrometry.net, phone photo?) is asked once per image.

        The answer is reused for every later attempt on the same image – the phone aids' extra tries, a
        new solve with an object hint, F5 – until another image (or the same file again) is opened.
        """
        import dataclasses
        if getattr(self, "_answers_for", None) is not image:
            self._answers_for = image
            self._answers: dict[str, bool] = {}
        answers = self._answers
        ask = ctx.confirm

        def confirm(title, text, remember_key="", **labels):
            key = remember_key or title
            if key not in answers:
                answers[key] = bool(ask(title, text, remember_key, **labels))
            return answers[key]
        return dataclasses.replace(ctx, confirm=confirm)

    # ------------------------------------------------------------------ phone and wide-field aids
    def _run_solver(self, solver, image: ImageData, ctx: TaskContext) -> SolveResult:
        t0 = time.monotonic()
        try:
            result = solver.solve(image, ctx)
        except Cancelled:
            raise
        except Exception as exc:
            log.exception("%s crashed", solver.name)
            why = str(exc) if isinstance(exc, (RuntimeError, TimeoutError)) else f"{type(exc).__name__}: {exc}"
            result = SolveResult.failed(why, solver.plugin_id, solver.name)
        if not result.elapsed_s:
            result.elapsed_s = time.monotonic() - t0
        return result

    def solve_with_phone_aids(self, image: ImageData, ctx: TaskContext, attempts: list[str],
                              interactive: bool = True) -> SolveResult | None:
        """Last try for phone / very wide-field photos: solve the cleaned-up centre, map it back."""
        from platesolver.core import phoneaids as pa

        settings = self.phone_aids
        mode = str(settings.get("mode") or "ask")
        if mode == "never" or image.header_wcs is not None:
            return None
        solvers = [s for s in self.solvers() if s.uses_pixels and s.is_available()[0]]
        if not solvers:
            return None
        fov = image.hints.fov_height_deg(image.height) or 0.0
        if image.format.upper() in ("FITS", "XISF") and fov < 5:
            return None        # an astro camera's file, not a phone photo or wide-field shot
        if mode == "ask":
            if not interactive:
                return None
            if not ctx.confirm("Not solved – a phone photo?", pa.question_text(image, pa.phone_clue(image)), "",
                               yes="Yes, try the phone aids", no="No"):
                attempts.append("Phone and wide-field aids: not used")
                ctx.log("Phone and wide-field aids: not used")
                return None
        ctx.log("Phone and wide-field aids: preparing the photo…")
        base, notes = pa.prepare_base(image, bool(settings.get("mask_foreground")))
        ctx.log("Phone aids: " + notes)
        allow_bin = bool(settings.get("bin2"))
        fractions = [float(x) / 100.0 for x in str(settings.get("centres") or "50,30").split(",") if x.strip()]
        t_start = time.monotonic()
        for frac in fractions:
            v = pa.make_variant(image, base, notes, frac, allow_bin)
            for solver in solvers:
                ctx.check_cancel()
                label = f"{solver.name} ({v.label})"
                ctx.log(f"Trying {label}…")
                result = self._run_solver(solver, v.image, ctx)
                if not result.success:
                    attempts.append(f"{label}: {result.message}")
                    ctx.log(f"{label}: {result.message}")
                    if result.message == "upload declined":
                        break
                    continue
                full = SolveResult.from_wcs(pa.wcs_to_full(result.wcs, v.x0, v.y0, v.factor), image.width,
                                            image.height, solver.plugin_id, solver.name)
                attempts.append(f"{label}: solved in {result.elapsed_s:.1f} s")
                ctx.log(f"Solved by {label} in {result.elapsed_s:.1f} s")
                note = f"Solved with the phone and wide-field aids ({v.notes})"
                if frac < 0.999 and settings.get("refine"):
                    refined_ok = False
                    for rfrac in [f for f in (1.0, 0.75) if f > frac + 0.01]:
                        rv = pa.make_variant(image, base, notes, rfrac, allow_bin)
                        rv.image.hints = pa.refine_hints(image, full.center_ra_deg, full.center_dec_deg,
                                                         full.pixel_scale_arcsec, rv.factor)
                        part = "whole photo" if rfrac >= 0.999 else f"middle {rfrac * 100:.0f} %"
                        rlabel = f"{solver.name} (phone aids, {part} near the position found)"
                        ctx.log(f"Trying {rlabel}…")
                        refined = self._run_solver(solver, rv.image, ctx)
                        if refined.success:
                            full = SolveResult.from_wcs(pa.wcs_to_full(refined.wcs, rv.x0, rv.y0, rv.factor),
                                                        image.width, image.height, solver.plugin_id, solver.name)
                            attempts.append(f"{rlabel}: solved in {refined.elapsed_s:.1f} s")
                            note += (". The whole photo was then solved near that position" if rfrac >= 0.999
                                     else f". The {part} was then solved near that position, so the solution "
                                          "is accurate except at the very edges")
                            refined_ok = True
                            break
                        attempts.append(f"{rlabel}: {refined.message}")
                else:
                    refined_ok = frac >= 0.999
                full.message = note
                full = self.correct_distortion(image, full, ctx, None, frac, after_aids=True)
                if full.message != note:
                    note = full.message                       # distortion corrected: edges are right now
                elif not refined_ok:
                    note += (". The solution of the centre is extended to the whole photo, so labels near "
                             "the edges may be a little off")
                ctx.log(note)
                full.message = note
                full.elapsed_s = time.monotonic() - t_start
                full.attempts = attempts
                return full
        ctx.log("Phone and wide-field aids: not solved either")
        return None

    # ------------------------------------------------------------------ lens distortion
    def correct_distortion(self, image: ImageData, result: SolveResult, ctx: TaskContext, lum=None,
                           start_fraction: float = 1.0, after_aids: bool = False) -> SolveResult:
        """Fit lens distortion with the built-in star catalogue (wide fields), keeping the result if it fails."""
        if self.screen_photo():
            return result     # a photo of a screen: the stars were imaged through a telescope, not this lens
        mode = str(self.distortion.get("correct") or "wide")
        if mode == "never" or (mode == "phone" and not after_aids) or result.wcs is None:
            return result
        if result.solver_id == "header_wcs" or getattr(result.wcs, "sip", None) is not None:
            return result                   # a stored solution, or one that already describes distortion
        field = max(result.fov_width_deg or 0, result.fov_height_deg or 0)
        if not after_aids and field <= 5:
            return result
        from platesolver.core.distortion import correct
        if lum is None:
            lum = image.luminance()
            if image.format.upper() in ("JPEG", "JPG", "HEIC", "HEIF"):
                from platesolver.core.phoneaids import soften
                lum = soften(lum)
        ctx.log("Measuring lens distortion with the star catalogue…")
        try:
            fit = correct(result.wcs, lum, start_fraction, ctx.log)
        except Cancelled:
            raise
        except Exception as exc:
            log.exception("Distortion fit failed")
            ctx.log(f"Distortion: not corrected ({exc})")
            return result
        if fit is None:
            return result
        new = SolveResult.from_wcs(fit.wcs, image.width, image.height, result.solver_id, result.solver_name)
        for attr in ("elapsed_s", "attempts", "scale_note"):
            setattr(new, attr, getattr(result, attr))
        summary = fit.summary()
        ctx.log(summary)
        new.message = (result.message.rstrip(". ") + ". " if result.message else "") + summary
        return new

    # ------------------------------------------------------------------ objects
    def find_objects(self, image: ImageData, solution: SolveResult, ctx: TaskContext) -> list[SkyObject]:
        """Catalogue lookup, then distances, then links.

        Problems (e.g. no internet) are collected in `self.object_errors` so the
        UI can say why the list is empty or incomplete.
        """
        self.object_errors: list[str] = []
        objects: list[SkyObject] = []
        catalogs = self.registry.of_kind(CatalogProvider)
        for cat in catalogs:
            ctx.check_cancel()
            if objects and getattr(cat, "only_as_fallback", lambda: False)():
                continue
            ok, why = cat.is_available()
            if not ok:
                ctx.log(f"{cat.name} skipped: {why}")
                continue
            ctx.log(f"Looking up objects in {cat.name}…")
            try:
                found = cat.find_objects(image, solution, ctx)
                ctx.log(f"{cat.name}: {len(found)} objects in the field")
                objects.extend(found)
            except Cancelled:
                raise
            except Exception as exc:
                _log_problem(exc, "%s failed", cat.name)
                self.object_errors.append(f"{cat.name}: {exc}")
                ctx.log(f"{cat.name} failed: {exc}")
        if len(catalogs) > 1 and objects:
            from platesolver.core.identifiers import merge_objects
            before = len(objects)
            objects = merge_objects(objects)
            if before != len(objects):
                ctx.log(f"Merged {before - len(objects)} objects found in more than one catalogue")
        for obj in objects:
            if obj.x is None or obj.y is None:
                obj.x, obj.y = solution.radec_to_pixel(obj.ra_deg, obj.dec_deg)
        if self.visibility.get("enabled"):
            from platesolver.core import visibility
            ctx.check_cancel()
            try:
                objects = visibility.apply(objects, image, solution, self.visibility, ctx.log, self.object_errors)
            except Cancelled:
                raise
            except Exception as exc:
                _log_problem(exc, "Visible stars failed")
                ctx.log(f"Visible stars: skipped ({exc})")
        if not objects:
            return objects

        resolvers = self.registry.of_kind(DistanceResolver)
        for r in resolvers:
            ctx.check_cancel()
            try:
                r.prepare(objects, ctx)
            except Cancelled:
                raise
            except Exception as exc:
                _log_problem(exc, "%s prepare failed", r.name)
                self.object_errors.append(f"{r.name}: {exc}")
                ctx.log(f"{r.name} failed: {exc}")
        for obj in objects:
            for r in resolvers:
                try:
                    obj.distance = r.resolve(obj, ctx)
                except Exception as exc:
                    log.warning("%s could not resolve %s: %s", r.name, obj.name, exc)
                if obj.distance is not None:
                    break
        n = sum(o.distance is not None for o in objects)
        ctx.log(f"Distances found for {n} of {len(objects)} objects")

        linkers = self.registry.of_kind(LinkProvider)
        for lp in linkers:
            ctx.check_cancel()
            try:
                lp.prepare(objects, ctx)
            except Cancelled:
                raise
            except Exception as exc:
                _log_problem(exc, "%s prepare failed", lp.name)
                self.object_errors.append(f"{lp.name}: {exc}")
                ctx.log(f"{lp.name} failed: {exc}")
            for obj in objects:
                try:
                    obj.links.extend(lp.links_for(obj, ctx))
                except Exception as exc:
                    log.warning("%s: no links for %s: %s", lp.name, obj.name, exc)
        return objects

    # ------------------------------------------------------------------ overlays
    def overlays(self) -> list[OverlayLayer]:
        return self.registry.of_kind(OverlayLayer)


def scale_check(image: ImageData, result: SolveResult) -> str:
    """Compare the scale the file promised with the measured one and explain a mismatch."""
    actual = result.pixel_scale_arcsec
    if not actual:
        return ""
    h = image.hints
    expected = h.scale_arcsec()
    if not expected:
        if result.solver_id == "header_wcs":
            return ""
        return (f"The file doesn't record focal length and pixel size, so the solver had to find the "
                f"scale ({actual:.2f}″/px) itself. Enter your telescope's focal length and camera pixel "
                f"size in Settings › Equipment to make solving faster.")
    ratio = actual / expected
    if abs(ratio - 1.0) <= 0.05:
        return ""
    if h.pixel_scale_arcsec:
        origin = f"the scale in the file ({h.source.get('scale', 'header')})"
    else:
        origin = (f"focal length {h.focal_length_mm:g} mm ({h.source.get('focal_length', '?')}) and pixel size "
                  f"{h.pixel_size_um:g} µm ({h.source.get('pixel_size', '?')})")
    text = f"Expected {expected:.2f}″/px from {origin}, measured {actual:.2f}″/px. "
    for factor, cause in ((2, "The image looks binned 2×2 or reduced to half size."),
                          (3, "The image looks binned 3×3 or reduced to a third."),
                          (0.5, "The image looks drizzled 2× or enlarged to double size.")):
        if abs(ratio / factor - 1.0) < 0.06:
            return text + cause
    if any(v == "Settings › Equipment" for v in h.source.values()):
        return text + "Check the focal length and pixel size in Settings › Equipment."
    return text + "Check the focal length and pixel size recorded by the capture software."


def _log_problem(exc: Exception, msg: str, *args) -> None:
    """Network trouble is expected (offline, server busy): one line. Anything else: full traceback."""
    from platesolver.core.net import NetworkError
    if isinstance(exc, NetworkError):
        log.warning(msg + ": %s", *args, exc)
    else:
        log.exception(msg, *args)
