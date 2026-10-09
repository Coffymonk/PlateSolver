# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Render the image with its overlays (and an optional caption) into a picture file."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsItemGroup, QGraphicsScene

from platesolver.core.export import caption_lines
from platesolver.ui.image_view import QtOverlayPainter, rgb8_to_qimage

REFERENCE_WIDTH = 1400.0   # labels look like they do on a screen showing the image this wide


def render_annotated(rgb: np.ndarray, image, solution, objects, overlays, max_width: int | None,
                     label_scale: float = 1.0, font_size: float = 11.0, ly_decimals: int = 1,
                     caption: bool = True) -> QImage:
    """overlays: the overlay plugins to draw (already filtered to the visible ones)."""
    w, h = image.width, image.height
    f = min(1.0, max_width / w) if max_width else 1.0
    out_w, out_h = max(1, int(round(w * f))), max(1, int(round(h * f)))
    rel = out_w / REFERENCE_WIDTH * label_scale
    font_scale = max(0.5, rel) * font_size / 11.0
    width_scale = max(1.0, rel)

    scene = QGraphicsScene()
    pix = scene.addPixmap(QPixmap.fromImage(rgb8_to_qimage(rgb)))
    pix.setTransformationMode(Qt.SmoothTransformation)
    pix.setOffset(-0.5, -0.5)
    pix.setZValue(-1)
    drawn = 0
    for z, ov in enumerate(overlays, start=1):
        group = QGraphicsItemGroup()
        group.setZValue(z)
        scene.addItem(group)
        painter = QtOverlayPainter(group, scene, font_scale, width_scale, screen_px=1.0 / f)
        painter.size_scale = max(0.5, rel)
        painter.ly_decimals = ly_decimals
        ov.render(painter, image, solution, objects)
        drawn += 1

    lines = caption_lines(image, solution, len(objects)) if caption else []
    cap_px = max(12, int(out_w / 95))
    cap_h = int(len(lines) * cap_px * 1.6 + cap_px) if lines else 0
    out = QImage(out_w, out_h + cap_h, QImage.Format_RGB32)
    out.fill(QColor("#0b0d10"))
    p = QPainter(out)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
    scene.render(p, QRectF(0, 0, out_w, out_h), QRectF(-0.5, -0.5, w, h), Qt.IgnoreAspectRatio)
    if lines:
        font = QFont()
        font.setPixelSize(cap_px)
        p.setFont(font)
        p.setPen(QColor("#c9d1dc"))
        y = out_h + cap_px * 1.4
        for i, line in enumerate(lines):
            if i == 1:
                p.setPen(QColor("#8b93a1"))
            p.drawText(int(cap_px * 0.8), int(y), line)
            y += cap_px * 1.6
    p.end()
    return out


def save_image(img: QImage, path: Path, quality: int = 92) -> bool:
    fmt = "JPG" if path.suffix.lower() in (".jpg", ".jpeg") else "PNG"
    return img.save(str(path), fmt, quality if fmt == "JPG" else -1)
