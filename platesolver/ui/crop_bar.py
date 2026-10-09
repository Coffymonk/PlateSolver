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

        row.addSpacing(12)
        self.btn_reset = QPushButton("Reset")
        self.btn_reset.setToolTip("Undo the rotation and the selection")
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

    def reset_angle(self):
        self._timer.stop()
        for w in (self.slider, self.spin):
            w.blockSignals(True)
        self.slider.setValue(0)
        self.spin.setValue(0.0)
        for w in (self.slider, self.spin):
            w.blockSignals(False)

    def set_selection(self, size: tuple[int, int] | None, image_size: tuple[int, int] | None = None):
        if size is None:
            self.hint.setText("<b>Crop and rotate.</b> Drag a rectangle around the part of the image you want "
                              "to solve – leave out menus, text and empty borders. Rotate first if it helps "
                              "you line up the selection. The file itself is not changed.")
        else:
            whole = f" of {image_size[0]} × {image_size[1]}" if image_size else ""
            self.hint.setText(f"<b>Selection: {size[0]} × {size[1]} pixels{whole}.</b> Drag inside it to move "
                              "it, drag an edge or corner to resize it, or drag outside it for a new one.")
