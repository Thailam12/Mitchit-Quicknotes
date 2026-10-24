"""Top region: graphical templates carousel."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QAbstractButton, QHBoxLayout, QVBoxLayout, QWidget

from ..core.constants import PALETTE
from ..core.templates import TemplateSpec
from .widgets import HScrollRow, IconButton, SectionHeader


class TemplateCard(QAbstractButton):
    """Pillow-rendered template micro-thumbnail with vector label plate."""

    chosen = pyqtSignal(str)

    def __init__(self, spec: TemplateSpec, thumbnail: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self._thumbnail = thumbnail
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(184, 86)
        self.setToolTip(f"{spec.label} · {spec.tagline}")
        self.setMouseTracking(True)
        self.clicked.connect(lambda: self.chosen.emit(self.spec.key))

    def sizeHint(self) -> QSize:
        return QSize(184, 86)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, QColor("#0F1720" if not self._hover else "#14202C"))
        painter.setPen(QPen(QColor(self.spec.accent if self._hover else "#1E2C3A"), 1.0))
        painter.drawPath(path)

        thumb_rect = QRectF(rect.left() + 7, rect.top() + 6, rect.width() - 14, 50)
        thumb_path = QPainterPath()
        thumb_path.addRoundedRect(thumb_rect, 6, 6)
        painter.save()
        painter.setClipPath(thumb_path)
        if not self._thumbnail.isNull():
            painter.drawPixmap(thumb_rect, self._thumbnail, QRectF(self._thumbnail.rect()))
        else:
            painter.fillRect(thumb_rect, QColor("#0A1017"))
        painter.restore()
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(thumb_rect, 6, 6)

        accent = QColor(self.spec.accent)
        accent.setAlpha(240 if self._hover else 150)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(thumb_rect.left(), thumb_rect.bottom() + 4, 14, 2.4), 1.2, 1.2)

        title_font = QFont(self.font())
        title_font.setPixelSize(10)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(PALETTE["text"] if self._hover else "#C9D8E6"))
        painter.drawText(
            QRectF(thumb_rect.left() + 20, thumb_rect.bottom() + 1, thumb_rect.width() - 20, 14),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.spec.label,
        )
        painter.end()


class TemplateCarousel(QWidget):
    templateChosen = pyqtSignal(str)

    def __init__(self, entries: list[tuple[TemplateSpec, QPixmap]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = SectionHeader("Templates", "vector quick-start layouts")
        self._count = QWidget()
        controls = QHBoxLayout(self._count)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        previous = IconButton("left", "Scroll templates left", PALETTE["text_dim"], 24)
        previous.clicked.connect(lambda: self._row.scroll_by(-400))
        next_button = IconButton("right", "Scroll templates right", PALETTE["text_dim"], 24)
        next_button.clicked.connect(lambda: self._row.scroll_by(400))
        controls.addWidget(previous)
        controls.addWidget(next_button)
        header.add_control(self._count)
        layout.addWidget(header)

        self._row = HScrollRow(spacing=10)
        self._row.setFixedHeight(88)
        for spec, thumbnail in entries:
            card = TemplateCard(spec, thumbnail)
            card.chosen.connect(self.templateChosen)
            self._row.add_card(card)
        layout.addWidget(self._row)

    def scroll_by(self, delta: int) -> None:
        self._row.scroll_by(delta)
