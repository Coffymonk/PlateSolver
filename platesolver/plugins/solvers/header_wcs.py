# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Use a plate solution that is already stored in the FITS header."""
from __future__ import annotations

from platesolver.core.interfaces import Solver
from platesolver.core.models import ImageData, SolveResult
from platesolver.core.plugin import TaskContext


class HeaderWcsSolver(Solver):
    plugin_id = "header_wcs"
    name = "Existing solution in file"
    description = ("If the FITS file was already plate solved (it has WCS keywords in its header), "
                   "that solution is used straight away. Disable this to always solve again.")
    priority = 10
    uses_pixels = False

    def solve(self, image: ImageData, ctx: TaskContext) -> SolveResult:
        if image.header_wcs is None:
            return SolveResult.failed("no solution stored in the file", self.plugin_id, self.name)
        return SolveResult.from_wcs(image.header_wcs, image.width, image.height,
                                    self.plugin_id, self.name)
