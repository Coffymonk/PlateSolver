# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Right-hand panel: objects list, solution details and log."""
from __future__ import annotations

import html
import time

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit,
                               QScrollArea, QStackedWidget, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from platesolver.core.formatting import format_angle, format_dec, format_ly, format_ra
from platesolver.core.models import ImageData, SkyObject, SolveResult
from platesolver.core.objectfilter import ObjectFilter
from platesolver.ui import theme
from platesolver.ui.object_filter import FilterButton, FilterPanel
from platesolver.ui.object_info import card_html, install_lasting_item_tooltips, size_text


_ICON: QIcon | None = None


def _visible_icon() -> QIcon:
    """A small green dot: the object can be seen in the image."""
    global _ICON
    if _ICON is None:
        pm = QPixmap(12, 12)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(theme.OK))
        p.setPen(Qt.NoPen)
        p.drawEllipse(3, 3, 6, 6)
        p.end()
        _ICON = QIcon(pm)
    return _ICON


def _muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    lab.setAlignment(Qt.AlignCenter)
    return lab


class _ObjectItem(QTreeWidgetItem):
    """Sorts the distance column by value, not by text."""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a, b = self.data(col, Qt.UserRole + 1), other.data(col, Qt.UserRole + 1)
        if a is not None and b is not None:
            return a < b
        return self.text(col).lower() < other.text(col).lower()


CATEGORY_LABELS = {"galaxy": "Galaxies", "nebula": "Nebulae", "cluster": "Star clusters", "star": "Stars",
                   "other": "Other"}


