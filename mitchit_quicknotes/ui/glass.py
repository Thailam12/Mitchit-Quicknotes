"""Glassmorphism surface.

Real frost requires a blurred copy of whatever sits behind the panel. On this
stack the blur is executed by Qt's native C++ ``QGraphicsBlurEffect`` pipeline
(cached per panel size so no repaint ever re-blurs), with a Pillow Gaussian
fallback if the effect cannot be instantiated.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene, QWidget

from ..core.constants import GLASS_RADIUS, PALETTE

NATIVE_BACKEND = "CXX_QT_GRAPHICSBLUR"
PILLOW_BACKEND = "PILLOW_GAUSSIAN_BLUR"


def native_blur_available() -> bool:
    try:
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(4.0)
        effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
        return True
    except (RuntimeError, AttributeError):
        return False


def blur_pixmap(pixmap: QPixmap, radius: float) -> QPixmap:
    """Native C++ blur of an arbitrary pixmap (source is preserved)."""
    if pixmap.isNull():
        return pixmap
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(pixmap)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(max(1.0, radius))
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    result = QPixmap(pixmap.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    try:
        scene.render(painter, QRectF(result.rect()), QRectF(pixmap.rect()))
    finally:
        painter.end()
        scene.clear()
    return result


def _pillow_blur(pixmap: QPixmap, radius: float) -> QPixmap:
    try:
        from PIL import Image, ImageFilter

        image = pixmap.toImage().convertToFormat(pixmap.toImage().Format.Format_RGBA8888)
        buffer = image.bits()
        buffer.setsize(image.sizeInBytes())
        pil = Image.frombuffer(
            "RGBA",
            (image.width(), image.height()),
            bytes(buffer),
            "raw",
            "RGBA",
            image.bytesPerLine(),
            1,
        )
        blurred = pil.filter(ImageFilter.GaussianBlur(radius=max(0.5, radius)))
        from PyQt6.QtGui import QImage

        payload = blurred.tobytes("raw", "RGBA")
        qimage = QImage(payload, blurred.width, blurred.height, blurred.width * 4, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qimage.copy())
    except (ImportError, ValueError, OSError, RuntimeError):
        return pixmap


def _as_float(value: object, fallback: float) -> float:
    """Coerce a painting parameter, never trusting a possibly shadowed attribute."""
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    if result != result or result in (float("inf"), float("-inf")):
        return fallback
    return result


class GlassPanel(QWidget):
    """Rounded translucent panel with optional blurred backdrop capture."""

    def __init__(
        self,
        parent: QWidget | None = None,
        radius: int = GLASS_RADIUS,
        tint: str = PALETTE["panel"],
        tint_alpha: int = 176,
        border_color: str = "#25384A",
        blur_radius: float = 26.0,
        backdrop_provider: Callable[[], QPixmap] | None = None,
        backend: str = NATIVE_BACKEND,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        # Surface state is _glass_-prefixed on purpose: subclasses own the short
        # names, and a LayerInspector corner-radius spin box assigned to
        # self._radius used to reach into the base painting maths and hand a
        # QSpinBox to QPainterPath.addRoundedRect.
        self._glass_radius = max(0.0, _as_float(radius, float(GLASS_RADIUS)))
        self._glass_tint = QColor(tint)
        self._glass_tint_alpha = int(_as_float(tint_alpha, 176.0))
        self._glass_border = QColor(border_color)
        self._glass_blur_radius = max(0.5, _as_float(blur_radius, 26.0))
        self._glass_provider = backdrop_provider
        self._glass_backend = backend
        self._glass_backdrop_source: QPixmap | None = None
        self._glass_backdrop_blurred: QPixmap | None = None
        self._glass_source_origin = QPoint(0, 0)
        self._glass_refreshing = False

    # -- backdrop plumbing ------------------------------------------------- #
    def set_backdrop(self, pixmap: QPixmap, origin: QPoint | None = None) -> None:
        self._glass_backdrop_source = pixmap if not pixmap.isNull() else None
        self._glass_source_origin = origin or QPoint(0, 0)
        self._glass_backdrop_blurred = None
        self.update()

    def clear_backdrop(self) -> None:
        self._glass_backdrop_source = None
        self._glass_backdrop_blurred = None
        self.update()

    def refresh_backdrop(self) -> None:
        if self._glass_provider is None or self._glass_refreshing:
            return
        self._glass_refreshing = True
        try:
            pixmap = self._glass_provider()
        except (RuntimeError, ValueError):
            return
        finally:
            self._glass_refreshing = False
        if pixmap is not None and not pixmap.isNull():
            self.set_backdrop(pixmap)

    def _blurred(self) -> QPixmap | None:
        if self._glass_backdrop_source is None:
            return None
        if self._glass_backdrop_blurred is not None:
            return self._glass_backdrop_blurred
        source = self._glass_backdrop_source
        try:
            if source.isNull():
                return None
            if source.size() != self.size():
                source = source.scaled(
                    self.size(),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            if self._glass_backend == PILLOW_BACKEND:
                self._glass_backdrop_blurred = _pillow_blur(source, self._glass_blur_radius)
            else:
                self._glass_backdrop_blurred = blur_pixmap(source, self._glass_blur_radius)
        except (RuntimeError, ValueError):
            return None
        return self._glass_backdrop_blurred

    def _frame_radius(self) -> float:
        """Paint-safe corner radius clamped to the live panel geometry."""
        limit = max(0.0, min(self.width(), self.height()) / 2.0)
        return min(max(0.0, _as_float(self._glass_radius, float(GLASS_RADIUS))), limit)

    # -- painting ---------------------------------------------------------- #
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = self._frame_radius()
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)

        blurred = self._blurred()
        if blurred is not None:
            painter.drawPixmap(self.rect(), blurred)
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0.0, QColor(20, 32, 44, 240))
            gradient.setColorAt(0.55, QColor(13, 20, 28, 236))
            gradient.setColorAt(1.0, QColor(9, 14, 20, 240))
            painter.fillPath(path, gradient)

        tint = QColor(self._glass_tint)
        tint.setAlpha(self._glass_tint_alpha)
        painter.fillPath(path, tint)

        sheen = QLinearGradient(rect.topLeft(), QRectF(rect).bottomLeft())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 26))
        sheen.setColorAt(0.35, QColor(255, 255, 255, 6))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillPath(path, sheen)

        painter.setClipping(False)
        painter.setPen(QColor(self._glass_border.red(), self._glass_border.green(), self._glass_border.blue(), 210))
        painter.drawPath(path)

        highlight = QPainterPath()
        highlight.moveTo(rect.left() + radius * 0.8, rect.top() + 1.0)
        highlight.lineTo(rect.right() - radius * 0.8, rect.top() + 1.0)
        painter.setPen(QColor(255, 255, 255, 46))
        painter.drawPath(highlight)
        painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._glass_backdrop_blurred = None
        self.refresh_backdrop()
