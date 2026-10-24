"""Programmatic vector icon set — no external asset files, no image CDNs."""

from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QApplication

_STROKE = 0.085


def _dpr() -> float:
    app = QApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    if screen is None:
        return 1.0
    return max(1.0, float(screen.devicePixelRatio()))


def _pen(painter: QPainter, color: str, size: float, width_factor: float = _STROKE) -> None:
    pen = QPen(QColor(color))
    pen.setWidthF(max(1.15, size * width_factor))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _r(painter: QPainter, x: float, y: float, w: float, h: float, radius: float = 0.0) -> None:
    if radius:
        painter.drawRoundedRect(QRectF(x, y, w, h), radius, radius)
    else:
        painter.drawRect(QRectF(x, y, w, h))


def _draw_new(p: QPainter, s: float) -> None:
    _r(p, s * 0.16, s * 0.12, s * 0.52, s * 0.72, s * 0.08)
    p.drawLine(QPointF(s * 0.68, s * 0.24), QPointF(s * 0.86, s * 0.24))
    p.drawLine(QPointF(s * 0.77, s * 0.13), QPointF(s * 0.77, s * 0.35))
    p.drawLine(QPointF(s * 0.30, s * 0.38), QPointF(s * 0.54, s * 0.38))
    p.drawLine(QPointF(s * 0.30, s * 0.54), QPointF(s * 0.54, s * 0.54))


def _draw_open(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.12, s * 0.76)
    path.lineTo(s * 0.12, s * 0.28)
    path.lineTo(s * 0.42, s * 0.28)
    path.lineTo(s * 0.50, s * 0.40)
    path.lineTo(s * 0.86, s * 0.40)
    path.lineTo(s * 0.86, s * 0.76)
    path.closeSubpath()
    p.drawPath(path)


def _draw_save(p: QPainter, s: float) -> None:
    _r(p, s * 0.15, s * 0.13, s * 0.70, s * 0.74, s * 0.09)
    _r(p, s * 0.32, s * 0.13, s * 0.36, s * 0.26, 0)
    _r(p, s * 0.30, s * 0.56, s * 0.40, s * 0.31, 0)


def _draw_save_as(p: QPainter, s: float) -> None:
    _r(p, s * 0.10, s * 0.13, s * 0.56, s * 0.62, s * 0.08)
    _r(p, s * 0.22, s * 0.13, s * 0.28, s * 0.20, 0)
    p.drawLine(QPointF(s * 0.52, s * 0.86), QPointF(s * 0.88, s * 0.86))
    p.drawLine(QPointF(s * 0.70, s * 0.74), QPointF(s * 0.88, s * 0.86))


def _draw_text(p: QPainter, s: float) -> None:
    font = QFont("DejaVu Serif")
    font.setPixelSize(int(s * 0.86))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, 0, s, s), int(Qt.AlignmentFlag.AlignCenter), "T")


def _draw_rect(p: QPainter, s: float) -> None:
    _r(p, s * 0.16, s * 0.22, s * 0.68, s * 0.56, s * 0.07)


def _draw_ellipse(p: QPainter, s: float) -> None:
    p.drawEllipse(QRectF(s * 0.16, s * 0.22, s * 0.68, s * 0.56))


def _draw_line(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.16, s * 0.80), QPointF(s * 0.84, s * 0.22))
    p.drawEllipse(QRectF(s * 0.10, s * 0.74, s * 0.14, s * 0.14))
    p.drawEllipse(QRectF(s * 0.76, s * 0.16, s * 0.14, s * 0.14))


def _draw_image(p: QPainter, s: float) -> None:
    _r(p, s * 0.12, s * 0.20, s * 0.76, s * 0.60, s * 0.08)
    p.drawEllipse(QRectF(s * 0.26, s * 0.32, s * 0.14, s * 0.14))
    path = QPainterPath()
    path.moveTo(s * 0.18, s * 0.74)
    path.lineTo(s * 0.42, s * 0.48)
    path.lineTo(s * 0.58, s * 0.62)
    path.lineTo(s * 0.72, s * 0.46)
    path.lineTo(s * 0.86, s * 0.74)
    p.drawPath(path)


def _draw_duplicate(p: QPainter, s: float) -> None:
    _r(p, s * 0.12, s * 0.12, s * 0.50, s * 0.50, s * 0.07)
    _r(p, s * 0.36, s * 0.36, s * 0.52, s * 0.52, s * 0.07)