class ObjectsPanel(QWidget):
    object_selected = Signal(object)    # SkyObject
    object_activated = Signal(object)
    filter_changed = Signal()
    filter_saved = Signal(dict)       # the filter choices, to remember them between sessions

    COLS = ("Object", "Distance", "Size", "Type")
    DIST, SIZE, TYPE = 1, 2, 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search objects…  (name, type, catalogue number)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.filter_panel = FilterPanel(self)
        self.filter_btn = FilterButton(self.filter_panel)
        self.filter_panel.changed.connect(self._filter_panel_changed)
        search_row = QHBoxLayout()
        search_row.setSpacing(2)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.filter_btn)
        self.cat_buttons: dict[str, QToolButton] = {}
        cats = QHBoxLayout()
        cats.setSpacing(4)
        for cat, label in CATEGORY_LABELS.items():
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setChecked(True)
            b.setToolTip(f"Show or hide {label.lower()}")
            b.toggled.connect(self._apply_filter)
            b.setStyleSheet("QToolButton { padding: 2px 6px; border: 1px solid #2a303a; border-radius: 9px; }"
                            f"QToolButton:checked {{ background: #25405f; border-color: {theme.ACCENT}; }}")
            self.cat_buttons[cat] = b
            cats.addWidget(b)
        cats.addStretch(1)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(self.COLS)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemSelectionChanged.connect(self._selected)
        self.tree.itemDoubleClicked.connect(self._activated)
        install_lasting_item_tooltips(self.tree)
        self.empty = _muted("Solve an image to list the objects in it.")
        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.tree)
        self.summary = QLabel("")
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        hint = QLabel("Click: show on image · Double-click: open information · Right-click: more")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.addLayout(search_row)
        lay.addLayout(cats)
        lay.addWidget(self.summary)
        lay.addWidget(self.stack, 1)
        lay.addWidget(hint)
        self._decimals = 1
        self._objects: list[SkyObject] = []
        self.card_options = {"show_size": True, "show_light": True}

    # ------------------------------------------------------------------ content
    def set_message(self, text: str):
        self.empty.setText(text)
        self.summary.setText("")
        self._objects = []
        self.stack.setCurrentWidget(self.empty)

    def set_objects(self, objects: list[SkyObject], decimals: int, note: str = "", size_column: bool = True):
        self._objects = objects
        self._decimals = decimals
        self._note = note
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self.filter_panel.set_objects(objects)
        for obj in objects:
            dist = format_ly(obj.distance.light_years, decimals) if obj.distance else "–"
            size = size_text(obj, decimals) or "–"
            it = _ObjectItem([obj.display_name, dist, size, obj.object_type])
            it.setData(0, Qt.UserRole, obj)
            if obj.extra.get("visible") is True:
                it.setIcon(0, _visible_icon())
            it.setData(self.DIST, Qt.UserRole + 1, obj.distance.light_years if obj.distance else float("inf"))
            psize = obj.physical_size_ly()
            it.setData(self.SIZE, Qt.UserRole + 1, psize if psize is not None else float("inf"))
            tip = card_html(obj, decimals, **self.card_options)
            for col in range(len(self.COLS)):
                it.setToolTip(col, tip)
            if obj.distance and obj.distance.method == "redshift":
                it.setForeground(self.DIST, QColor(theme.MUTED))
            self.tree.addTopLevelItem(it)
        # keep the catalogue order (most prominent first) until the user clicks a column header
        self.tree.header().setSortIndicator(-1, Qt.AscendingOrder)
        self.tree.setSortingEnabled(True)
        self.tree.setColumnHidden(self.SIZE, not size_column)
        for i in range(len(self.COLS)):
            self.tree.resizeColumnToContents(i)
        self.tree.setColumnWidth(0, min(self.tree.columnWidth(0) + 8, 190))
        self.tree.setColumnWidth(self.DIST, self.tree.columnWidth(self.DIST) + 8)
        if not objects:
            self.set_message(note or "No catalogued objects found in this field.")
            return
        self.stack.setCurrentWidget(self.tree)
        self._apply_filter()

    def _update_summary(self, shown: int):
        objects = self._objects
        counts = {}
        for o in objects:
            counts[o.category] = counts.get(o.category, 0) + 1
        singular = {"galaxy": "galaxy", "nebula": "nebula", "cluster": "star cluster", "star": "star",
                    "other": "other"}
        parts = [f"{n} {singular[c] if n == 1 else CATEGORY_LABELS.get(c, c).lower()}"
                 for c, n in counts.items()]
        with_dist = sum(1 for o in objects if o.distance)
        text = f"{len(objects)} objects: " + ", ".join(parts) + f". Distances for {with_dist}."
        if shown != len(objects):
            text += f" Showing {shown}."
        described = self.filter_panel.filter().describe()
        if described:
            text += f"\nFilter: {described}."
        note = getattr(self, "_note", "")
        self.summary.setText(text + (f"\n{note}" if note else ""))

    # ------------------------------------------------------------------ filter
    def matches(self, obj: SkyObject) -> bool:
        btn = self.cat_buttons.get(obj.category) or self.cat_buttons["other"]
        if not btn.isChecked():
            return False
        if not self.filter_panel.filter().matches(obj):
            return False
        q = self.search.text().strip().lower()
        if not q:
            return True
        hay = " ".join([obj.name, obj.common_name, obj.object_type] + obj.aliases +
                       [str(i) for i in obj.extra.get("identifiers") or []]).lower()
        hay_compact = hay.replace(" ", "")
        return all(w in hay or w.replace(" ", "") in hay_compact for w in q.split())

    def set_filter(self, f: ObjectFilter, save: bool = False):
        self.filter_panel.set_filter(f)
        self._filter_panel_changed(save)

    def _filter_panel_changed(self, save: bool = True):
        self.filter_btn.refresh()
        self._apply_filter()
        if save:
            self.filter_saved.emit(self.filter_panel.filter().to_dict())

    def _type_filter(self, object_type: str, only: bool):
        f = self.filter_panel.filter()
        if only:
            types = {o.object_type or "Object" for o in self._objects}
            f.hidden_types = (set(f.hidden_types) | types) - {object_type}
        else:
            f.hidden_types = set(f.hidden_types) | {object_type}
        self.set_filter(f, save=True)

    def visible_objects(self) -> list[SkyObject]:
        return [o for o in self._objects if self.matches(o)]

    def _apply_filter(self, *_):
        shown = 0
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            ok = self.matches(it.data(0, Qt.UserRole))
            it.setHidden(not ok)
            shown += ok
        if self._objects:
            self._update_summary(shown)
        self.filter_changed.emit()

    # ------------------------------------------------------------------ interaction
    def select_object(self, obj: SkyObject):
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, Qt.UserRole) is obj:
                if it.isHidden():
                    return
                self.tree.setCurrentItem(it)
                self.tree.scrollToItem(it)
                return

    def _obj(self, item):
        return item.data(0, Qt.UserRole) if item else None

    def _selected(self):
        items = self.tree.selectedItems()
        if items:
            self.object_selected.emit(self._obj(items[0]))

    def _activated(self, item, _col):
        obj = self._obj(item)
        if obj is None:
            return
        self.object_activated.emit(obj)
        open_first_link(obj)

    def _context_menu(self, pos):
        obj = self._obj(self.tree.itemAt(pos))
        if obj is None:
            return
        menu = object_menu(self, obj, self._decimals)
        menu.addSeparator()
        kind = obj.object_type or "Object"
        menu.addAction(f"Show only: {kind}", lambda: self._type_filter(kind, True))
        menu.addAction(f"Hide: {kind}", lambda: self._type_filter(kind, False))
        if self.filter_panel.filter().active_count():
            menu.addAction("Reset the filter", self.filter_panel.reset)
        menu.exec(self.tree.viewport().mapToGlobal(pos))


def open_first_link(obj: SkyObject) -> bool:
    if obj.links:
        QDesktopServices.openUrl(QUrl(obj.links[0].url))
        return True
    return False


def object_menu(parent, obj: SkyObject, decimals: int) -> QMenu:
    menu = QMenu(parent)
    for link in obj.links:
        menu.addAction(f"Open {link.title}", lambda u=link.url: QDesktopServices.openUrl(QUrl(u)))
    if obj.links:
        menu.addSeparator()
    menu.addAction("Copy name", lambda: QApplication.clipboard().setText(obj.display_name))
    menu.addAction("Copy coordinates",
                   lambda: QApplication.clipboard().setText(f"{format_ra(obj.ra_deg)} {format_dec(obj.dec_deg)}"))
    if obj.distance:
        menu.addAction("Copy distance", lambda: QApplication.clipboard().setText(
            format_ly(obj.distance.light_years, decimals)))
    return menu


