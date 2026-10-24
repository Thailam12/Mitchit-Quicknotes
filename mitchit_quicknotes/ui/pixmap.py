"""Pillow → Qt bitmap bridge (zero-copy detach, correct stride)."""

from __future__ import annotations

from PIL import Image
from PyQt6.QtGui import QImage, QPixmap


def pil_to_qimage(image: Image.Image) -> QImage:
    converted = image.convert("RGBA")
    payload = converted.tobytes("raw", "RGBA")
    qimage = QImage(
        payload,
        converted.width,
        converted.height,
        converted.width * 4,
        QImage.Format.Format_RGBA8888,
    )
    return qimage.copy()


def pil_to_pixmap(image: Image.Image) -> QPixmap:
    return QPixmap.fromImage(pil_to_qimage(image))