def _draw_trash(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.14, s * 0.26), QPointF(s * 0.86, s * 0.26))
    p.drawLine(QPointF(s * 0.38, s * 0.26), QPointF(s * 0.38, s * 0.16))
    p.drawLine(QPointF(s * 0.38, s * 0.16), QPointF(s * 0.62, s * 0.16))
    p.drawLine(QPointF(s * 0.62, s * 0.16), QPointF(s * 0.62, s * 0.26))
    path = QPainterPath()
    path.moveTo(s * 0.24, s * 0.26)
    path.lineTo(s * 0.29, s * 0.86)
    path.lineTo(s * 0.71, s * 0.86)
    path.lineTo(s * 0.76, s * 0.26)
    p.drawPath(path)
    p.drawLine(QPointF(s * 0.42, s * 0.40), QPointF(s * 0.44, s * 0.72))
    p.drawLine(QPointF(s * 0.58, s * 0.40), QPointF(s * 0.56, s * 0.72))


def _draw_up(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.24, s * 0.64), QPointF(s * 0.50, s * 0.36))
    p.drawLine(QPointF(s * 0.50, s * 0.36), QPointF(s * 0.76, s * 0.64))


def _draw_down(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.24, s * 0.36), QPointF(s * 0.50, s * 0.64))
    p.drawLine(QPointF(s * 0.50, s * 0.64), QPointF(s * 0.76, s * 0.36))


def _draw_left(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.64, s * 0.24), QPointF(s * 0.36, s * 0.50))
    p.drawLine(QPointF(s * 0.36, s * 0.50), QPointF(s * 0.64, s * 0.76))


def _draw_right(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.36, s * 0.24), QPointF(s * 0.64, s * 0.50))
    p.drawLine(QPointF(s * 0.64, s * 0.50), QPointF(s * 0.36, s * 0.76))


def _draw_plus(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.50, s * 0.22), QPointF(s * 0.50, s * 0.78))
    p.drawLine(QPointF(s * 0.22, s * 0.50), QPointF(s * 0.78, s * 0.50))


def _draw_minus(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.22, s * 0.50), QPointF(s * 0.78, s * 0.50))


def _draw_fit(p: QPainter, s: float) -> None:
    for x, y, dx, dy in (
        (0.16, 0.16, 1, 1),
        (0.84, 0.16, -1, 1),
        (0.16, 0.84, 1, -1),
        (0.84, 0.84, -1, -1),
    ):
        p.drawLine(QPointF(s * x, s * y), QPointF(s * (x + 0.18 * dx), s * y))
        p.drawLine(QPointF(s * x, s * y), QPointF(s * x, s * (y + 0.18 * dy)))


def _draw_sliders(p: QPainter, s: float) -> None:
    for index, y in enumerate((0.28, 0.50, 0.72)):
        p.drawLine(QPointF(s * 0.16, s * y), QPointF(s * 0.84, s * y))
        knob = 0.32 if index == 0 else (0.62 if index == 1 else 0.46)
        p.setBrush(p.brush())
        p.drawEllipse(QRectF(s * knob - s * 0.07, s * y - s * 0.07, s * 0.14, s * 0.14))


def _draw_scan(p: QPainter, s: float) -> None:
    for x, y, dx, dy in (
        (0.14, 0.14, 1, 1),
        (0.86, 0.14, -1, 1),
        (0.14, 0.86, 1, -1),
        (0.86, 0.86, -1, -1),
    ):
        p.drawLine(QPointF(s * x, s * (y + 0.16 * dy)), QPointF(s * x, s * y))
        p.drawLine(QPointF(s * x, s * y), QPointF(s * (x + 0.16 * dx), s * y))
    p.drawLine(QPointF(s * 0.20, s * 0.50), QPointF(s * 0.80, s * 0.50))


def _draw_grid(p: QPainter, s: float) -> None:
    for x in (0.18, 0.54):
        for y in (0.18, 0.54):
            _r(p, s * x, s * y, s * 0.28, s * 0.28, s * 0.04)


def _draw_close(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.28, s * 0.28), QPointF(s * 0.72, s * 0.72))
    p.drawLine(QPointF(s * 0.72, s * 0.28), QPointF(s * 0.28, s * 0.72))


def _draw_minimise(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.26, s * 0.50), QPointF(s * 0.74, s * 0.50))


def _draw_shield(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.50, s * 0.12)
    path.lineTo(s * 0.84, s * 0.26)
    path.lineTo(s * 0.84, s * 0.54)
    path.cubicTo(s * 0.84, s * 0.72, s * 0.68, s * 0.84, s * 0.50, s * 0.90)
    path.cubicTo(s * 0.32, s * 0.84, s * 0.16, s * 0.72, s * 0.16, s * 0.54)
    path.lineTo(s * 0.16, s * 0.26)
    path.closeSubpath()
    p.drawPath(path)


def _draw_spark(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.50, s * 0.08)
    path.lineTo(s * 0.60, s * 0.40)
    path.lineTo(s * 0.92, s * 0.50)
    path.lineTo(s * 0.60, s * 0.60)
    path.lineTo(s * 0.50, s * 0.92)
    path.lineTo(s * 0.40, s * 0.60)
    path.lineTo(s * 0.08, s * 0.50)
    path.lineTo(s * 0.40, s * 0.40)
    path.closeSubpath()
    p.drawPath(path)


