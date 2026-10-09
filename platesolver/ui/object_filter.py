# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""The small filter button next to the object search field, and the panel it opens."""
from __future__ import annotations

from collections import Counter

from PySide6.QtCore import QPoint, QRectF, QRegularExpression, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QRegularExpressionValidator, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QToolButton, QVBoxLayout, QWidget)

from platesolver.core.models import SkyObject
from platesolver.core.objectfilter import ObjectFilter
from platesolver.ui import theme

DIST_UNITS = (("ly", 1.0), ("thousand ly", 1e3), ("million ly", 1e6), ("billion ly", 1e9))


def funnel_icon(color: str, badge: int = 0) -> QIcon:
    """A funnel, with a small count in a dot when filters are on."""
    icon = QIcon()
    for size in (16, 20, 24, 32, 48):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        s = size
        path = QPainterPath()
        path.moveTo(s * 0.12, s * 0.18)
        path.lineTo(s * 0.88, s * 0.18)
        path.lineTo(s * 0.58, s * 0.52)
        path.lineTo(s * 0.58, s * 0.84)
        path.lineTo(s * 0.42, s * 0.74)
        path.lineTo(s * 0.42, s * 0.52)
        path.closeSubpath()
        pen = QPen(QColor(color), max(1.0, s / 14))
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(QColor(color) if badge else Qt.NoBrush)
        p.drawPath(path)
        if badge:
            r = s * 0.24
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.ERROR))
            p.drawEllipse(QRectF(s - 2 * r, s - 2 * r, 2 * r, 2 * r))
            if s >= 20:
                f = QFont()
                f.setPixelSize(int(r * 1.5))
                f.setBold(True)
                p.setFont(f)
                p.setPen(QColor("white"))
                p.drawText(QRectF(s - 2 * r, s - 2 * r, 2 * r, 2 * r), Qt.AlignCenter, str(min(badge, 9)))
        p.end()
        icon.addPixmap(pm)
    return icon


def _num_edit(placeholder: str = "any", width: int = 56) -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setFixedWidth(width)
    # Both 9.5 and 9,5 are accepted, whatever the system's decimal separator (QDoubleValidator would
    # follow the Windows language setting and refuse one of them).
    e.setValidator(QRegularExpressionValidator(QRegularExpression(r"^-?\d{0,13}([.,]\d{0,4})?$")))
    e.setClearButtonEnabled(False)
    return e


def _value(edit: QLineEdit) -> float | None:
    t = edit.text().strip().replace(",", ".")
    try:
        return float(t) if t else None
    except ValueError:
        return None


def _set(edit: QLineEdit, v: float | None):
    edit.setText("" if v is None else f"{v:g}")


# A lighter slate panel that stands out from the dark main window. Entry boxes and lists are darker
# "wells" so they read as places to type or tick. Contrast (WCAG): text 9.8:1, headings 6.2:1,
# links 6.1:1 on the panel; text in the boxes 14.7:1; box borders 3.2:1. All meet the guidelines.
PANEL_BG = "#353c48"
PANEL_STYLE = f"""
#filterPanel {{ background: {PANEL_BG}; border: 1px solid #8a95a5; border-radius: 8px; }}
#filterPanel QLabel, #filterPanel QCheckBox {{ color: #eef1f5; background: transparent; }}
#filterPanel QLabel#head {{ color: #b9c2ce; font-weight: bold; margin-top: 6px; }}
#filterPanel QLineEdit, #filterPanel QComboBox {{
    background: #1d2128; color: #f2f4f7; border: 1px solid #8a95a5; border-radius: 4px; padding: 2px 4px; }}
#filterPanel QLineEdit:focus, #filterPanel QComboBox:focus {{ border: 1px solid #8cc4ff; }}
#filterPanel QListWidget {{ background: #1d2128; color: #eef1f5; border: 1px solid #8a95a5; border-radius: 4px; }}
#filterPanel QListWidget::item {{ padding: 3px 6px; }}
#filterPanel QListWidget::item:hover {{ background: #2b323d; }}
#filterPanel QCheckBox::indicator, #filterPanel QListWidget::indicator {{
    width: 14px; height: 14px; border: 1px solid #8a95a5; border-radius: 3px; background: #1d2128; }}
#filterPanel QCheckBox::indicator:hover, #filterPanel QListWidget::indicator:hover {{ border-color: #8cc4ff; }}
#filterPanel QCheckBox::indicator:checked, #filterPanel QListWidget::indicator:checked {{
    background: #3d8ee6; border-color: #8cc4ff; image: url(CHECKMARK); }}
#filterPanel QPushButton#link {{ color: #8cc4ff; background: transparent; border: none; padding: 0 4px;
    font-weight: bold; }}
#filterPanel QPushButton#link:hover {{ color: #ffffff; text-decoration: underline; }}
"""


