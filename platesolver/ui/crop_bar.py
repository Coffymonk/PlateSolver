# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""The bar shown above the image while cropping and rotating (Image › Crop and rotate)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider,
                               QToolButton, QVBoxLayout)


def crop_icon(color: str = "#d6dbe3", size: int = 32) -> QIcon:
    """Two overlapping corner brackets, the usual crop symbol."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), size * 0.09)
    pen.setCapStyle(Qt.FlatCap)
    pen.setJoinStyle(Qt.MiterJoin)
    p.setPen(pen)
    s = size
    a = QPainterPath()
    a.moveTo(s * 0.28, s * 0.08)
    a.lineTo(s * 0.28, s * 0.72)
    a.lineTo(s * 0.92, s * 0.72)
    b = QPainterPath()
    b.moveTo(s * 0.08, s * 0.28)
    b.lineTo(s * 0.72, s * 0.28)
    b.lineTo(s * 0.72, s * 0.92)
    p.drawPath(a)
    p.drawPath(b)
    p.end()
    return QIcon(pm)


def rotate_icon(clockwise: bool, color: str = "#d6dbe3", size: int = 32) -> QIcon:
    """A three-quarter circle with an arrow head."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), size * 0.09)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    r = QRectF(size * 0.2, size * 0.22, size * 0.6, size * 0.6)
    if clockwise:
        p.drawArc(r, 100 * 16, -280 * 16)
        tip_x, tip_y = size * 0.5 - size * 0.05, size * 0.22
        p.setBrush(QColor(color))
        p.setPen(Qt.NoPen)
        path = QPainterPath()
        path.moveTo(tip_x + size * 0.2, tip_y)
        path.lineTo(tip_x, tip_y - size * 0.13)
        path.lineTo(tip_x, tip_y + size * 0.13)
        path.closeSubpath()
    else:
        p.drawArc(r, 80 * 16, 280 * 16)
        tip_x, tip_y = size * 0.5 + size * 0.05, size * 0.22
        p.setBrush(QColor(color))
        p.setPen(Qt.NoPen)
        path = QPainterPath()
        path.moveTo(tip_x - size * 0.2, tip_y)
        path.lineTo(tip_x, tip_y - size * 0.13)
        path.lineTo(tip_x, tip_y + size * 0.13)
        path.closeSubpath()
    p.drawPath(path)
    p.end()
    return QIcon(pm)


