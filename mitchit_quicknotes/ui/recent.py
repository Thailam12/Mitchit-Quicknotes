"""Bottom region: recent notes grid with Pillow micro-thumbnails.

Thumbnails are taken straight from the stored pixels of each ``.png`` container,
so the grid shows exactly what an outside viewer would see, while the badge on
every card reports whether that same file also carries its editable tree.
"""

from __future__ import annotations

from PIL import Image
from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QAbstractButton, QHBoxLayout, QVBoxLayout, QWidget

from ..core import container
from ..core.constants import PALETTE
from ..core.model import NoteSummary
from ..session import StudioSession
from .pixmap import pil_to_pixmap
from .widgets import HScrollRow, IconButton, SectionHeader


class NoteCard(QAbstractButton):
    openRequested = pyqtSignal(str)

    def __init__(self, summary: NoteSummary, thumbnail: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.summary = summary
        self._thumbnail = thumbnail
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(168, 82)
        self.setToolTip(
            f"{summary.title} · {summary.nodes} nodes · {summary.size_label} · "
            f"{summary.tree_label}"
        )
        self.clicked.connect(lambda: self.openRequested.emit(str(self.summary.path)))
        self.setMouseTracking(True)

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
        painter.setPen(QPen(QColor(self.summary.accent if self._hover else "#1E2C3A"), 1.0))
        painter.drawPath(path)

        thumb_rect = QRectF(rect.left() + 7, rect.top() + 6, rect.width() - 14, 42)
        thumb_path = QPainterPath()
        thumb_path.addRoundedRect(thumb_rect, 5, 5)
        painter.save()
        painter.setClipPath(thumb_path)
        if not self._thumbnail.isNull():
            painter.drawPixmap(thumb_rect, self._thumbnail, QRectF(self._thumbnail.rect()))
        else:
            painter.fillRect(thumb_rect, QColor("#0A1017"))
        painter.restore()

        title_font = QFont(self.font())
        title_font.setPixelSize(9)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(PALETTE["text"] if self._hover else "#C6D5E4"))
        title = self.summary.title
        metrics = painter.fontMetrics()
        painter.drawText(
            QRectF(thumb_rect.left(), thumb_rect.bottom() + 2, thumb_rect.width(), 11),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics.elidedText(title, Qt.TextElideMode.ElideRight, int(thumb_rect.width())),
        )

        meta_font = QFont(self.font())
        meta_font.setPixelSize(8)
        painter.setFont(meta_font)
        painter.setPen(QColor(PALETTE["text_faint"]))
        painter.drawText(
            QRectF(thumb_rect.left(), thumb_rect.bottom() + 13, thumb_rect.width(), 10),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            f"{self.summary.stamp} · {self.summary.nodes} nodes",
        )
        # Container badge: VEC means the image carries its own layer tree and is
        # therefore re-openable; FLAT marks a plain imported picture.
        editable = self.summary.editable
        painter.setPen(QColor(self.summary.accent if editable else PALETTE["text_faint"]))
        painter.drawText(
            QRectF(thumb_rect.left(), thumb_rect.bottom() + 13, thumb_rect.width() - 8, 10),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            "VEC" if editable else "FLAT",
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.summary.accent))
        painter.drawEllipse(QRectF(thumb_rect.right() - 5, thumb_rect.bottom() + 15, 4, 4))
        painter.end()


class RecentGrid(QWidget):
    noteChosen = pyqtSignal(str)
    refreshRequested = pyqtSignal()

    def __init__(self, session: StudioSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self._cache: dict[tuple[str, float], QPixmap] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._header = SectionHeader("Recent notes", "universal PNG library")
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(4)
        refresh = IconButton("refresh", "Rescan local notes directory", PALETTE["text_dim"], 24)
        refresh.clicked.connect(self.refreshRequested)
        controls_layout.addWidget(refresh)
        self._header.add_control(controls)
        layout.addWidget(self._header)

        self._row = HScrollRow(spacing=10)
        self._row.setFixedHeight(84)
        layout.addWidget(self._row)

        self.reload()

    def _thumbnail(self, summary: NoteSummary) -> QPixmap:
        key = (str(summary.path), summary.modified)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            image = container.thumbnail(summary.path, 336)
            pixmap = pil_to_pixmap(image)
        except (OSError, ValueError, Image.DecompressionBombError):
            pixmap = QPixmap()
        self._cache[key] = pixmap
        if len(self._cache) > 48:
            oldest = next(iter(self._cache))
            self._cache.pop(oldest, None)
        return pixmap

    def reload(self) -> None:
        self._row.clear_cards()
        summaries = self.session.summaries(14)
        self._header.set_hint(f"{len(summaries)} local notes")
        if not summaries:
            empty = QWidget()
            empty.setFixedSize(320, 80)
            empty_layout = QVBoxLayout(empty)
            empty_layout.setContentsMargins(2, 0, 0, 0)
            from PyQt6.QtWidgets import QLabel

            title = QLabel("NO LOCAL NOTES YET")
            title.setProperty("role", "section")
            body = QLabel("Press SAVE in the right dock to seal the first PNG container into notes/.")
            body.setProperty("role", "faint")
            body.setWordWrap(True)
            empty_layout.addWidget(title)
            empty_layout.addWidget(body)
            empty_layout.addStretch(1)
            self._row.add_card(empty)
            return
        for summary in summaries:
            card = NoteCard(summary, self._thumbnail(summary))
            card.openRequested.connect(self.noteChosen)
            self._row.add_card(card)
