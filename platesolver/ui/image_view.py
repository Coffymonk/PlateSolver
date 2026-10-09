# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Zoomable, pannable image view with overlay layers on top."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem, QGraphicsItemGroup,
                               QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView)


def rgb8_to_qimage(rgb: np.ndarray) -> QImage:
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class _Text(QGraphicsSimpleTextItem):
    """Text that keeps its screen size and has a dark outline for contrast."""

    def __init__(self, text, color, size, anchor, outline=2.5):
        super().__init__(text)
        f = QFont()
        f.setPixelSize(int(size))
        f.setBold(True)
        self.setFont(f)
        self.setBrush(QColor(color))
        self.setPen(QPen(QColor(0, 0, 0, 170), outline))
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        r = self.boundingRect()
        dx = {"left": 0, "center": -r.width() / 2, "right": -r.width()}.get(anchor, 0)
        self.setTransform(self.transform().translate(dx, -r.height() / 2))

    def paint(self, painter, option, widget=None):
        # outline first, then the fill on top
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addText(QPointF(0, self.font().pixelSize() * 0.92), self.font(), self.text())
        painter.strokePath(path, self.pen())
        painter.fillPath(path, self.brush())


class QtOverlayPainter:
    """Implements core.interfaces.OverlayPainter by adding items to one layer group."""

    def __init__(self, group: QGraphicsItemGroup, scene: QGraphicsScene, font_scale: float = 1.0,
                 width_scale: float = 1.0, screen_px: float = 1.0):
        self.group = group
        self.scene = scene
        self.font_scale = font_scale      # text size multiplier
        self.width_scale = width_scale    # line width multiplier (used for exports)
        self.screen_px = screen_px        # image pixels per screen (or output) pixel
        self.size_scale = 1.0             # export resolution factor (1 on screen); font_scale includes it
        self.ly_decimals = 1

    def text_extent(self, text: str, size: float = 10) -> tuple[float, float]:
        """Width and height a label will take, in image pixels (for avoiding overlaps)."""
        f = QFont()
        f.setPixelSize(max(1, int(size * self.font_scale)))
        f.setBold(True)
        fm = QFontMetricsF(f)
        return fm.horizontalAdvance(text) * self.screen_px, fm.height() * self.screen_px

    def _pen(self, color, width):
        pen = QPen(QColor(color), width * self.width_scale)
        pen.setCosmetic(True)  # width in screen pixels at every zoom
        pen.setCapStyle(Qt.RoundCap)
        return pen

    def _add(self, item):
        self.group.addToGroup(item)
        return item

    def line(self, x1, y1, x2, y2, color, width=1.5):
        path = QPainterPath(QPointF(x1, y1))
        path.lineTo(x2, y2)
        self._add(QGraphicsPathItem(path)).setPen(self._pen(color, width))

    def polyline(self, points, color, width=1.5, closed=False):
        pts = list(points)
        if len(pts) < 2:
            return
        path = QPainterPath(QPointF(*pts[0]))
        for p in pts[1:]:
            path.lineTo(*p)
        if closed:
            path.closeSubpath()
        self._add(QGraphicsPathItem(path)).setPen(self._pen(color, width))

    def arrow(self, x1, y1, x2, y2, color, width=1.5):
        self.line(x1, y1, x2, y2, color, width)
        ang = math.atan2(y2 - y1, x2 - x1)
        head = math.hypot(x2 - x1, y2 - y1) * 0.18
        pts = [(x2 - head * math.cos(ang - s), y2 - head * math.sin(ang - s)) for s in (0.4, -0.4)]
        self.polyline([pts[0], (x2, y2), pts[1]], color, width)

    def circle(self, x, y, radius, color, width=1.5):
        self.ellipse(x, y, radius, radius, 0.0, color, width)

    def ellipse(self, x, y, rx, ry, angle_deg, color, width=1.5):
        item = QGraphicsEllipseItem(-rx, -ry, 2 * rx, 2 * ry)
        item.setPen(self._pen(color, width))
        item.setPos(x, y)
        item.setRotation(angle_deg)
        self._add(item)

    def text(self, x, y, text, color, size=10, anchor="left"):
        item = _Text(text, color, size * self.font_scale, anchor, 2.5 * self.width_scale)
        item.setPos(x, y)
        self._add(item)


