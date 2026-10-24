"""Absolute right sidebar: the four structural action triggers."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..core.constants import PALETTE
from .glass import GlassPanel
from .widgets import Chip, DockButton


class ActionsDock(GlassPanel):
    sigAction = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, backend: str = "CXX_QT_GRAPHICSBLUR") -> None:
        super().__init__(
            parent,
            radius=14,
            tint_alpha=172,
            border_color="#273A4C",
            blur_radius=20.0,
            backend=backend,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("ACTIONS")
        title.setProperty("role", "section")
        header.addWidget(title)
        header.addStretch(1)
        self._badge = Chip("4 NODES", "accent")
        header.addWidget(self._badge)
        layout.addLayout(header)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        specs = (
            ("new", "NEW", "Ctrl+N", PALETTE["mint"], 0, 0),
            ("open", "OPEN", "Ctrl+O", PALETTE["accent"], 0, 1),
            ("save", "SAVE", "Ctrl+S", PALETTE["violet"], 1, 0),
            ("save_as", "SAVE AS", "Ctrl+Shift+S", PALETTE["amber"], 1, 1),
        )
        self.buttons: dict[str, DockButton] = {}
        for kind, title_text, hint, accent, row, column in specs:
            button = DockButton(kind, title_text, hint, accent, self)
            button.activated.connect(self.sigAction)
            grid.addWidget(button, row, column)
            self.buttons[kind] = button
        layout.addLayout(grid)

        note = QLabel("Writes are released only by these explicit triggers.")
        note.setProperty("role", "faint")
        note.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(note)

    def set_badge(self, text: str) -> None:
        self._badge.set_text(text)

    def set_busy(self, busy: bool) -> None:
        for button in self.buttons.values():
            button.setEnabled(not busy)
