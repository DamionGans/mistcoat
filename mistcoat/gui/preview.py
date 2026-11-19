"""Before/after comparison canvas.

The artist's real question is "can I see it?", and the only honest way to
answer that is a 1:1 pixel view they can drag around and flip between the two
versions. Everything here serves that: a split wipe, a hold-to-compare key, a
100% button, and an amplified difference view for when they want to see what
was actually done.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QMouseEvent, QPainter, QPen,
                           QPixmap, QWheelEvent)
from PySide6.QtWidgets import QWidget

from . import theme

MODE_SPLIT = "split"
MODE_AFTER = "after"
MODE_BEFORE = "before"
MODE_DIFF = "diff"

DIFF_GAIN = 14.0


def to_qimage(rgb: np.ndarray) -> QImage:
    arr = np.ascontiguousarray((np.clip(rgb, 0, 1) * 255.0 + 0.5).astype(np.uint8))
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class PreviewCanvas(QWidget):
    """Shows before and after with a draggable wipe. Pan with the mouse, zoom
    with the wheel, hold Space to see the original."""

    zoom_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 340)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(False)

        self._before: QPixmap | None = None
        self._after: QPixmap | None = None
        self._diff: QPixmap | None = None
        self._placeholder = "Drop an image here, or use Add images"

        self.mode = MODE_SPLIT
        self.split = 0.5
        self.zoom = 1.0
        self._fit_zoom = 1.0
        self._fitting = True
        self.offset = QPointF(0, 0)

        self._panning = False
        self._dragging_split = False
        self._last_mouse = QPoint()
        self._peek = False

    # ------------------------------------------------------------- content

    def set_images(self, before: np.ndarray | None, after: np.ndarray | None) -> None:
        if before is None:
            self._before = self._after = self._diff = None
        else:
            self._before = QPixmap.fromImage(to_qimage(before))
            if after is not None:
                self._after = QPixmap.fromImage(to_qimage(after))
                d = np.clip((after - before) * DIFF_GAIN + 0.5, 0, 1)
                self._diff = QPixmap.fromImage(to_qimage(d))
            else:
                self._after = self._diff = None
        if self._fitting:
            self.fit()
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self.update()

    def has_image(self) -> bool:
        return self._before is not None

    # ---------------------------------------------------------------- view

    def _source(self) -> QPixmap | None:
        return self._before

    def fit(self) -> None:
        src = self._source()
        if src is None:
            return
        avail_w = max(self.width() - 24, 1)
        # Leave room under the image for the mode caption.
        avail_h = max(self.height() - 46, 1)
        self._fit_zoom = min(avail_w / src.width(), avail_h / src.height(), 4.0)
        self.zoom = self._fit_zoom
        self._fitting = True
        self.offset = QPointF(0, 0)
        self.zoom_changed.emit(self.zoom)
        self.update()

    def set_zoom(self, zoom: float, anchor: QPointF | None = None) -> None:
        src = self._source()
        if src is None:
            return
        zoom = float(np.clip(zoom, 0.04, 16.0))
        if anchor is not None:
            # Keep the point under the cursor fixed while zooming.
            centre = QPointF(self.width() / 2, self.height() / 2)
            rel = anchor - centre - self.offset
            self.offset -= rel * (zoom / self.zoom - 1.0)
        self.zoom = zoom
        self._fitting = abs(zoom - self._fit_zoom) < 1e-3
        self.zoom_changed.emit(self.zoom)
        self.update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.update()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._fitting:
            self.fit()

    # -------------------------------------------------------------- paint

    def _target_rect(self) -> QRectF:
        src = self._source()
        w = src.width() * self.zoom
        h = src.height() * self.zoom
        x = (self.width() - w) / 2 + self.offset.x()
        y = (self.height() - h) / 2 + self.offset.y()
        return QRectF(x, y, w, h)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.CANVAS))

        if self._before is None:
            p.setPen(QColor(theme.TEXT_DIM))
            f = QFont()
            f.setPointSize(11)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, self._placeholder)
            return

        rect = self._target_rect()
        # Crisp pixels when zoomed past 1:1 - that is the whole point of the
        # 100% view; smooth when shrinking so the preview is not aliased.
        p.setRenderHint(QPainter.SmoothPixmapTransform, self.zoom < 1.0)

        after = self._after or self._before
        if self._peek:
            p.drawPixmap(rect, self._before, QRectF(self._before.rect()))
        elif self.mode == MODE_BEFORE or after is None:
            p.drawPixmap(rect, self._before, QRectF(self._before.rect()))
        elif self.mode == MODE_AFTER:
            p.drawPixmap(rect, after, QRectF(after.rect()))
        elif self.mode == MODE_DIFF and self._diff is not None:
            p.drawPixmap(rect, self._diff, QRectF(self._diff.rect()))
        else:
            self._paint_split(p, rect, after)

        # The split handle leaves a filled brush behind; an outline must not
        # inherit it or it paints over the whole image.
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.drawRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5))
        self._paint_badge(p, rect)

    def _paint_split(self, p: QPainter, rect: QRectF, after: QPixmap) -> None:
        split_x = rect.left() + rect.width() * self.split
        src_rect = QRectF(self._before.rect())

        left = QRectF(rect.left(), rect.top(), split_x - rect.left(), rect.height())
        right = QRectF(split_x, rect.top(), rect.right() - split_x, rect.height())

        if left.width() > 0:
            frac = left.width() / rect.width()
            p.drawPixmap(left, self._before,
                         QRectF(0, 0, src_rect.width() * frac, src_rect.height()))
        if right.width() > 0:
            frac = right.width() / rect.width()
            p.drawPixmap(right, after,
                         QRectF(src_rect.width() * (1 - frac), 0,
                                src_rect.width() * frac, src_rect.height()))

        p.setPen(QPen(QColor(theme.ACCENT), 1.5))
        p.drawLine(QPointF(split_x, rect.top()), QPointF(split_x, rect.bottom()))
        handle_y = rect.center().y()
        p.setBrush(QColor(theme.ACCENT))
        p.drawEllipse(QPointF(split_x, handle_y), 7, 7)

    def _paint_badge(self, p: QPainter, rect: QRectF) -> None:
        labels = {MODE_SPLIT: "Original  |  Protected", MODE_AFTER: "Protected",
                  MODE_BEFORE: "Original", MODE_DIFF: f"Difference x{DIFF_GAIN:.0f}"}
        text = "Original (hold)" if self._peek else labels.get(self.mode, "")
        if not text:
            return
        p.setPen(QColor(theme.TEXT_DIM))
        f = QFont()
        f.setPointSize(9)
        p.setFont(f)
        p.drawText(QRectF(rect.left(), rect.bottom() + 3, rect.width(), 18),
                   Qt.AlignHCenter | Qt.AlignTop, text)

    # -------------------------------------------------------------- input

    def _near_split(self, x: float) -> bool:
        if self.mode != MODE_SPLIT or self._before is None:
            return False
        rect = self._target_rect()
        return abs(x - (rect.left() + rect.width() * self.split)) < 12

    def mousePressEvent(self, event: QMouseEvent):  # noqa: N802
        if self._before is None:
            return
        if event.button() == Qt.LeftButton and self._near_split(event.position().x()):
            self._dragging_split = True
        elif event.button() in (Qt.LeftButton, Qt.MiddleButton):
            self._panning = True
            self.setCursor(Qt.ClosedHandCursor)
        self._last_mouse = event.position().toPoint()

    def mouseMoveEvent(self, event: QMouseEvent):  # noqa: N802
        if self._before is None:
            return
        pos = event.position()
        if self._dragging_split:
            rect = self._target_rect()
            if rect.width() > 0:
                self.split = float(np.clip((pos.x() - rect.left()) / rect.width(),
                                           0.0, 1.0))
            self.update()
        elif self._panning:
            delta = pos.toPoint() - self._last_mouse
            self.offset += QPointF(delta.x(), delta.y())
            self._fitting = False
            self.update()
        else:
            self.setCursor(Qt.SplitHCursor if self._near_split(pos.x())
                           else Qt.OpenHandCursor)
        self._last_mouse = pos.toPoint()

    def mouseReleaseEvent(self, event: QMouseEvent):  # noqa: N802
        self._panning = False
        self._dragging_split = False
        self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, event: QMouseEvent):  # noqa: N802
        self.fit() if not self._fitting else self.set_zoom(1.0)

    def wheelEvent(self, event: QWheelEvent):  # noqa: N802
        if self._before is None:
            return
        step = 1.0015 ** event.angleDelta().y()
        self.set_zoom(self.zoom * step, event.position())

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._peek = True
            self.update()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._peek = False
            self.update()
        else:
            super().keyReleaseEvent(event)
