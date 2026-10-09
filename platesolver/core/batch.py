# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Batch mode: plate solve every image in a folder. No Qt here; the dialog lives in ui/batch_dialog.py."""
from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from platesolver.core.export import write_objects_csv, write_solution_into_fits, write_wcs_file
from platesolver.core.formatting import format_dec, format_ra
from platesolver.core.plugin import Cancelled, TaskContext
from platesolver.core.settings import BOOL, SettingField, SettingsSection

log = logging.getLogger(__name__)


class BatchSettings(SettingsSection):
    section_id = "batch"
    name = "Batch solving"
    description = "Defaults for Tools › Batch solve folder. They can also be changed in the batch window each time."

    def settings_schema(self) -> list[SettingField]:
        return [
            SettingField("recursive", "Include subfolders", BOOL, False),
            SettingField("skip_solved", "Skip FITS files that are already plate solved", BOOL, True),
            SettingField("write_wcs", "Save a .wcs file next to each solved image", BOOL, True,
                         help="A small side-car file with the solution, readable by most astronomy software."),
            SettingField("write_fits", "Write the solution into FITS files' own headers", BOOL, False,
                         help="Changes your FITS files (only the header, never the image data). Then they open "
                              "already solved, in PlateSolver and other programs."),
            SettingField("objects_csv", "Also look up objects and save a CSV list per image", BOOL, False,
                         help="Slower: needs the online lookups for every image."),
        ]


@dataclass
class BatchOptions:
    recursive: bool = False
    skip_solved: bool = True
    write_wcs: bool = True
    write_fits: bool = False
    objects_csv: bool = False
    ly_decimals: int = 1
    csv_separator: str = "comma"


@dataclass
class BatchItem:
    path: Path
    status: str = "waiting"          # solved / already solved / failed / error / skipped
    message: str = ""
    solver: str = ""
    ra_deg: float | None = None
    dec_deg: float | None = None
    scale: float | None = None
    rotation: float | None = None
    fov_w: float | None = None
    fov_h: float | None = None
    seconds: float = 0.0
    objects: int | None = None
    outputs: list[str] = field(default_factory=list)


def find_images(folder: Path, extensions: set[str], recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    files = [p for p in Path(folder).glob(pattern) if p.is_file() and p.suffix.lower() in extensions]
    # skip our own side-car and work files
    return sorted(p for p in files if not p.name.startswith("platesolver_") and p.suffix.lower() != ".wcs")


def run_batch(pipeline, files: list[Path], options: BatchOptions, ctx: TaskContext,
              on_item=lambda index, item: None) -> list[BatchItem]:
    items = [BatchItem(p) for p in files]
    for i, item in enumerate(items):
        ctx.check_cancel()
        ctx.progress(i, len(items))
        ctx.log(f"[{i + 1}/{len(items)}] {item.path.name}")
        t0 = time.monotonic()
        try:
            image = pipeline.load(item.path)
            if options.skip_solved and image.header_wcs is not None:
                item.status = "already solved"
            result = pipeline.solve(image, ctx, interactive=False)
            item.seconds = time.monotonic() - t0
            if not result.success and "probably a starless image" in result.message:
                item.status = "skipped"
                item.message = result.message.replace("Not solved: ", "")
            elif not result.success:
                item.status = "failed"
                item.message = "; ".join(result.attempts[-2:]) or result.message
            else:
                if item.status != "already solved":
                    item.status = "solved"
                item.solver = result.solver_name
                item.ra_deg, item.dec_deg = result.center_ra_deg, result.center_dec_deg
                item.scale, item.rotation = result.pixel_scale_arcsec, result.rotation_deg
                item.fov_w, item.fov_h = result.fov_width_deg, result.fov_height_deg
                new_solution = item.status == "solved"
                if options.write_wcs and new_solution:
                    out = write_wcs_file(item.path.with_suffix(".wcs"), result, image)
                    item.outputs.append(out.name)
                if options.write_fits and new_solution and image.format == "FITS":
                    write_solution_into_fits(item.path, result, image)
                    item.outputs.append("FITS header")
                if options.objects_csv:
                    objects = pipeline.find_objects(image, result, ctx)
                    item.objects = len(objects)
                    out = item.path.with_name(item.path.stem + "_objects.csv")
                    write_objects_csv(out, objects, options.ly_decimals, options.csv_separator)
                    item.outputs.append(out.name)
        except Cancelled:
            item.status = "cancelled"
            on_item(i, item)
            raise
        except Exception as exc:
            log.exception("Batch: %s failed", item.path)
            item.status = "error"
            item.message = f"{type(exc).__name__}: {exc}"
            item.seconds = time.monotonic() - t0
        on_item(i, item)
    ctx.progress(len(items), len(items))
    return items


def write_summary_csv(path: Path, items: list[BatchItem]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "status", "solver", "ra_deg", "dec_deg", "ra_hms", "dec_dms", "scale_arcsec_per_px",
                    "rotation_deg", "field_width_deg", "field_height_deg", "seconds", "objects", "outputs",
                    "message"])
        for it in items:
            w.writerow([str(it.path), it.status, it.solver,
                        "" if it.ra_deg is None else f"{it.ra_deg:.6f}",
                        "" if it.dec_deg is None else f"{it.dec_deg:.6f}",
                        "" if it.ra_deg is None else format_ra(it.ra_deg),
                        "" if it.dec_deg is None else format_dec(it.dec_deg),
                        "" if it.scale is None else f"{it.scale:.4f}",
                        "" if it.rotation is None else f"{it.rotation:.2f}",
                        "" if it.fov_w is None else f"{it.fov_w:.4f}",
                        "" if it.fov_h is None else f"{it.fov_h:.4f}",
                        f"{it.seconds:.1f}", "" if it.objects is None else it.objects,
                        "; ".join(it.outputs), it.message])
