# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Link to the object's SIMBAD page - always available, so every object has at least one link."""
from __future__ import annotations

from platesolver.core.interfaces import LinkProvider
from platesolver.core.models import Link, SkyObject
from platesolver.core.plugin import TaskContext
from platesolver.services import simbad


class SimbadLinks(LinkProvider):
    plugin_id = "simbad_page"
    name = "SIMBAD page"
    description = ("Adds a link to the object's page in the SIMBAD database, with all its measurements and "
                   "references. Opens on double-click when there is no Wikipedia article.")
    priority = 90

    def links_for(self, obj: SkyObject, ctx: TaskContext) -> list[Link]:
        ident = obj.extra.get("simbad_main_id") or obj.name
        return [Link(f"SIMBAD: {obj.name}", simbad.page_url(ident), "SIMBAD")]
