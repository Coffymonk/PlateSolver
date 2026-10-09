# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""The information card shown when hovering over an object (image or list)."""
from __future__ import annotations

import html

from platesolver.core.formatting import format_dec, format_ly, format_ra
from platesolver.core.models import SkyObject, light_left_text
from platesolver.ui import theme


def size_text(obj: SkyObject, decimals: int) -> str:
    size = obj.physical_size_ly()
    if size is None:
        return ""
    return format_ly(size, decimals if size < 100 else 0)


def card_html(obj: SkyObject, decimals: int = 1, show_size: bool = True, show_light: bool = True,
              hint: str = "") -> str:
    esc = html.escape
    rows: list[tuple[str, str]] = []
    if obj.distance:
        d = format_ly(obj.distance.light_years, decimals)
        if obj.distance.uncertainty_ly:
            d += f" ± {format_ly(obj.distance.uncertainty_ly, decimals).replace(' ly', '')}"
        rows.append(("Distance", esc(d)))
    else:
        rows.append(("Distance", "<i>unknown</i>"))
    if obj.size_arcmin:
        ang = f"{obj.size_arcmin:.1f}′"
        if obj.size_minor_arcmin and abs(obj.size_minor_arcmin - obj.size_arcmin) > 0.05:
            ang += f" × {obj.size_minor_arcmin:.1f}′"
        rows.append(("Apparent size", esc(ang)))
    if show_size:
        st = size_text(obj, decimals)
        if st:
            rows.append(("True size", f"about {esc(st)} across"))
    if show_light:
        lt = light_left_text(obj.light_travel_years())
        if lt:
            rows.append(("Light left it", esc(lt)))
    if obj.magnitude is not None:
        rows.append(("Magnitude", f"{obj.magnitude:.1f}"))
    rows.append(("Position", f"{format_ra(obj.ra_deg)} &nbsp;{esc(format_dec(obj.dec_deg))}"))
    seen = obj.extra.get("visible")
    if seen is True:
        rows.append(("In this image", f"<span style='color:{theme.OK}'>● visible</span>"
                     + (" (added: no catalogue had marked it)" if obj.extra.get("added_visible") else "")))
    elif seen is False:
        rows.append(("In this image", "too faint to be seen"))
    if obj.aliases:
        rows.append(("Also known as", esc(", ".join(obj.aliases[:5]))))
    table = "".join(f"<tr><td style='color:{theme.MUTED}; padding-right:10px'>{k}</td><td>{v}</td></tr>"
                    for k, v in rows)
    src = ""
    if obj.distance and obj.distance.source:
        src = f"<div style='color:{theme.MUTED}; font-size:8pt; margin-top:4px'>Distance: {esc(obj.distance.source)}</div>"
    tip = f"<div style='color:{theme.MUTED}; font-size:8pt; margin-top:4px'>{esc(hint)}</div>" if hint else ""
    return (f"<div style='min-width:260px'><div style='font-size:11pt; font-weight:600'>{esc(obj.display_name)}</div>"
            f"<div style='color:{theme.ACCENT}; margin-bottom:4px'>{esc(obj.object_type)}</div>"
            f"<table cellspacing='0' cellpadding='1'>{table}</table>{src}{tip}</div>")


class HoverCard:
    """A card that stays visible as long as the pointer is over the same object.

    It is a child of the image view's viewport (not a Qt tooltip, which hides itself after a few
    seconds), ignores the mouse so it never gets in the way, and is kept inside the view.
    """

    OFFSET = 18

    def __init__(self, viewport):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel
        self.viewport = viewport
        self.label = QLabel(viewport)
        self.label.setTextFormat(Qt.RichText)
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.label.setStyleSheet("QLabel { background: rgba(27, 31, 38, 238); color: #e6e9ef; "
                                 "border: 1px solid #3a4150; border-radius: 6px; padding: 8px 10px; }")
        self.label.hide()

    def show(self, html: str, pos) -> None:
        """pos: pointer position in viewport coordinates."""
        if self.label.text() != html:
            self.label.setText(html)
            self.label.adjustSize()
        self.move_near(pos)
        self.label.show()
        self.label.raise_()

    def move_near(self, pos) -> None:
        w, h = self.label.width(), self.label.height()
        vw, vh = self.viewport.width(), self.viewport.height()
        x = pos.x() + self.OFFSET
        y = pos.y() + self.OFFSET
        if x + w > vw - 4:          # not enough room on the right: put it on the left of the pointer
            x = pos.x() - self.OFFSET - w
        if y + h > vh - 4:          # or above the pointer
            y = pos.y() - self.OFFSET - h
        self.label.move(max(4, x), max(4, y))

    def hide(self) -> None:
        self.label.hide()

    def is_visible(self) -> bool:
        return self.label.isVisible()


def install_lasting_item_tooltips(view, duration_ms: int = 3_600_000) -> None:
    """Item-view tooltips that stay until the pointer leaves the row (instead of ~10 s)."""
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QToolTip

    class _Filter(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.ToolTip:
                index = view.indexAt(event.pos())
                tip = index.data(3) if index.isValid() else None   # 3 = Qt.ToolTipRole
                if tip:
                    rect = view.visualRect(index)
                    rect.setLeft(0)
                    rect.setRight(view.viewport().width())
                    QToolTip.showText(event.globalPos(), tip, view.viewport(), rect, duration_ms)
                else:
                    QToolTip.hideText()
                return True
            return False

    f = _Filter(view)
    view.viewport().installEventFilter(f)
    view._lasting_tooltip_filter = f   # keep a reference
