"""Reusable presentation widgets: chips, icon buttons, dock triggers, headers."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import PALETTE
from .icons import icon


class Chip(QLabel):
    """Compact uppercase status pill with a tonal variant."""

    def __init__(self, text: str, tone: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("variant", "chip")
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        font = self.font()
        font.setBold(True)
        self.setFont(font)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_text(self, text: str) -> None:
        if text != self.text():
            self.setText(text)


class HLine(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "hline")
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class IconButton(QToolButton):
    """Square icon-only command target."""

    def __init__(
        self,
        kind: str,
        tooltip: str,
        color: str = PALETTE["text_dim"],
        size: int = 26,
        checkable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._kind = kind
        self._color = color
        self.setToolTip(tooltip)
        self.setIcon(icon(kind, color, 16))
        self.setIconSize(QSize(16, 16))
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCheckable(checkable)
        self.setProperty("selected", "false")

    def set_kind(self, kind: str, color: str | None = None) -> None:
        self._kind = kind
        if color:
            self._color = color
        self.setIcon(icon(self._kind, self._color, 16))

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class DensityCombo(QComboBox):
    """Density stepper with a hand-painted chevron instead of native arrow art."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(PALETTE["text_dim"]))
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        try:
            tip_x = float(self.width()) - 12.0
            mid_y = self.height() / 2.0
            painter.drawLine(QPointF(tip_x - 4.5, mid_y - 2.4), QPointF(tip_x, mid_y + 2.0))
            painter.drawLine(QPointF(tip_x, mid_y + 2.0), QPointF(tip_x + 4.5, mid_y - 2.4))
        finally:
            painter.end()


class DockButton(QAbstractButton):
    """Large right-rail action trigger with icon, label and shortcut hint."""

    activated = pyqtSignal(str)

    def __init__(
        self,
        kind: str,
        title: str,
        subtitle: str,
        accent: str = PALETTE["accent"],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._kind = kind
        self._title = title
        self._subtitle = subtitle
        self._accent = accent
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(104, 58)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(f"{title} · {subtitle}")
        self.clicked.connect(lambda: self.activated.emit(self._kind))

    def sizeHint(self) -> QSize:
        return QSize(116, 62)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        hovered = self.underMouse() and self.isEnabled()
        pressed = self.isDown()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 11.0

        if pressed:
            base_top, base_bottom = QColor(28, 58, 92), QColor(20, 40, 64)
        elif hovered:
            base_top, base_bottom = QColor(26, 42, 58), QColor(17, 27, 38)
        else:
            base_top, base_bottom = QColor(18, 28, 38), QColor(13, 20, 28)

        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, base_top)
        gradient.setColorAt(1.0, base_bottom)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, gradient)

        border = QColor(self._accent) if hovered else QColor(30, 44, 58)
        if hovered:
            border.setAlpha(170)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        if hovered:
            bar = QRectF(rect.left() + 1.0, rect.top() + 10.0, 2.6, rect.height() - 20.0)
            accent_color = QColor(self._accent)
            accent_color.setAlpha(230)
            painter.fillRect(bar, accent_color)

        icon_pixmap = icon(self._kind, self._accent if (hovered or pressed) else "#8FA3B6", 20).pixmap(20, 20)
        painter.drawPixmap(QPoint(int(rect.left()) + 12, int(rect.center().y()) - 10), icon_pixmap)

        title_font = QFont(self.font())
        title_font.setPixelSize(max(10, int(self.font().pixelSize() * 0.95)))
        title_font.setBold(True)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.5)
        painter.setFont(title_font)
        painter.setPen(QColor(PALETTE["text"] if self.isEnabled() else PALETTE["text_faint"]))
        painter.drawText(
            QRectF(rect.left() + 42, rect.top() + 12, rect.width() - 50, 16),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._title,
        )

        sub_font = QFont(self.font())
        sub_font.setPixelSize(max(8, int(self.font().pixelSize() * 0.76)))
        painter.setFont(sub_font)
        painter.setPen(QColor(PALETTE["text_faint"]))
        painter.drawText(
            QRectF(rect.left() + 42, rect.top() + 30, rect.width() - 50, 16),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._subtitle,
        )
        painter.end()


class SectionHeader(QWidget):
    """Eyebrow title row with an optional hint and right-aligned controls."""

    def __init__(
        self,
        title: str,
        hint: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._title = QLabel(title.upper())
        self._title.setProperty("role", "section")
        self._layout.addWidget(self._title)
        self._hint = QLabel(hint)
        self._hint.setProperty("role", "faint")
        self._layout.addWidget(self._hint)
        self._layout.addStretch(1)

    def add_control(self, widget: QWidget) -> None:
        self._layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)

    def set_hint(self, text: str) -> None:
        self._hint.setText(text)

    def set_title(self, text: str) -> None:
        self._title.setText(text.upper())


class HScrollRow(QScrollArea):
    """Horizontal card strip that never shows a vertical scrollbar."""

    def __init__(self, spacing: int = 10, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._content = QWidget()
        self._content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._layout = QHBoxLayout(self._content)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(spacing)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setWidget(self._content)

    @property
    def content_layout(self) -> QHBoxLayout:
        return self._layout

    def add_card(self, widget: QWidget) -> None:
        self._layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def clear_cards(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def scroll_by(self, delta: int) -> None:
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() + delta)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta:
            self.scroll_by(-delta)
            event.accept()
        else:
            super().wheelEvent(event)


class StatusDot(QWidget):
    """Painted status beacon used by the header shield pill."""

    def __init__(self, color: str = PALETTE["mint"], diameter: int = 8, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(diameter + 6, diameter + 6)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        glow = QColor(self._color)
        glow.setAlpha(60)
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(0, 0, self.width(), self.height()))
        painter.setBrush(self._color)
        inner = QRectF(3, 3, self.width() - 6, self.height() - 6)
        painter.drawEllipse(inner)
        painter.end()


class ToolbarRow(QWidget):
    """Thin strip hosting contextual tool buttons."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(3)

    def add_widget(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)

    def add_stretch(self) -> None:
        self._layout.addStretch(1)

    def add_gap(self, width: int = 8) -> None:
        spacer = QWidget()
        spacer.setFixedWidth(width)
        self._layout.addWidget(spacer)


def labelled(label_text: str, widget: QWidget, spacing: int = 8) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    text = QLabel(label_text)
    text.setProperty("role", "muted")
    layout.addWidget(text)
    layout.addWidget(widget, 1)
    return container


def make_button(text: str, on_click: Callable[[], None], role: str = "ghost", width: int | None = None) -> QWidget:
    from PyQt6.QtWidgets import QPushButton

    button = QPushButton(text)
    button.setProperty("role", role)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(lambda: on_click())
    if width:
        button.setFixedWidth(width)
    return button