class ImageView(QGraphicsView):
    """Mouse wheel zooms around the cursor, dragging pans, double-click fits.

    In crop mode (`set_crop_mode`), dragging draws a selection rectangle instead: drag inside it to move
    it, drag its edges or corners to resize it, drag outside it for a new one. The area outside is shaded.
    Panning then works with the right or middle mouse button.
    """

    cursor_moved = Signal(float, float)   # image pixel x, y
    clicked = Signal(float, float)        # a click (not a drag) at image pixel x, y
    double_clicked = Signal(float, float)
    cursor_left = Signal()
    zoom_changed = Signal(float)
    file_dropped = Signal(str)
    crop_changed = Signal()               # the crop selection changed (crop mode)

    MIN_ZOOM, MAX_ZOOM = 0.02, 32.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(QColor("#0b0d10")))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self._pixmap: QGraphicsPixmapItem | None = None
        self._layers: dict[str, QGraphicsItemGroup] = {}
        self._fit_mode = True
        self._crop_mode = False
        self._crop: QRectF | None = None      # selection in scene (= image pixel) coordinates
        self._crop_items: list = []
        self._drag = None                     # (kind, start scene point, start rect) while dragging the selection
        self._pan = None                      # last mouse position while panning in crop mode

    # ------------------------------------------------------------------ image
    def set_image(self, qimage: QImage | None, keep_view: bool = False):
        if qimage is None:
            self.scene().clear()
            self._pixmap = None
            self._layers.clear()
            self._crop_items = []
            self.viewport().update()
            return
        if self._pixmap is None:
            self._pixmap = self.scene().addPixmap(QPixmap.fromImage(qimage))
            self._pixmap.setTransformationMode(Qt.SmoothTransformation)
            self._pixmap.setZValue(-1)
            # pixel centres at integer scene coordinates, so scene = image pixel coordinates
            self._pixmap.setOffset(-0.5, -0.5)
        else:
            self._pixmap.setPixmap(QPixmap.fromImage(qimage))
        self.scene().setSceneRect(QRectF(-0.5, -0.5, qimage.width(), qimage.height()))
        if not keep_view:
            self.fit()

    def has_image(self) -> bool:
        return self._pixmap is not None

    # ------------------------------------------------------------------ overlays
    def clear_layers(self):
        for g in self._layers.values():
            self.scene().removeItem(g)
        self._layers.clear()

    def new_layer(self, layer_id: str, z: float = 1.0) -> QGraphicsItemGroup:
        old = self._layers.pop(layer_id, None)
        if old is not None:
            self.scene().removeItem(old)
        g = QGraphicsItemGroup()
        g.setZValue(z)
        self.scene().addItem(g)
        self._layers[layer_id] = g
        return g

    def set_layer_visible(self, layer_id: str, visible: bool):
        if layer_id in self._layers:
            self._layers[layer_id].setVisible(visible)

    # ------------------------------------------------------------------ zoom
    def zoom(self) -> float:
        return self.transform().m11()

    def fit(self):
        if self._pixmap is None:
            return
        self._fit_mode = True
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
        self.zoom_changed.emit(self.zoom())
        if self._crop_mode:
            self._update_crop_items()        # handles keep their size on screen

    def set_zoom(self, z: float):
        z = max(self.MIN_ZOOM, min(self.MAX_ZOOM, z))
        self._fit_mode = False
        self.setTransform(self.transform().fromScale(z, z))
        self.zoom_changed.emit(z)
        if self._crop_mode:
            self._update_crop_items()

    def zoom_by(self, factor: float):
        self.set_zoom(self.zoom() * factor)

    def center_on_pixel(self, x: float, y: float):
        self.centerOn(QPointF(x, y))

    def wheelEvent(self, event):
        if self._pixmap is None:
            return
        factor = 1.25 ** (event.angleDelta().y() / 120.0)
        self.zoom_by(factor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit()

    def mousePressEvent(self, event):
        self._press_pos = event.position()
        if self._crop_mode and self._pixmap is not None:
            if event.button() == Qt.LeftButton:
                p = self.mapToScene(event.position().toPoint())
                kind = self._crop_hit(event.position())
                if kind is None:
                    kind = "new"
                    self._crop = QRectF(self._clamp_point(p), self._clamp_point(p))
                self._drag = (kind, p, QRectF(self._crop) if self._crop else None)
                self._update_crop_items()
            elif event.button() in (Qt.RightButton, Qt.MiddleButton):
                self._pan = event.position()
                self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._crop_mode:
            if self._drag is not None and event.button() == Qt.LeftButton:
                self._drag = None
                if self._crop is not None:
                    r = self._crop.normalized()
                    self._crop = r if r.width() >= 4 and r.height() >= 4 else None
                self._update_crop_items()
                self.crop_changed.emit()
            self._pan = None
            self._crop_cursor(event.position())
            return
        super().mouseReleaseEvent(event)
        start = getattr(self, "_press_pos", None)
        if (start is not None and event.button() == Qt.LeftButton and self._pixmap is not None
                and (event.position() - start).manhattanLength() < 4):
            p = self.mapToScene(event.position().toPoint())
            self.clicked.emit(p.x(), p.y())

    def mouseDoubleClickEvent(self, event):
        if self._crop_mode:
            return
        if event.button() == Qt.LeftButton and self._pixmap is not None:
            p = self.mapToScene(event.position().toPoint())
            self.double_clicked.emit(p.x(), p.y())
        else:
            super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if self._crop_mode and self._pixmap is not None:
            if self._pan is not None:
                d = event.position() - self._pan
                self._pan = event.position()
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            elif self._drag is not None:
                self._drag_crop(self.mapToScene(event.position().toPoint()))
            else:
                self._crop_cursor(event.position())
        else:
            super().mouseMoveEvent(event)
        if self._pixmap is not None:
            p = self.mapToScene(event.position().toPoint())
            r = self.sceneRect()
            if r.contains(p):
                self.cursor_moved.emit(p.x(), p.y())
            else:
                self.cursor_left.emit()

    def leaveEvent(self, event):
        self.cursor_left.emit()
        super().leaveEvent(event)

    # ------------------------------------------------------------------ crop mode
    HANDLE_PX = 9          # how close (screen pixels) to an edge counts as grabbing it

    def crop_mode(self) -> bool:
        return self._crop_mode

    def set_crop_mode(self, on: bool):
        self._crop_mode = bool(on)
        self._drag = self._pan = None
        self.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.ScrollHandDrag)
        if on:
            self.viewport().setCursor(Qt.CrossCursor)
        else:
            self.viewport().unsetCursor()
            self._crop = None
        self._update_crop_items()

    def crop_rect(self) -> tuple[int, int, int, int] | None:
        """The selection as whole image pixels (x, y, width, height), or None for no selection."""
        if self._crop is None:
            return None
        r = self._crop.normalized()
        x0, y0 = math.floor(r.left() + 0.5), math.floor(r.top() + 0.5)
        x1, y1 = math.floor(r.right() + 0.5), math.floor(r.bottom() + 0.5)
        return x0, y0, max(0, x1 - x0), max(0, y1 - y0)

    def set_crop_rect(self, rect: tuple[float, float, float, float] | None):
        self._crop = None if rect is None else QRectF(rect[0] - 0.5, rect[1] - 0.5, rect[2], rect[3])
        self._update_crop_items()
        self.crop_changed.emit()

    def _clamp_point(self, p: QPointF) -> QPointF:
        r = self.sceneRect()
        return QPointF(min(max(p.x(), r.left()), r.right()), min(max(p.y(), r.top()), r.bottom()))

    def _crop_hit(self, pos) -> str | None:
        """Which part of the selection the mouse is over: 'l', 'tr', … for edges/corners, 'move', or None."""
        if self._crop is None:
            return None
        r = self._crop.normalized()
        tl = self.mapFromScene(r.topLeft())
        br = self.mapFromScene(r.bottomRight())
        x, y, t = pos.x(), pos.y(), self.HANDLE_PX
        inside_x = tl.x() - t <= x <= br.x() + t
        inside_y = tl.y() - t <= y <= br.y() + t
        if not (inside_x and inside_y):
            return None
        kind = ""
        if abs(y - tl.y()) <= t:
            kind += "t"
        elif abs(y - br.y()) <= t:
            kind += "b"
        if abs(x - tl.x()) <= t:
            kind += "l"
        elif abs(x - br.x()) <= t:
            kind += "r"
        if kind:
            return kind
        return "move" if tl.x() < x < br.x() and tl.y() < y < br.y() else None

    def _crop_cursor(self, pos):
        kind = self._crop_hit(pos) if self._crop_mode else None
        shapes = {"move": Qt.SizeAllCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
                  "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "tl": Qt.SizeFDiagCursor,
                  "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor}
        if self._crop_mode:
            self.viewport().setCursor(shapes.get(kind, Qt.CrossCursor))

    def _drag_crop(self, p: QPointF):
        kind, start, rect0 = self._drag
        p = self._clamp_point(p)
        if kind == "new":
            self._crop = QRectF(self._clamp_point(start), p).normalized()
        elif kind == "move":
            area = self.sceneRect()
            dx, dy = p.x() - start.x(), p.y() - start.y()
            dx = min(max(dx, area.left() - rect0.left()), area.right() - rect0.right())
            dy = min(max(dy, area.top() - rect0.top()), area.bottom() - rect0.bottom())
            self._crop = rect0.translated(dx, dy)
        else:
            r = QRectF(rect0)
            if "l" in kind:
                r.setLeft(p.x())
            if "r" in kind:
                r.setRight(p.x())
            if "t" in kind:
                r.setTop(p.y())
            if "b" in kind:
                r.setBottom(p.y())
            self._crop = r
        self._update_crop_items()
        self.crop_changed.emit()

    def _update_crop_items(self):
        for item in self._crop_items:
            self.scene().removeItem(item)
        self._crop_items = []
        if not self._crop_mode or self._pixmap is None:
            return
        area = self.sceneRect()
        shade = QPainterPath()
        shade.addRect(area)
        if self._crop is not None:
            inner = QPainterPath()
            inner.addRect(self._crop.normalized())
            shade = shade.subtracted(inner)
        dim = QGraphicsPathItem(shade)
        dim.setBrush(QBrush(QColor(0, 0, 0, 150 if self._crop is not None else 60)))
        dim.setPen(QPen(Qt.NoPen))
        dim.setZValue(200)
        self.scene().addItem(dim)
        self._crop_items.append(dim)
        if self._crop is None:
            return
        r = self._crop.normalized()
        for color, width, style in (("#000000", 3.0, Qt.SolidLine), ("#ffd23f", 1.5, Qt.DashLine)):
            pen = QPen(QColor(color), width, style)
            pen.setCosmetic(True)
            outline = self.scene().addRect(r, pen)
            outline.setZValue(201)
            self._crop_items.append(outline)
        # handles at the corners and edge middles, a fixed size on screen
        s = 7.0 / max(self.zoom(), 1e-6)
        pen = QPen(QColor("#000000"), 1.0)
        pen.setCosmetic(True)
        for hx, hy in ((r.left(), r.top()), (r.right(), r.top()), (r.left(), r.bottom()),
                       (r.right(), r.bottom()), (r.center().x(), r.top()), (r.center().x(), r.bottom()),
                       (r.left(), r.center().y()), (r.right(), r.center().y())):
            h = self.scene().addRect(QRectF(hx - s / 2, hy - s / 2, s, s), pen, QBrush(QColor("#ffd23f")))
            h.setZValue(202)
            self._crop_items.append(h)
        x, y, w, hgt = self.crop_rect()
        label = _Text(f"{w} × {hgt} px", "#ffd23f", 12, "left")
        label.setTransform(label.transform().translate(1, -label.boundingRect().height() / 2 - 3))
        label.setPos(r.left(), r.top())        # just above the top-left corner
        label.setZValue(203)
        self.scene().addItem(label)
        self._crop_items.append(label)

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.file_dropped.emit(urls[0])
            event.acceptProposedAction()

    def drawForeground(self, painter, rect):
        if self._pixmap is None:
            painter.resetTransform()
            painter.setPen(QColor("#5a616d"))
            f = painter.font()
            f.setPointSize(13)
            painter.setFont(f)
            painter.drawText(self.viewport().rect(), Qt.AlignCenter,
                             "Open an image (Ctrl+O) or drop a JPG, FITS or TIFF file here")
