"""Visual theme: palette bridge and density-aware stylesheet compiler."""

from __future__ import annotations

from functools import lru_cache

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

from ..core.constants import PALETTE
from ..core.geometry import LayoutMetrics

UI_FONT_PREFERENCES = (
    "Inter",
    "SF Pro Display",
    "Cantarell",
    "Ubuntu Sans",
    "Ubuntu",
    "Noto Sans",
    "DejaVu Sans",
)


@lru_cache(maxsize=1)
def ui_font_family() -> str:
    try:
        available = set(QFontDatabase.families())
    except RuntimeError:
        available = set()
    for family in UI_FONT_PREFERENCES:
        if family in available:
            return family
    return "DejaVu Sans"


def mono_font_family() -> str:
    try:
        available = set(QFontDatabase.families())
    except RuntimeError:
        available = set()
    for family in ("JetBrains Mono", "Fira Code", "Ubuntu Mono", "DejaVu Sans Mono"):
        if family in available:
            return family
    return "monospace"


def base_font(metrics: LayoutMetrics) -> QFont:
    font = QFont(ui_font_family())
    font.setPixelSize(metrics.font_body)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font


def build_qss(metrics: LayoutMetrics) -> str:
    palette = PALETTE
    body = metrics.font_body
    small = metrics.font_small
    title = metrics.font_title
    radius = metrics.radius
    pad = metrics.pad_small
    return f"""
* {{
    outline: none;
}}
QWidget#Root {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {palette["shell"]}, stop:0.45 #070C12, stop:1 #04070A);
    color: {palette["text"]};
}}
QWidget#HeaderBar {{
    background: rgba(9, 14, 20, 235);
    border-bottom: 1px solid {palette["line"]};
}}
QWidget#FooterBar {{
    background: rgba(7, 11, 16, 240);
    border-top: 1px solid {palette["line"]};
}}
QWidget#Panel {{
    background: {palette["panel"]};
    border: 1px solid {palette["line"]};
    border-radius: {radius}px;
}}
QWidget#Rail {{
    background: rgba(10, 15, 21, 210);
    border-left: 1px solid {palette["line"]};
}}
QLabel {{
    color: {palette["text"]};
    background: transparent;
    font-size: {body}px;
}}
QLabel[role="brand"] {{
    font-size: {title + 2}px;
    font-weight: 800;
    letter-spacing: 2px;
    color: {palette["text"]};
}}
QLabel[role="brand-accent"] {{
    color: {palette["accent"]};
}}
QLabel[role="title"] {{
    font-size: {title}px;
    font-weight: 700;
    color: {palette["text"]};
}}
QLabel[role="section"] {{
    font-size: {max(9, small)}px;
    font-weight: 700;
    letter-spacing: 2px;
    color: {palette["text_faint"]};
}}
QLabel[role="muted"] {{
    color: {palette["text_dim"]};
    font-size: {body}px;
}}
QLabel[role="faint"] {{
    color: {palette["text_faint"]};
    font-size: {small}px;
}}
QLabel[role="mono"] {{
    font-family: "{mono_font_family()}";
    font-size: {small}px;
    color: {palette["text_dim"]};
}}
QLabel[variant="chip"] {{
    background: {palette["panel_alt"]};
    border: 1px solid {palette["line_hi"]};
    border-radius: 10px;
    padding: 3px 9px;
    font-size: {max(9, small - 1)}px;
    font-weight: 700;
    letter-spacing: 1px;
    color: {palette["text_dim"]};
}}
QLabel[variant="chip"][tone="accent"] {{
    background: rgba(78, 161, 255, 28);
    border: 1px solid rgba(78, 161, 255, 92);
    color: {palette["accent"]};
}}
QLabel[variant="chip"][tone="mint"] {{
    background: rgba(111, 231, 193, 26);
    border: 1px solid rgba(111, 231, 193, 88);
    color: {palette["mint"]};
}}
QLabel[variant="chip"][tone="amber"] {{
    background: rgba(255, 182, 72, 26);
    border: 1px solid rgba(255, 182, 72, 86);
    color: {palette["amber"]};
}}
QLabel[variant="chip"][tone="danger"] {{
    background: rgba(255, 95, 126, 28);
    border: 1px solid rgba(255, 95, 126, 90);
    color: {palette["danger"]};
}}
QLabel[variant="chip"][tone="violet"] {{
    background: rgba(167, 139, 250, 26);
    border: 1px solid rgba(167, 139, 250, 90);
    color: {palette["violet"]};
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {max(6, radius - 4)}px;
    padding: 4px;
}}
QToolButton:hover {{
    background: {palette["panel_hi"]};
    border: 1px solid {palette["line_hi"]};
}}
QToolButton:pressed {{
    background: {palette["accent_soft"]};
}}
QToolButton:disabled {{
    background: transparent;
    border: 1px solid transparent;
}}
QToolButton[selected="true"] {{
    background: rgba(78, 161, 255, 34);
    border: 1px solid rgba(78, 161, 255, 110);
}}
QPushButton {{
    background: {palette["panel_alt"]};
    border: 1px solid {palette["line_hi"]};
    border-radius: {max(7, radius - 3)}px;
    padding: 7px {pad + 2}px;
    color: {palette["text"]};
    font-size: {body}px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {palette["panel_hi"]};
    border: 1px solid {palette["accent_soft"]};
}}
QPushButton:pressed {{
    background: {palette["accent_soft"]};
}}
QPushButton:disabled {{
    color: {palette["text_faint"]};
    border: 1px solid {palette["line"]};
}}
QPushButton[role="primary"] {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2E7DE0, stop:0.55 #4EA1FF, stop:1 #6FE7C1);
    color: #04121C;
    border: 1px solid #8ED0FF;
    font-weight: 800;
    letter-spacing: 1px;
}}
QPushButton[role="primary"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #3C8BEE, stop:0.55 #63B2FF, stop:1 #86F0D1);
}}
QPushButton[role="ghost"] {{
    background: transparent;
    border: 1px solid {palette["line"]};
    color: {palette["text_dim"]};
    font-weight: 600;
}}
QPushButton[role="ghost"]:hover {{
    background: {palette["panel_alt"]};
    color: {palette["text"]};
}}
QComboBox, QSpinBox, QLineEdit, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {palette["panel_alt"]};
    border: 1px solid {palette["line_hi"]};
    border-radius: {max(6, radius - 5)}px;
    padding: 6px 8px;
    color: {palette["text"]};
    selection-background-color: {palette["accent_soft"]};
    font-size: {body}px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {palette["accent"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0;
    height: 0;
}}
QComboBox QAbstractItemView {{
    background: {palette["panel"]};
    border: 1px solid {palette["line_hi"]};
    selection-background-color: {palette["accent_soft"]};
    color: {palette["text"]};
    padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 14px;
    background: {palette["panel_hi"]};
    border: 1px solid {palette["line"]};
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollBar:horizontal {{
    height: 6px;
    background: transparent;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {palette["line_hi"]};
    border-radius: 3px;
    min-width: 36px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar:vertical {{
    width: 6px;
    background: transparent;
}}
QScrollBar::handle:vertical {{
    background: {palette["line_hi"]};
    border-radius: 3px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QSplitter::handle {{
    background: transparent;
}}
QCheckBox {{
    color: {palette["text_dim"]};
    font-size: {body}px;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid {palette["line_hi"]};
    background: {palette["panel_alt"]};
}}
QCheckBox::indicator:checked {{
    background: {palette["accent"]};
    border: 1px solid {palette["accent"]};
}}
QDialog {{
    background: {palette["shell"]};
    color: {palette["text"]};
}}
QMenu {{
    background: {palette["panel"]};
    border: 1px solid {palette["line_hi"]};
    padding: 6px;
}}
QMenu::item {{
    padding: 6px 18px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {palette["accent_soft"]};
}}
QToolTip {{
    background: {palette["panel_alt"]};
    color: {palette["text"]};
    border: 1px solid {palette["line_hi"]};
    padding: 5px 8px;
    font-size: {small}px;
}}
QMessageBox {{
    background: {palette["shell"]};
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QFrame[role="hline"] {{
    background: {palette["line"]};
    max-height: 1px;
    border: none;
}}
"""


def apply_theme(app: QApplication, metrics: LayoutMetrics) -> None:
    app.setStyleSheet(build_qss(metrics))
    font = base_font(metrics)
    app.setFont(font)