def _draw_zip(p: QPainter, s: float) -> None:
    _r(p, s * 0.14, s * 0.20, s * 0.72, s * 0.64, s * 0.08)
    p.drawLine(QPointF(s * 0.50, s * 0.20), QPointF(s * 0.50, s * 0.84))
    for y in (0.30, 0.42, 0.54, 0.66):
        p.drawLine(QPointF(s * 0.42, s * y), QPointF(s * 0.58, s * y))


def _draw_refresh(p: QPainter, s: float) -> None:
    rect = QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64)
    p.drawArc(rect, 40 * 16, 280 * 16)
    p.drawLine(QPointF(s * 0.72, s * 0.14), QPointF(s * 0.80, s * 0.34))
    p.drawLine(QPointF(s * 0.80, s * 0.34), QPointF(s * 0.58, s * 0.34))


def _draw_eye(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.10, s * 0.50)
    path.cubicTo(s * 0.28, s * 0.22, s * 0.72, s * 0.22, s * 0.90, s * 0.50)
    path.cubicTo(s * 0.72, s * 0.78, s * 0.28, s * 0.78, s * 0.10, s * 0.50)
    p.drawPath(path)
    p.drawEllipse(QRectF(s * 0.42, s * 0.42, s * 0.16, s * 0.16))


def _draw_lock(p: QPainter, s: float) -> None:
    _r(p, s * 0.22, s * 0.44, s * 0.56, s * 0.42, s * 0.08)
    p.drawArc(QRectF(s * 0.32, s * 0.16, s * 0.36, s * 0.46), 0, 180 * 16)


def _draw_dot(p: QPainter, s: float) -> None:
    p.setBrush(p.pen().color())
    p.drawEllipse(QRectF(s * 0.30, s * 0.30, s * 0.40, s * 0.40))


def _draw_wand(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.18, s * 0.82), QPointF(s * 0.66, s * 0.34))
    p.drawLine(QPointF(s * 0.60, s * 0.28), QPointF(s * 0.72, s * 0.40))
    p.drawLine(QPointF(s * 0.78, s * 0.16), QPointF(s * 0.78, s * 0.30))
    p.drawLine(QPointF(s * 0.71, s * 0.23), QPointF(s * 0.85, s * 0.23))


def _draw_layers(p: QPainter, s: float) -> None:
    p.drawLine(QPointF(s * 0.50, s * 0.16), QPointF(s * 0.86, s * 0.36))
    p.drawLine(QPointF(s * 0.86, s * 0.36), QPointF(s * 0.50, s * 0.56))
    p.drawLine(QPointF(s * 0.50, s * 0.56), QPointF(s * 0.14, s * 0.36))
    p.drawLine(QPointF(s * 0.14, s * 0.36), QPointF(s * 0.50, s * 0.16))
    p.drawLine(QPointF(s * 0.14, s * 0.58), QPointF(s * 0.50, s * 0.78))
    p.drawLine(QPointF(s * 0.50, s * 0.78), QPointF(s * 0.86, s * 0.58))


_DRAWERS = {
    "new": _draw_new,
    "open": _draw_open,
    "save": _draw_save,
    "save_as": _draw_save_as,
    "text": _draw_text,
    "rect": _draw_rect,
    "ellipse": _draw_ellipse,
    "line": _draw_line,
    "image": _draw_image,
    "duplicate": _draw_duplicate,
    "trash": _draw_trash,
    "up": _draw_up,
    "down": _draw_down,
    "left": _draw_left,
    "right": _draw_right,
    "plus": _draw_plus,
    "minus": _draw_minus,
    "fit": _draw_fit,
    "sliders": _draw_sliders,
    "scan": _draw_scan,
    "grid": _draw_grid,
    "close": _draw_close,
    "minimise": _draw_minimise,
    "shield": _draw_shield,
    "spark": _draw_spark,
    "zip": _draw_zip,
    "refresh": _draw_refresh,
    "eye": _draw_eye,
    "lock": _draw_lock,
    "dot": _draw_dot,
    "wand": _draw_wand,
    "layers": _draw_layers,
}


def render_icon(kind: str, color: str, size: int = 18) -> QPixmap:
    scale = _dpr()
    pixmap = QPixmap(int(round(size * scale)), int(round(size * scale)))
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    # QPainter already applies the pixmap DPR, so the drawer works in logical units.
    _pen(painter, color, float(size))
    drawer = _DRAWERS.get(kind, _draw_dot)
    try:
        drawer(painter, float(size))
    finally:
        painter.end()
    return pixmap


@lru_cache(maxsize=256)
def icon(kind: str, color: str, size: int = 18) -> QIcon:
    return QIcon(render_icon(kind, color, size))


def icon_kinds() -> tuple[str, ...]:
    return tuple(sorted(_DRAWERS))