class CropBar(QFrame):
    """Rotate 90°, straighten, reset, cancel and apply. The selection itself is drawn on the image."""

    rotate_requested = Signal(int)        # -1 = 90° anticlockwise, +1 = 90° clockwise
    angle_changed = Signal(float)         # straightening angle in degrees (clockwise), after a short pause
    reset_requested = Signal()
    cancel_requested = Signal()
    apply_requested = Signal(bool)        # True = solve right after
    find_area_requested = Signal()        # straighten and select the star area automatically
    find_marks_requested = Signal()       # mark the app's text and buttons as areas to hide
    tool_changed = Signal(str)            # "select" (the area to keep) or "hide" (areas to hide)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CropBar")
        self.setStyleSheet("#CropBar { background: #1d2733; border-bottom: 1px solid #3a4a5c; }"
                           "#CropBar QLabel#cropHint { color: #b7c3d0; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(4)
        self.hint = QLabel()
        self.hint.setObjectName("cropHint")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_left = QToolButton()
        self.btn_left.setIcon(rotate_icon(False))
        self.btn_left.setText("90°")
        self.btn_left.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_left.setToolTip("Rotate 90° anticlockwise ( [ )")
        self.btn_right = QToolButton()
        self.btn_right.setIcon(rotate_icon(True))
        self.btn_right.setText("90°")
        self.btn_right.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_right.setToolTip("Rotate 90° clockwise ( ] )")
        self.btn_left.clicked.connect(lambda: self.rotate_requested.emit(-1))
        self.btn_right.clicked.connect(lambda: self.rotate_requested.emit(1))
        row.addWidget(self.btn_left)
        row.addWidget(self.btn_right)

        row.addSpacing(12)
        row.addWidget(QLabel("Straighten:"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(-450, 450)          # tenths of a degree
        self.slider.setSingleStep(1)
        self.slider.setPageStep(10)
        self.slider.setMinimumWidth(160)
        self.slider.setToolTip("Turn the image a little, e.g. to line up a photographed screen with the edges")
        self.spin = QDoubleSpinBox()
        self.spin.setRange(-45.0, 45.0)
        self.spin.setDecimals(1)
        self.spin.setSingleStep(0.5)
        self.spin.setSuffix(" °")
        self.spin.setToolTip("Straightening angle; positive turns the image clockwise")
        self.slider.valueChanged.connect(lambda v: self._set_angle(v / 10.0, from_slider=True))
        self.spin.valueChanged.connect(lambda v: self._set_angle(v, from_slider=False))
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)

        outer.addLayout(row)

        # second row: automatic help for photos of a screen, what the mouse draws, and the actions
        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_find_area = QPushButton("Find the star area")
        self.btn_find_area.setToolTip("Straighten a photographed screen and select the part with stars, leaving "
                                      "out the app's bars and the screen's edges. Adjust it afterwards if needed.")
        self.btn_find_area.clicked.connect(self.find_area_requested)
        self.btn_find_marks = QPushButton("Find app text and buttons")
        self.btn_find_marks.setToolTip("Mark text, icons and panels drawn by the app on top of the stars, so they "
                                       "are hidden (filled with the sky colour) before solving. Click a marked "
                                       "area to remove it.")
        self.btn_find_marks.clicked.connect(self.find_marks_requested)
        row.addWidget(self.btn_find_area)
        row.addWidget(self.btn_find_marks)
        row.addSpacing(12)
        row.addWidget(QLabel("The mouse draws:"))
        self.btn_keep = QToolButton()
        self.btn_keep.setText("Area to keep")
        self.btn_keep.setCheckable(True)
        self.btn_keep.setChecked(True)
        self.btn_keep.setToolTip("Drag a rectangle around the part of the image to solve")
        self.btn_hide = QToolButton()
        self.btn_hide.setText("Areas to hide")
        self.btn_hide.setCheckable(True)
        self.btn_hide.setToolTip("Drag rectangles over app text, buttons or anything that isn't sky; they are "
                                 "filled with the sky colour. Click one to remove it.")
        for b in (self.btn_keep, self.btn_hide):
            b.setStyleSheet("QToolButton { padding: 3px 8px; } QToolButton:checked { background: #2f6db5; "
                            "color: white; border-radius: 3px; }")
        self.btn_keep.clicked.connect(lambda: self.set_tool("select", emit=True))
        self.btn_hide.clicked.connect(lambda: self.set_tool("hide", emit=True))
        row.addWidget(self.btn_keep)
        row.addWidget(self.btn_hide)
        row.addStretch(1)
        self.btn_reset = QPushButton("Reset")
        self.btn_reset.setToolTip("Undo the rotation, the selection and the hidden areas")
        self.btn_reset.clicked.connect(self.reset_requested)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setToolTip("Leave without changing the image (Esc)")
        self.btn_cancel.clicked.connect(self.cancel_requested)
        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setToolTip("Show the cropped and rotated image, without solving")
        self.btn_apply.clicked.connect(lambda: self.apply_requested.emit(False))
        self.btn_solve = QPushButton("Apply and solve")
        self.btn_solve.setToolTip("Show the cropped and rotated image and plate solve it (Enter)")
        self.btn_solve.setStyleSheet("QPushButton { background: #2f6db5; color: white; padding: 4px 12px; "
                                     "border-radius: 3px; } QPushButton:hover { background: #3b80cf; }")
        self.btn_solve.clicked.connect(lambda: self.apply_requested.emit(True))
        for b in (self.btn_reset, self.btn_cancel, self.btn_apply, self.btn_solve):
            row.addWidget(b)
        outer.addLayout(row)

        # the preview is rotated after the slider rests for a moment (a full image takes a fraction of a second)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(lambda: self.angle_changed.emit(self.angle()))
        self.tool = "select"
        self.set_selection(None)

    def angle(self) -> float:
        return round(self.spin.value(), 1)

    def _set_angle(self, value: float, from_slider: bool):
        value = round(value, 1)
        if from_slider:
            self.spin.blockSignals(True)
            self.spin.setValue(value)
            self.spin.blockSignals(False)
        else:
            self.slider.blockSignals(True)
            self.slider.setValue(int(round(value * 10)))
            self.slider.blockSignals(False)
        self._timer.start()

    def set_angle_value(self, value: float):
        """Show an angle chosen elsewhere (e.g. Find the star area) without starting a new preview."""
        self._timer.stop()
        for w in (self.slider, self.spin):
            w.blockSignals(True)
        self.spin.setValue(round(value, 1))
        self.slider.setValue(int(round(value * 10)))
        for w in (self.slider, self.spin):
            w.blockSignals(False)

    def reset_angle(self):
        self._timer.stop()
        for w in (self.slider, self.spin):
            w.blockSignals(True)
        self.slider.setValue(0)
        self.spin.setValue(0.0)
        for w in (self.slider, self.spin):
            w.blockSignals(False)

    def set_tool(self, tool: str, emit: bool = False):
        hide = tool == "hide"
        self.btn_keep.setChecked(not hide)
        self.btn_hide.setChecked(hide)
        self.tool = "hide" if hide else "select"
        if emit:
            self.tool_changed.emit(self.tool)

    def set_selection(self, size: tuple[int, int] | None, image_size: tuple[int, int] | None = None,
                      hidden: int = 0):
        tail = ""
        if hidden:
            tail = f" <b>{hidden} area{'s' if hidden != 1 else ''} hidden</b> (red); click one with " \
                   "<i>Areas to hide</i> to remove it."
        if getattr(self, "tool", "select") == "hide":
            self.hint.setText("<b>Areas to hide.</b> Drag rectangles over app text, buttons, panels or anything "
                              "that isn't sky – they are filled with the sky colour before solving. Click a red "
                              "area to remove it." + (f" {hidden} hidden so far." if hidden else ""))
        elif size is None:
            self.hint.setText("<b>Crop and rotate.</b> Drag a rectangle around the part of the image you want "
                              "to solve – leave out menus, text and empty borders. Rotate first if it helps "
                              "you line up the selection. For a photo of a screen, try <i>Find the star "
                              "area</i>. The file itself is not changed." + tail)
        else:
            whole = f" of {image_size[0]} × {image_size[1]}" if image_size else ""
            self.hint.setText(f"<b>Selection: {size[0]} × {size[1]} pixels{whole}.</b> Drag inside it to move "
                              "it, drag an edge or corner to resize it, or drag outside it for a new one." + tail)


class SuggestionBar(QFrame):
    """A slim bar above the image with a suggestion and a few buttons; it never changes anything by itself."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SuggestBar")
        self.setStyleSheet("#SuggestBar { background: #2a2a1c; border-bottom: 1px solid #5c5530; }"
                           "#SuggestBar QLabel { color: #e8dfb0; }")
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(10, 5, 10, 5)
        self._row.setSpacing(6)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self._row.addWidget(self.text, 1)
        self._buttons: list = []
        self.hide()

    def show_suggestion(self, text: str, buttons):
        """buttons: [(label, callback), ...]; any button also closes the bar."""
        for b in self._buttons:
            self._row.removeWidget(b)
            b.deleteLater()
        self._buttons = []
        self.text.setText(text)
        for i, (label, cb) in enumerate(buttons):
            b = QPushButton(label)
            if i == 0:
                b.setStyleSheet("QPushButton { background: #2f6db5; color: white; padding: 3px 10px; "
                                "border-radius: 3px; } QPushButton:hover { background: #3b80cf; }")
            b.clicked.connect(lambda _=False, f=cb: (self.hide(), f()))
            self._row.addWidget(b)
            self._buttons.append(b)
        self.show()
