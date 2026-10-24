"""Geometric telemetry console: node counts, OCR latency, thermal state, ledger."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QTextEdit, QVBoxLayout, QWidget

from ..core.constants import PALETTE
from ..core.ledger import Ledger
from ..session import StudioSession
from .glass import GlassPanel
from .widgets import Chip, IconButton


class MetricTile(QWidget):
    def __init__(self, label: str, value: str = "—", accent: str = PALETTE["text"], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = label
        self._value = value
        self._accent = accent
        self.setMinimumHeight(36)

    def set_value(self, value: str, accent: str | None = None) -> None:
        if accent:
            self._accent = accent
        if value != self._value:
            self._value = value
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 7, 7)
        painter.fillPath(path, QColor(255, 255, 255, 9))
        painter.setPen(QPen(QColor(255, 255, 255, 16), 1))
        painter.drawPath(path)

        font = painter.font()
        font.setPixelSize(7)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        painter.setFont(font)
        painter.setPen(QColor(PALETTE["text_faint"]))
        painter.drawText(
            QRectF(rect.left() + 7, rect.top() + 4, rect.width() - 12, 10),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._label.upper(),
        )

        value_font = painter.font()
        value_font.setPixelSize(12)
        value_font.setBold(True)
        value_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0)
        painter.setFont(value_font)
        painter.setPen(QColor(self._accent))
        metrics = painter.fontMetrics()
        painter.drawText(
            QRectF(rect.left() + 7, rect.top() + 14, rect.width() - 12, 18),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics.elidedText(self._value, Qt.TextElideMode.ElideRight, int(rect.width() - 14)),
        )
        painter.end()


class TelemetryHUD(GlassPanel):
    refreshRequested = pyqtSignal()

    LEVEL_COLORS = {
        "INFO": "#8CA0B3",
        "WARN": PALETTE["amber"],
        "ERROR": PALETTE["danger"],
        "WRITE": PALETTE["mint"],
    }

    def __init__(
        self,
        session: StudioSession,
        parent: QWidget | None = None,
        backend: str = "CXX_QT_GRAPHICSBLUR",
    ) -> None:
        super().__init__(
            parent,
            radius=14,
            tint_alpha=168,
            border_color="#233647",
            blur_radius=18.0,
            backend=backend,
        )
        self.session = session
        self._backend = backend
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(7)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("TELEMETRY")
        title.setProperty("role", "section")
        header.addWidget(title)
        header.addStretch(1)
        self._idle_chip = Chip("IDLE FREEZE", "mint")
        header.addWidget(self._idle_chip)
        layout.addLayout(header)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(5)
        grid.setVerticalSpacing(5)
        self._nodes = MetricTile("nodes", "0", PALETTE["text"])
        self._shapes = MetricTile("shapes", "0", PALETTE["mint"])
        self._selection = MetricTile("selection · mass", "0", PALETTE["mint"])
        self._clipboard = MetricTile("clipboard c/v/x", "0 nodes", PALETTE["accent"])
        self._raster = MetricTile("raster", "0.0 ms", PALETTE["accent"])
        self._ocr = MetricTile("ocr", "0 µs", PALETTE["amber"])
        self._temp = MetricTile("cpu temp", "n/a", PALETTE["violet"])
        self._writes = MetricTile("disk writes", "0", PALETTE["text_dim"])
        self._rss = MetricTile("rss", "0 MiB", PALETTE["text_dim"])
        self._egress = MetricTile("egress", "open", PALETTE["text_dim"])
        self._container = MetricTile("container · png", "unsaved", PALETTE["mint"])
        self._tree = MetricTile("chunk array", "0 KiB", PALETTE["violet"])
        for index, tile in enumerate(
            (
                self._nodes,
                self._shapes,
                self._selection,
                self._clipboard,
                self._raster,
                self._ocr,
                self._temp,
                self._writes,
                self._rss,
                self._egress,
                self._container,
                self._tree,
            )
        ):
            grid.addWidget(tile, index // 2, index % 2)
        layout.addLayout(grid)

        console_header = QHBoxLayout()
        console_header.setContentsMargins(0, 0, 0, 0)
        label = QLabel("LEDGER")
        label.setProperty("role", "section")
        console_header.addWidget(label)
        console_header.addStretch(1)
        self._engine = QLabel(f"GLASS · {backend.replace('CXX_QT_', 'QT ').replace('_', ' ')}")
        self._engine.setProperty("role", "faint")
        console_header.addWidget(self._engine)
        layout.addLayout(console_header)

        self._console = QTextEdit()
        self._console.setReadOnly(True)
        self._console.setFrameShape(QTextEdit.Shape.NoFrame)
        self._console.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._console.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._console.setStyleSheet(
            "QTextEdit { background: rgba(4, 8, 12, 140); border: 1px solid #1B2A38; "
            f"border-radius: 8px; padding: 5px; font-family: \"DejaVu Sans Mono\"; font-size: 9px; "
            f"color: {PALETTE['text_dim']}; }}"
        )
        layout.addWidget(self._console, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self._status = QLabel("explicit I/O only")
        self._status.setProperty("role", "faint")
        footer.addWidget(self._status)
        footer.addStretch(1)
        refresh = IconButton("refresh", "Re-read hardware telemetry", PALETTE["text_dim"], 22)
        refresh.clicked.connect(self.refreshRequested)
        footer.addWidget(refresh)
        layout.addLayout(footer)

        self.update_metrics()
        self.refresh_log()

    def update_metrics(self) -> None:
        session = self.session
        counts = session.document.shape_counts()
        snapshot = session.snapshot()
        self._nodes.set_value(str(session.document.node_count()))
        self._shapes.set_value(str(counts["line"] + counts["rect"] + counts["ellipse"]))
        mass = len(session.selection)
        self._selection.set_value(f"{mass} nodes", PALETTE["mint"] if mass > 1 else PALETTE["text"])
        buffer = session.clipboard
        self._clipboard.set_value(
            f"{buffer.depth()} · {buffer.bytes_used // 1024} KiB",
            PALETTE["accent"] if buffer.depth() else PALETTE["text_faint"],
        )
        self._raster.set_value(f"{session.raster.elapsed_ms:.1f} ms")
        # Open-container telemetry: what the last seal actually wrote.
        record = session.last_container or {}
        probe = session.container_probe or {}
        mode = str(record.get("mode", ""))
        if mode == "vector":
            self._container.set_value(
                f"vector · {record.get('chunks', 0)} chunks", PALETTE["mint"]
            )
        elif mode == "raster":
            self._container.set_value("flat raster", PALETTE["amber"])
        else:
            self._container.set_value("unsaved · png only", PALETTE["text_faint"])
        tree_bytes = float(record.get("tree_bytes", 0) or probe.get("tree_bytes", 0) or 0)
        slices = int(probe.get("slices", 0) or 0)
        self._tree.set_value(f"{tree_bytes / 1024.0:.1f} KiB · {slices} slice")
        result = session.last_ocr
        if result is None:
            self._ocr.set_value("idle")
        else:
            value = f"{result.latency_ms * 1000:.0f} µs" if result.latency_ms < 1.0 else f"{result.latency_ms:.1f} ms"
            self._ocr.set_value(value, PALETTE["mint"] if result.within_budget else PALETTE["amber"])
        hardware = snapshot.get("hardware")
        if hardware is not None:
            self._temp.set_value(f"{hardware.temp_label}", PALETTE["violet"])
            self._rss.set_value(f"{snapshot['rss_mb']:.0f} MiB")
        writes = session.ledger.counters.get("io.writes", 0)
        self._writes.set_value(str(writes))
        report = session.shield.report
        if report is not None:
            self._egress.set_value("VETO" if report.armed else "OPEN", PALETTE["danger"] if report.armed else PALETTE["mint"])
        self._status.setText(
            f"{session.raster.ops} rasters · {session.raster.suppressed} suppressed · "
            f"{buffer.pastes} pastes · {snapshot['blocked']} blocked"
        )

    def refresh_log(self) -> None:
        ledger: Ledger = self.session.ledger
        events = list(ledger.events)[-24:]
        rows = []
        for event in events:
            color = self.LEVEL_COLORS.get(event.level, PALETTE["text_dim"])
            stamp = event.render()[:8]
            rows.append(
                f'<span style="color:#4A5D6E">{stamp}</span> '
                f'<span style="color:{color}">{event.level.lower():<5}</span> '
                f'<span style="color:#7C93A8">{event.source}</span> '
                f'<span style="color:#A9BCCD">{event.message}</span>'
            )
        body = "<br>".join(rows) if rows else (
            f'<span style="color:{PALETTE["text_faint"]}">awaiting first explicit command…</span>'
        )
        self._console.setHtml(f'<div style="font-family:DejaVu Sans Mono;font-size:9px">{body}</div>')
        bar = self._console.verticalScrollBar()
        bar.setValue(bar.maximum())

    def update_all(self) -> None:
        self.update_metrics()
        self.refresh_log()
