# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Equipment profiles: one click sets everything that differs between camera types.

A profile holds the settings that depend on the camera: focal length, pixel size, sensor size,
drizzle detection, the 35 mm-equivalent lens for phone photos without EXIF, and the ASTAP options
that matter for the field size. Everything else stays shared.

Built-in templates are starting points; your own profiles (e.g. "Redcat 71 + ASI585MC Pro") are
made from a template plus a camera from the catalogue. With "Choose the profile automatically",
an image's metadata picks the profile: a FITS from a ZWO camera gets your astro profile, a photo
with Apple/Samsung EXIF the smartphone profile, and so on.
"""
from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field

from platesolver.core import cameras
from platesolver.core.settings import BOOL, SettingField, SettingsSection

PROFILE_KEYS = {
    "equipment": ("focal_length", "pixel_size", "sensor_width", "sensor_height", "detect_drizzle",
                  "override_file", "lens_35mm"),
    "solver.astap": ("database", "fov_override", "any_scale_retry"),
}
KINDS = {
    "astro": "Astro camera on a telescope",
    "dslr_scope": "DSLR / mirrorless on a telescope",
    "dslr_lens": "DSLR / mirrorless with a camera lens",
    "phone": "Smartphone",
    "screen": "Photo of a screen or print",
}

PHONE_MAKERS = ("apple", "samsung", "google", "xiaomi", "oneplus", "huawei", "oppo", "motorola", "honor",
                "vivo", "realme", "nothing", "fairphone")
CAMERA_MAKERS = ("canon", "nikon", "sony", "fujifilm", "olympus", "om digital", "panasonic", "pentax", "ricoh",
                 "leica", "hasselblad", "sigma")
ASTRO_WORDS = ("zwo", "asi", "qhy", "touptek", "player one", "altair", "svbony", "atik", "moravian", "sbig",
               "starlight xpress")


def _values(fl=0.0, px=0.0, sw=0, sh=0, drizzle=True, override=False, f35=0.0,
            database="", fov=0.0, any_scale=True) -> dict:
    return {"equipment": {"focal_length": fl, "pixel_size": px, "sensor_width": sw, "sensor_height": sh,
                          "detect_drizzle": drizzle, "override_file": override, "lens_35mm": f35},
            "solver.astap": {"database": database, "fov_override": fov, "any_scale_retry": any_scale}}


@dataclass
class Profile:
    id: str
    name: str
    kind: str
    values: dict
    camera: str = ""
    builtin: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "values": self.values,
                "camera": self.camera, "note": self.note}

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        return cls(d["id"], d.get("name", "Profile"), d.get("kind", "astro"), d.get("values") or _values(),
                   d.get("camera", ""), False, d.get("note", ""))

    def summary(self) -> str:
        e = self.values.get("equipment", {})
        if self.kind == "screen":
            fov = float(self.values.get("solver.astap", {}).get("fov_override") or 0)
            return ("The photo's lens information is ignored; " +
                    (f"the stars shown cover about {fov:g}° of sky (height)" if fov else
                     "the stars' scale is found while solving (slower; an object hint makes it quick)"))
        if self.kind in ("phone", "dslr_lens"):
            f35 = e.get("lens_35mm") or 0
            return ("Scale from the photo's EXIF" +
                    (f"; without EXIF a {f35:g} mm full-frame-equivalent lens is assumed" if f35 else ""))
        fl, px = float(e.get("focal_length") or 0), float(e.get("pixel_size") or 0)
        if not (fl and px):
            return "Choose a camera and enter the focal length"
        from platesolver.core.equipment import image_scale
        s = f"{fl:g} mm, {px:g} µm pixels: {image_scale(fl, px):.2f}″/px"
        if e.get("sensor_width") and e.get("sensor_height"):
            s += f", sensor {e['sensor_width']} × {e['sensor_height']}"
        return s


TEMPLATES = [
    Profile("tpl-astro", "Template: astro camera on a telescope", "astro", _values(), builtin=True,
            note="ZWO, ToupTek, Player One, QHY … Pick your camera and enter the telescope's focal length."),
    Profile("tpl-dslr-scope", "Template: DSLR / mirrorless on a telescope", "dslr_scope", _values(), builtin=True,
            note="Camera body at the telescope's focus. Pick the body and enter the focal length."),
    Profile("tpl-dslr-lens", "Template: DSLR / mirrorless with a camera lens", "dslr_lens",
            _values(drizzle=False), builtin=True,
            note="The image scale comes from each photo's EXIF (focal length and sensor)."),
    Profile("tpl-phone", "Template: smartphone, main camera (26 mm)", "phone", _values(drizzle=False, f35=26.0),
            builtin=True, note="iPhone / Android. Uses EXIF when present, otherwise a 26 mm-equivalent lens. "
                               "For very wide photos astrometry.net is often the better solver."),
    Profile("tpl-phone-uw", "Template: smartphone, ultra-wide (13 mm)", "phone", _values(drizzle=False, f35=13.0),
            builtin=True, note="0.5× camera. Very wide and distorted; astrometry.net is usually needed."),
    Profile("tpl-screen", "Template: photo of a screen or print", "screen", _values(drizzle=False), builtin=True,
            note="A phone or camera photo of stars shown on a tablet, monitor or printout (e.g. a live view in "
                 "ASIAIR, N.I.N.A. or a smart-telescope app). The photo's own lens information is ignored, "
                 "because the stars' scale comes from the telescope that took the picture on the screen. Crop "
                 "to the star area (Image › Crop and rotate); an object hint (Image › Solve with object hint) helps most."),
]


class ProfileSettings(SettingsSection):
    section_id = "profiles"
    name = "Profiles"
    description = ("Profiles set everything that depends on your camera in one go. Choose one in the toolbar, "
                   "and create your own in Tools › Profiles….")

    def settings_schema(self) -> list[SettingField]:
        return [SettingField("auto_switch", "Choose the profile automatically from the image's metadata", BOOL,
                             True, help="E.g. a FITS from a ZWO camera uses your astro-camera profile, a photo "
                                        "with Apple or Samsung EXIF the smartphone profile. The Log says "
                                        "when the profile changes.")]


TELESCOPE_DATABASES = ("d50", "d80", "v50", "d20", "h18", "h17", "d05")
WIDE_DATABASES = ("w08", "g05")


@dataclass
class CheckItem:
    ok: bool
    text: str
    required: bool = True       # False: advice; not having it doesn't stop solving


class ProfileManager:
    def __init__(self, store, registry=None):
        self.store = store
        self.registry = registry
        self._migrate()

    # ---------------------------------------------------------------- checklist
    def checklist(self, profile: Profile) -> list[CheckItem]:
        """What else is needed to plate solve with this profile, checked against what is installed/set."""
        e = profile.values.get("equipment", {})
        astap = self.registry.get("solver.astap") if self.registry else None
        an = self.registry.get("solver.astrometry_net") if self.registry else None
        dbs = astap.installed_databases() if astap else []
        has_astap = bool(astap and astap.executable())
        has_an = bool(an and an.enabled and str(an.setting("api_key") or "").strip())
        items = [CheckItem(has_astap, "ASTAP found (Settings › Plate solvers › ASTAP)")]
        if profile.kind in ("astro", "dslr_scope"):
            fl, px = float(e.get("focal_length") or 0), float(e.get("pixel_size") or 0)
            items += [
                CheckItem(fl > 0, "Telescope focal length entered, including any reducer or Barlow"),
                CheckItem(px > 0, "Pixel size entered – choose your camera in this profile"),
                CheckItem(bool(e.get("sensor_width") and e.get("sensor_height")),
                          "Sensor size entered, so drizzled images are recognised", required=False),
                CheckItem(any(d in dbs for d in TELESCOPE_DATABASES),
                          "ASTAP star database for telescope fields installed (D50 or D80; D20/D05 also work)"
                          + (f" – found: {', '.join(d.upper() for d in dbs)}" if dbs else "")),
            ]
        elif profile.kind == "dslr_lens":
            items += [
                CheckItem(any(d in dbs for d in WIDE_DATABASES),
                          "ASTAP's wide-field database W08 (or G05) installed – lenses up to ~150 mm give fields "
                          "wider than 10°" + (f" – found: {', '.join(d.upper() for d in dbs)}" if dbs else "")),
                CheckItem(bool(e.get("lens_35mm")), "Photos keep their EXIF (focal length). For exported files "
                          "without EXIF, enter 'Lens without EXIF' in your own profile", required=False),
                CheckItem(any(d in dbs for d in TELESCOPE_DATABASES),
                          "D50/D80 database for long lenses (fields under 10°)", required=False),
            ]
        elif profile.kind == "screen":
            fov = float(profile.values.get("solver.astap", {}).get("fov_override") or 0)
            items += [
                CheckItem(any(d in dbs for d in TELESCOPE_DATABASES + WIDE_DATABASES),
                          "An ASTAP star database installed (D50 suits most telescope pictures shown on a screen)"
                          + (f" – found: {', '.join(d.upper() for d in dbs)}" if dbs else "")),
                CheckItem(True, "Crop to the area with stars with Image › Crop and rotate (leave out buttons, "
                          "text and the screen's edges)", required=False),
                CheckItem(True, "If you know what it shows, use Image › Solve with object hint – much faster",
                          required=False),
                CheckItem(bool(fov), "Height of the sky shown entered (optional; e.g. 0.6° for a 576 mm telescope "
                          "with an ASI585MC) – narrows the search", required=False),
            ]
        elif profile.kind == "phone":
            f35 = float(e.get("lens_35mm") or 0)
            items += [
                CheckItem(any(d in dbs for d in WIDE_DATABASES),
                          "ASTAP's wide-field database W08 (or G05) installed – phone photos cover 50–110°"
                          + (f" – found: {', '.join(d.upper() for d in dbs)}" if dbs else "")),
                CheckItem(True, f"Photos without EXIF are assumed to be taken with a {f35:g} mm-equivalent lens"
                          + (" (iPhone 14 Pro and newer: 24 mm – set it in your own profile)" if f35 == 26 else "")),
                CheckItem(has_an, "astrometry.net set up with an API key – it copes much better with wide, "
                          "distorted phone photos" + (" (practically required for ultra-wide)" if f35 and f35 < 20
                                                      else ""), required=bool(f35 and f35 < 20)),
                CheckItem(True, "Use the original photo: messaging apps strip EXIF and recompress", required=False),
            ]
        items.append(CheckItem(True, "Field of view height and star database are left on automatic by the "
                                     "profile – no need to set them by hand", required=False))
        return items

    def missing(self, profile: Profile) -> list[str]:
        return [c.text for c in self.checklist(profile) if c.required and not c.ok]

    # ---------------------------------------------------------------- storage
    def user_profiles(self) -> list[Profile]:
        return [Profile.from_dict(d) for d in (self.store.get("profiles", "user") or [])]

    def profiles(self) -> list[Profile]:
        return self.user_profiles() + [copy.deepcopy(t) for t in TEMPLATES]

    def get(self, profile_id: str) -> Profile | None:
        return next((p for p in self.profiles() if p.id == profile_id), None)

    @property
    def active_id(self) -> str:
        return self.store.get("profiles", "active") or ""

    def active(self) -> Profile | None:
        return self.get(self.active_id)

    def save(self, profile: Profile) -> Profile:
        if profile.builtin:
            raise ValueError("built-in templates can't be changed; make your own profile from it")
        users = [p for p in self.user_profiles() if p.id != profile.id]
        names = {p.name.lower() for p in users}
        base, n = profile.name.strip() or "My profile", 2
        name = base
        while name.lower() in names:
            name, n = f"{base} ({n})", n + 1
        profile.name = name
        existing = [p.id for p in self.user_profiles()]
        lst = [p.to_dict() for p in self.user_profiles()]
        if profile.id in existing:
            lst[existing.index(profile.id)] = profile.to_dict()
        else:
            lst.append(profile.to_dict())
        self.store.set("profiles", "user", lst)
        return profile

    def delete(self, profile_id: str) -> None:
        self.store.set("profiles", "user", [p.to_dict() for p in self.user_profiles() if p.id != profile_id])
        for key in ("active", "chosen"):
            if self.store.get("profiles", key) == profile_id:
                self.store.set("profiles", key, "")

    # ---------------------------------------------------------------- applying
    def current_values(self) -> dict:
        from platesolver.core.equipment import EquipmentSettings
        eq = self.store.section(EquipmentSettings())
        out = {"equipment": {k: eq.get(k) for k in PROFILE_KEYS["equipment"]}, "solver.astap": {}}
        for k in PROFILE_KEYS["solver.astap"]:
            v = self.store.get("solver.astap", k)
            if v is not None:
                out["solver.astap"][k] = v
        return out

    @property
    def chosen_id(self) -> str:
        """The profile picked by the user (toolbar / Profiles window); automatic switches return to it."""
        return self.store.get("profiles", "chosen") or self.active_id

    def apply(self, profile: Profile) -> None:
        """Write the profile's values into the settings (used for automatic, per-image switches)."""
        for section, keys in PROFILE_KEYS.items():
            vals = profile.values.get(section, {})
            for k in keys:
                if k in vals:
                    self.store.set(section, k, vals[k])
        self.store.set("profiles", "active", profile.id)

    def choose(self, profile: Profile) -> None:
        """The user's own choice: applied now and returned to after automatic switches."""
        self.apply(profile)
        self.store.set("profiles", "chosen", profile.id)

    def new_profile(self, name: str, kind: str, camera_id: str = "", focal_length: float = 0.0,
                    note: str = "") -> Profile:
        cam = cameras.get(camera_id) if camera_id else None
        if kind in ("astro", "dslr_scope"):
            vals = _values(fl=focal_length, px=cam.pixel_um if cam else 0.0, sw=cam.width if cam else 0,
                           sh=cam.height if cam else 0, drizzle=True)
        elif kind == "phone":
            vals = _values(drizzle=False, f35=cam.lens_35mm if cam and cam.lens_35mm else 26.0)
        else:
            vals = _values(drizzle=False)
        return Profile("user-" + uuid.uuid4().hex[:8], name, kind, vals, camera_id, False, note)

    def _migrate(self) -> None:
        """First run with profiles: keep the current equipment as 'My telescope'."""
        if self.store.get("profiles", "user") is not None:
            return
        cur = self.current_values()
        e = cur["equipment"]
        fl, px = float(e.get("focal_length") or 0), float(e.get("pixel_size") or 0)
        if fl and px:
            p = Profile("user-" + uuid.uuid4().hex[:8], f"My telescope ({fl:g} mm, {px:g} µm)",
                        "astro", cur, "", False, "Made from your earlier Equipment settings. Rename it in "
                                                 "Tools › Profiles…")
            self.store.set("profiles", "user", [p.to_dict()])
            self.store.set("profiles", "active", p.id)
            self.store.set("profiles", "chosen", p.id)
        else:
            self.store.set("profiles", "user", [])

    # ---------------------------------------------------------------- automatic choice
    def detect(self, image) -> tuple[Profile | None, str]:
        """The profile an image's metadata points to, and why; (None, '') when there is no clue."""
        hdr = {str(k).upper(): str(v) for k, v in (image.header or {}).items()}
        make = hdr.get("MAKE", "").lower()
        model = hdr.get("CAMERA", "") or hdr.get("MODEL", "")
        instrument = hdr.get("INSTRUME", "")
        users = self.user_profiles()

        def pick(kind: str, cam_id: str = "", allow_template: bool = False) -> Profile | None:
            if cam_id:
                hit = next((p for p in users if p.camera == cam_id), None)
                if hit:
                    return hit
            same = [p for p in users if p.kind == kind]
            if self.active_id in [p.id for p in same]:
                return self.active()
            if same:
                return same[0]
            if allow_template:
                return next((copy.deepcopy(t) for t in TEMPLATES if t.kind == kind), None)
            return None

        if any(make.startswith(m) for m in PHONE_MAKERS):
            return pick("phone", allow_template=True), f"photo from {hdr.get('MAKE', '')} {model}".strip()
        if any(make.startswith(m) for m in CAMERA_MAKERS):
            from_lens = bool(image.hints.focal_length_mm and image.hints.source.get("focal_length") == "EXIF")
            if from_lens:
                return pick("dslr_lens", allow_template=True), f"photo from a {hdr.get('MAKE', '')} camera with a lens"
            return pick("dslr_scope"), f"photo from a {hdr.get('MAKE', '')} camera without lens data"
        text = f"{instrument} {make}".lower()
        cam = cameras.match_instrument(instrument)
        if cam or any(w in text for w in ASTRO_WORDS):
            return pick("astro", cam.id if cam else ""), f"FITS from {instrument or make}"
        return None, ""

    def auto_select(self, image) -> str:
        """Switch to the detected profile (if enabled and different). Returns a log message or ''."""
        if not self.store.section(ProfileSettings()).get("auto_switch"):
            return ""
        chosen = self.get(self.chosen_id)
        if chosen is not None and chosen.kind == "screen":
            # a photo of a screen carries the phone's EXIF; that must not switch to a phone profile
            if chosen.id == self.active_id:
                return ""
            self.apply(chosen)
            return f"Profile: {chosen.name} (your choice, for photos of a screen)"
        prof, why = self.detect(image)
        if prof is None:
            chosen = self.get(self.chosen_id)
            if chosen is None or chosen.id == self.active_id:
                return ""
            prof, why = chosen, "your chosen profile; the file says nothing about the camera"
        if prof.id == self.active_id:
            return ""
        self.apply(prof)
        return f"Profile: {prof.name} ({why})"