class SolutionPanel(QScrollArea):
    min_stars = 15   # set from Settings › Star check

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        self.setWidget(inner)
        self.form = QFormLayout(inner)
        self.form.setLabelAlignment(Qt.AlignRight)
        self.form.setContentsMargins(8, 10, 8, 10)
        self.form.setVerticalSpacing(5)
        self.clear()

    def clear(self):
        while self.form.rowCount():
            self.form.removeRow(0)

    def _section(self, title: str):
        lab = QLabel(f"<b>{html.escape(title)}</b>")
        lab.setStyleSheet(f"color:{theme.ACCENT}; margin-top:8px")
        self.form.addRow(lab)

    def _row(self, label: str, value: str, color: str | None = None):
        v = QLabel(value)
        v.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.setWordWrap(True)
        if color:
            v.setStyleSheet(f"color:{color}")
        self.form.addRow(f"{label}:" if label else "", v)

    def display(self, image: ImageData | None, result: SolveResult | None, status: str = ""):
        self.clear()
        if image is None:
            self.form.addRow(_muted("No image loaded."))
            return
        self._section("Image")
        self._row("File", image.path.name)
        self._row("Size", f"{image.width} × {image.height} px, {'colour' if image.is_color else 'mono'}")
        self._row("Format", f"{image.format} {image.bit_depth}".strip())
        h = image.hints
        if h.has_position:
            self._row("Pointing", f"{format_ra(h.ra_deg)}  {format_dec(h.dec_deg)}")
        s = h.scale_arcsec()
        if s:
            src = sorted({v for k, v in h.source.items() if k in ("focal_length", "pixel_size", "scale")})
            self._row("Scale hint", f"{s:.2f}″/px, field {format_angle(s * image.width / 3600)} × "
                                   f"{format_angle(s * image.height / 3600)}"
                                   + (f"  (from {', '.join(src)})" if src else ""))
        elif h.focal_length_mm:
            self._row("Focal length", f"{h.focal_length_mm:g} mm")
        if image.header_wcs is not None:
            self._row("In file", "Already plate solved")
        if image.background_unevenness is not None:
            u = image.background_unevenness * 100
            self._row("Background", f"varies {u:.0f} %" + ("  – uneven (not flat-field corrected, or a gradient)"
                                                          if u > 20 else ""), theme.WARN if u > 20 else None)
        if image.star_count is not None:
            few = image.star_count < self.min_stars
            self._row("Stars found", f"{image.star_count}" + ("  – very few, starless image?" if few else ""),
                      theme.WARN if few else None)
        for note in image.notes:
            self._row("Note", note, theme.MUTED)

        self._section("Plate solution")
        if result is None:
            self._row("Status", status or "Not solved yet", theme.MUTED)
            return
        if not result.success:
            self._row("Status", result.message, theme.ERROR)
            for a in result.attempts:
                self._row("", "• " + a, theme.MUTED)
            return
        self._row("Status", f"Solved by {result.solver_name} in {result.elapsed_s:.1f} s", theme.OK)
        self._row("Centre RA", format_ra(result.center_ra_deg))
        self._row("Centre Dec", format_dec(result.center_dec_deg))
        self._row("Centre (deg)", f"{result.center_ra_deg:.5f}°, {result.center_dec_deg:+.5f}°")
        const = constellation_of(result.center_ra_deg, result.center_dec_deg)
        if const:
            self._row("Constellation", const)
        self._row("Field", f"{format_angle(result.fov_width_deg)} × {format_angle(result.fov_height_deg)}")
        self._row("Scale", f"{result.pixel_scale_arcsec:.3f}″/px")
        self._row("Rotation", f"{result.rotation_deg:.2f}° (up is this far east of north)")
        self._row("Mirrored", "Yes" if result.mirrored else "No")
        if result.message:
            self._row("Note", result.message, theme.WARN)
        if result.scale_note:
            self._row("Scale check", result.scale_note, theme.WARN)


class LogPanel(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)

    def add(self, text: str):
        self.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")


class Sidebar(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.objects = ObjectsPanel()
        self.solution = SolutionPanel()
        self.log = LogPanel()
        self.addTab(self.objects, "Objects")
        self.addTab(self.solution, "Solution")
        self.addTab(self.log, "Log")
        self.setMinimumWidth(320)


def constellation_of(ra_deg: float, dec_deg: float) -> str:
    """Constellation containing a sky position (astropy's built-in boundary data, works offline)."""
    try:
        from astropy.coordinates import SkyCoord, get_constellation
        return str(get_constellation(SkyCoord(ra_deg, dec_deg, unit="deg")))
    except Exception:
        return ""
