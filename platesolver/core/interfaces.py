# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""The extension points. Subclass one of these to add a feature.

Pipeline:  ImageLoader -> Solver -> CatalogProvider -> DistanceResolver
           -> LinkProvider -> OverlayLayer (drawing on the image)

Put the new class in a module under `platesolver/plugins/<kind>/` (or in the
user plugin folder, see paths.user_plugin_dir) and it is found automatically.
"""
from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import Protocol, Sequence

from platesolver.core.models import Distance, ImageData, Link, SkyObject, SolveResult
from platesolver.core.plugin import Plugin, TaskContext


class ImageLoader(Plugin):
    kind = "loader"
    kind_label = "Image formats"
    extensions: tuple[str, ...] = ()          # lower case, with dot: (".fits", ".fit")
    format_name: str = ""                     # for the file dialog filter, e.g. "FITS"

    def can_load(self, path: Path) -> bool:
        return path.suffix.lower() in self.extensions

    @abstractmethod
    def load(self, path: Path) -> ImageData:
        ...


class Solver(Plugin):
    kind = "solver"
    kind_label = "Plate solvers"
    uses_pixels: bool = True        # False for solvers that only read the file header
    retry_prepared: bool = False    # may be tried again with the cleaned copy (Settings › Solving aids)

    @abstractmethod
    def solve(self, image: ImageData, ctx: TaskContext) -> SolveResult:
        """Return SolveResult.from_wcs(...) on success or SolveResult.failed(...)."""


class CatalogProvider(Plugin):
    kind = "catalog"
    kind_label = "Object catalogues"

    @abstractmethod
    def find_objects(self, image: ImageData, solution: SolveResult, ctx: TaskContext) -> list[SkyObject]:
        ...


class DistanceResolver(Plugin):
    """Tried in priority order for each object; the first one that returns a distance wins."""
    kind = "distance"
    kind_label = "Distance sources"

    def prepare(self, objects: Sequence[SkyObject], ctx: TaskContext) -> None:
        """Optional: fetch data for all objects at once before resolve() is called per object."""

    @abstractmethod
    def resolve(self, obj: SkyObject, ctx: TaskContext) -> Distance | None:
        ...


class LinkProvider(Plugin):
    """All link modules contribute; the first link of an object opens on double-click."""
    kind = "link"
    kind_label = "Information links"

    def prepare(self, objects: Sequence[SkyObject], ctx: TaskContext) -> None:
        """Optional: batch work (e.g. one web request for all objects) before links_for()."""

    @abstractmethod
    def links_for(self, obj: SkyObject, ctx: TaskContext) -> list[Link]:
        ...


class OverlayPainter(Protocol):
    """Drawing surface given to overlay plugins. Coordinates are image pixels.

    Line widths and text sizes are in screen pixels, so they stay readable at
    any zoom level. Colours are strings like "#ffcc00".
    """

    def line(self, x1: float, y1: float, x2: float, y2: float, color: str, width: float = 1.5) -> None: ...
    def polyline(self, points: Sequence[tuple[float, float]], color: str, width: float = 1.5, closed: bool = False) -> None: ...
    def arrow(self, x1: float, y1: float, x2: float, y2: float, color: str, width: float = 1.5) -> None: ...
    def circle(self, x: float, y: float, radius: float, color: str, width: float = 1.5) -> None: ...
    def ellipse(self, x: float, y: float, rx: float, ry: float, angle_deg: float, color: str, width: float = 1.5) -> None: ...
    def text(self, x: float, y: float, text: str, color: str, size: float = 10, anchor: str = "left") -> None: ...


class OverlayLayer(Plugin):
    kind = "overlay"
    kind_label = "Overlays"
    visible_by_default: bool = True    # initial state of its switch in View › Overlays

    @abstractmethod
    def render(self, painter: OverlayPainter, image: ImageData, solution: SolveResult,
               objects: Sequence[SkyObject]) -> None:
        ...


ALL_KINDS: tuple[type[Plugin], ...] = (
    ImageLoader, Solver, CatalogProvider, DistanceResolver, LinkProvider, OverlayLayer,
)