def _checkmark_file() -> str:
    """A white tick for the checked boxes (style sheets need an image file)."""
    from platesolver.core.paths import cache_dir

    path = cache_dir() / "filter_check.png"
    if not path.exists():
        pm = QPixmap(28, 28)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("white"), 4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        path_ = QPainterPath()
        path_.moveTo(6, 14.5)
        path_.lineTo(11.5, 20)
        path_.lineTo(22, 8)
        p.drawPath(path_)
        p.end()
        pm.save(str(path))
    return path.as_posix()


class FilterPanel(QFrame):
    """Pop-up with the filter choices. Changes apply immediately."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("filterPanel")
        self.setStyleSheet(PANEL_STYLE.replace("CHECKMARK", _checkmark_file()))
        self._loading = False
        self._timer = QTimer(self, singleShot=True, interval=250)
        self._timer.timeout.connect(self.changed.emit)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(4)
        top = QHBoxLayout()
        title = QLabel("<b>Filter the object list</b>")
        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setFlat(True)
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.setObjectName("link")
        self.reset_btn.clicked.connect(self.reset)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.reset_btn)
        lay.addLayout(top)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        self.mag_min, self.mag_max = _num_edit("bright"), _num_edit("faint")
        self.mag_min.setToolTip("Brightest magnitude to show (smaller number = brighter). Empty = no limit.")
        self.mag_max.setToolTip("Faintest magnitude to show. Empty = no limit.")
        self.dist_min, self.dist_max = _num_edit(), _num_edit()
        self.dist_unit = QComboBox()
        for label, _ in DIST_UNITS:
            self.dist_unit.addItem(label)
        self.dist_unit.setCurrentIndex(0)
        self.size_min, self.size_max = _num_edit(), _num_edit()
        rows = (("Magnitude", self.mag_min, self.mag_max, None),
                ("Distance", self.dist_min, self.dist_max, self.dist_unit),
                ("Size", self.size_min, self.size_max, QLabel("′ (arc-min)")))
        for r, (label, a, b, extra) in enumerate(rows):
            grid.addWidget(QLabel(label), r, 0)
            grid.addWidget(a, r, 1)
            grid.addWidget(QLabel("–"), r, 2)
            grid.addWidget(b, r, 3)
            if extra is not None:
                grid.addWidget(extra, r, 4)
        grid.setColumnStretch(5, 1)
        lay.addLayout(grid)
        self.no_mag = QCheckBox("Keep objects without a known magnitude")
        self.no_mag.setChecked(True)
        self.with_dist = QCheckBox("Only objects with a distance")
        lay.addWidget(self.no_mag)
        lay.addWidget(self.with_dist)

        head = QLabel("Show only")
        head.setObjectName("head")
        lay.addWidget(head)
        self.messier = QCheckBox("Messier objects")
        self.named = QCheckBox("Objects with a common name")
        self.known = QCheckBox("Classic catalogues (M, NGC, IC, Caldwell…)")
        self.named.setToolTip("For example Orion Nebula or Pinwheel Galaxy")
        self.known.setToolTip("Messier, NGC, IC, Caldwell, Sharpless, Barnard, Melotte, Collinder, Abell, Arp, …")
        self.visible = QCheckBox("Visible in the image")
        self.visible.setToolTip("Objects you can actually see in this image. Needs Settings › Visible stars and "
                                "label order › Find the stars that are visible in the image.")
        for cb in (self.messier, self.named, self.known, self.visible):
            lay.addWidget(cb)

        types_head = QHBoxLayout()
        head = QLabel("Types in this image")
        head.setObjectName("head")
        types_head.addWidget(head)
        types_head.addStretch(1)
        for text, state in (("All", True), ("None", False)):
            b = QPushButton(text)
            b.setFlat(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setObjectName("link")
            b.clicked.connect(lambda _=False, s=state: self._check_all(self.types, s))
            types_head.addWidget(b)
        lay.addLayout(types_head)
        self.types = QListWidget()
        self.types.setMaximumHeight(150)
        self.types.setUniformItemSizes(True)
        lay.addWidget(self.types)

        head = QLabel("Catalogues")
        head.setObjectName("head")
        lay.addWidget(head)
        self.catalogs = QListWidget()
        self.catalogs.setMaximumHeight(72)
        lay.addWidget(self.catalogs)

        for e in (self.mag_min, self.mag_max, self.dist_min, self.dist_max, self.size_min, self.size_max):
            e.textChanged.connect(self._edited)
        for cb in (self.no_mag, self.with_dist, self.messier, self.named, self.known, self.visible):
            cb.toggled.connect(self._edited)
        self.dist_unit.currentIndexChanged.connect(self._edited)
        self.types.itemChanged.connect(self._edited)
        self.catalogs.itemChanged.connect(self._edited)
        self.setFixedWidth(330)
        self._filter = ObjectFilter()

    # ------------------------------------------------------------------ content
    def set_objects(self, objects: list[SkyObject]):
        """Fill the type and catalogue lists with what this image contains (with counts)."""
        self._loading = True
        for lst, counts, hidden in ((self.types, Counter(o.object_type or "Object" for o in objects),
                                     self._filter.hidden_types),
                                    (self.catalogs, Counter(o.catalog or "Other" for o in objects),
                                     self._filter.hidden_catalogs)):
            lst.clear()
            for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                it = QListWidgetItem(f"{name}  ({n})")
                it.setData(Qt.UserRole, name)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Unchecked if name in hidden else Qt.Checked)
                lst.addItem(it)
        self._loading = False

    def set_filter(self, f: ObjectFilter):
        self._filter = f
        self._loading = True
        _set(self.mag_min, f.mag_min)
        _set(self.mag_max, f.mag_max)
        big = max([v for v in (f.dist_min_ly, f.dist_max_ly) if v] or [0.0])
        idx = max((i for i, (_, fac) in enumerate(DIST_UNITS) if big >= fac), default=0)
        self.dist_unit.setCurrentIndex(idx)
        unit = DIST_UNITS[idx][1]
        _set(self.dist_min, None if f.dist_min_ly is None else f.dist_min_ly / unit)
        _set(self.dist_max, None if f.dist_max_ly is None else f.dist_max_ly / unit)
        _set(self.size_min, f.size_min_arcmin)
        _set(self.size_max, f.size_max_arcmin)
        self.no_mag.setChecked(f.include_no_magnitude)
        self.with_dist.setChecked(f.only_with_distance)
        self.messier.setChecked(f.only_messier)
        self.named.setChecked(f.only_named)
        self.known.setChecked(f.only_well_known)
        self.visible.setChecked(f.only_visible)
        for lst, hidden in ((self.types, f.hidden_types), (self.catalogs, f.hidden_catalogs)):
            for i in range(lst.count()):
                it = lst.item(i)
                it.setCheckState(Qt.Unchecked if it.data(Qt.UserRole) in hidden else Qt.Checked)
        self._loading = False

    def current_filter(self) -> ObjectFilter:
        unit = DIST_UNITS[self.dist_unit.currentIndex()][1]
        dmin, dmax = _value(self.dist_min), _value(self.dist_max)
        f = ObjectFilter(
            mag_min=_value(self.mag_min), mag_max=_value(self.mag_max),
            include_no_magnitude=self.no_mag.isChecked(),
            dist_min_ly=None if dmin is None else dmin * unit, dist_max_ly=None if dmax is None else dmax * unit,
            only_with_distance=self.with_dist.isChecked(),
            size_min_arcmin=_value(self.size_min), size_max_arcmin=_value(self.size_max),
            only_messier=self.messier.isChecked(), only_named=self.named.isChecked(),
            only_well_known=self.known.isChecked(), only_visible=self.visible.isChecked())
        # types/catalogues not in this image keep their earlier choice
        for lst, attr in ((self.types, "hidden_types"), (self.catalogs, "hidden_catalogs")):
            hidden = set(getattr(self._filter, attr))
            for i in range(lst.count()):
                it = lst.item(i)
                name = it.data(Qt.UserRole)
                if it.checkState() == Qt.Checked:
                    hidden.discard(name)
                else:
                    hidden.add(name)
            setattr(f, attr, hidden)
        return f

    # ------------------------------------------------------------------ actions
    def _edited(self, *_):
        if self._loading:
            return
        self._filter = self.current_filter()
        self._timer.start()

    def _check_all(self, lst: QListWidget, state: bool):
        self._loading = True
        for i in range(lst.count()):
            lst.item(i).setCheckState(Qt.Checked if state else Qt.Unchecked)
        self._loading = False
        self._edited()

    def reset(self):
        self.set_filter(ObjectFilter())
        self._filter = ObjectFilter()
        self.changed.emit()

    def filter(self) -> ObjectFilter:
        return self._filter

    def popup_below(self, widget: QWidget):
        self.adjustSize()
        pos = widget.mapToGlobal(QPoint(widget.width() - self.width(), widget.height() + 2))
        screen = widget.screen().availableGeometry()
        pos.setX(max(screen.left(), min(pos.x(), screen.right() - self.width())))
        if pos.y() + self.height() > screen.bottom():
            pos.setY(widget.mapToGlobal(QPoint(0, 0)).y() - self.height() - 2)
        self.move(pos)
        self.show()


class FilterButton(QToolButton):
    """Small funnel button; shows a count when filters are on."""

    def __init__(self, panel: FilterPanel, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.setAutoRaise(True)
        self.setFixedSize(30, 28)
        self.setIconSize(QSize(20, 20))
        self.setStyleSheet("QToolButton { padding: 2px; }")
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(lambda: self.panel.popup_below(self))
        self.refresh()

    def refresh(self):
        f = self.panel.filter()
        n = f.active_count()
        self.setIcon(funnel_icon(theme.ACCENT if n else "#c9ced6", n))
        tip = "Filter the list by magnitude, distance, size, type and catalogue"
        if n:
            tip += f"\nOn: {f.describe()}"
        self.setToolTip(tip)
