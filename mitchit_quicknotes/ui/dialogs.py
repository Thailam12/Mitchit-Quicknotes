"""Modal sheets: text insertion, shape matrix, image ingestion, export composer."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QFont, QMouseEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import EXPORT_FORMATS, PALETTE, SCALE_CHOICES
from ..core.fonts import known_families
from ..core.geometry import clamp
from ..core.model import ImageLayer, NoteDocument, ShapeLayer, TextLayer, new_layer_id
from .icons import icon
from .inspector import ColorField
from .widgets import Chip


class SheetDialog(QDialog):
    """Frameless, draggable modal sheet with a branded header."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None, width: int = 500) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setFixedWidth(width)
        self.setStyleSheet(
            f"QDialog {{ background: {PALETTE['shell']}; border: 1px solid {PALETTE['line_hi']}; "
            f"border-radius: 12px; }}"
        )
        self._drag_origin: QPoint | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(1)
        self._title = QLabel(title.upper())
        font = self._title.font()
        font.setPixelSize(13)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        self._title.setFont(font)
        titles.addWidget(self._title)
        if subtitle:
            hint = QLabel(subtitle)
            hint.setProperty("role", "faint")
            titles.addWidget(hint)
        header.addLayout(titles)
        header.addStretch(1)
        self._header_chip = Chip("VECTOR", "accent")
        header.addWidget(self._header_chip)
        close = QPushButton()
        close.setIcon(icon("close", PALETTE["text_dim"], 12))
        close.setFixedSize(24, 24)
        close.setProperty("role", "ghost")
        close.clicked.connect(self.reject)
        header.addWidget(close)
        root.addLayout(header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(9)
        root.addLayout(self.body)

        self.footer = QHBoxLayout()
        self.footer.setContentsMargins(0, 0, 0, 0)
        self.footer.setSpacing(8)
        root.addLayout(self.footer)

        self._form = QFormLayout()
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(8)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(self._form)

    def form(self) -> QFormLayout:
        return self._form

    def add_row(self, label: str, widget: QWidget) -> QWidget:
        caption = QLabel(label.upper())
        caption.setProperty("role", "faint")
        self._form.addRow(caption, widget)
        return widget

    def add_pair(self, label: str, *widgets: QWidget) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for widget in widgets:
            layout.addWidget(widget, 1)
        return self.add_row(label, container)

    def add_footer(self, *widgets: QWidget, primary: QWidget | None = None) -> None:
        self.footer.addStretch(1)
        for widget in widgets:
            self.footer.addWidget(widget)
        if primary is not None:
            self.footer.addWidget(primary)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 54:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def center_on_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        anchor = parent.window().geometry()
        x = anchor.x() + (anchor.width() - self.width()) // 2
        y = anchor.y() + (anchor.height() - self.height()) // 3
        screen = parent.screen()
        if screen is not None:
            available = screen.availableGeometry()
            x = max(available.left() + 8, min(x, available.right() - self.width() - 8))
            y = max(available.top() + 8, min(y, available.bottom() - self.height() - 8))
        self.move(x, y)


def _spin(low: int, high: int, value: int, width: int = 74) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setValue(value)
    spin.setFixedWidth(width)
    return spin


class TextDialog(SheetDialog):
    def __init__(self, document: NoteDocument, layer: TextLayer | None = None, parent: QWidget | None = None) -> None:
        super().__init__(
            "Text node",
            "Font family, size and fill colour travel with the node.",
            parent,
            width=520,
        )
        self._document = document
        source = layer or TextLayer(
            id=new_layer_id("text"),
            name="Text",
            x=64,
            y=96,
            w=min(460, document.width - 128),
            text="",
            family="DejaVu Sans",
            size=24,
            fill="#10161D",
        )
        self._source = source

        self._text = QPlainTextEdit()
        self._text.setFixedHeight(92)
        self._text.setPlainText(source.text)
        self._text.setPlaceholderText("Write the note content…")
        self.body.insertWidget(0, self._text)

        self._family = QComboBox()
        self._family.addItems(list(known_families()))
        index = self._family.findText(source.family)
        self._family.setCurrentIndex(max(0, index))
        self._family.setMaxVisibleItems(18)
        self.add_row("family", self._family)

        self._size = _spin(6, 240, int(source.size))
        self._bold = QCheckBox("BOLD")
        self._bold.setChecked(source.bold)
        self._italic = QCheckBox("ITALIC")
        self._italic.setChecked(source.italic)
        self.add_pair("typography", self._size, self._bold, self._italic)

        self._align = QComboBox()
        self._align.addItems(["left", "center", "right"])
        self._align.setCurrentText(source.align)
        self._fill = ColorField(source.fill)
        self.add_pair("colour · align", self._fill, self._align)

        self._x = _spin(-2000, 4000, int(source.x))
        self._y = _spin(-2000, 4000, int(source.y))
        self._w = _spin(48, 4000, int(source.w))
        self.add_pair("x · y · width", self._x, self._y, self._w)

        self._preview = QLabel()
        self._preview.setMinimumHeight(52)
        self._preview.setWordWrap(True)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._preview.setStyleSheet(
            f"background: {PALETTE['paper']}; border-radius: 8px; padding: 8px; color: #10161D;"
        )
        self.body.addWidget(self._preview)

        cancel = QPushButton("CANCEL")
        cancel.setProperty("role", "ghost")
        cancel.clicked.connect(self.reject)
        apply_button = QPushButton("APPLY NODE")
        apply_button.setProperty("role", "primary")
        apply_button.setDefault(True)
        apply_button.clicked.connect(self.accept)
        self.add_footer(cancel, primary=apply_button)

        self._text.textChanged.connect(self._refresh_preview)
        self._family.currentTextChanged.connect(self._refresh_preview)
        self._size.valueChanged.connect(self._refresh_preview)
        self._bold.toggled.connect(self._refresh_preview)
        self._italic.toggled.connect(self._refresh_preview)
        self._fill.colorCommitted.connect(lambda _: self._refresh_preview())
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        family = self._family.currentText()
        size = int(clamp(self._size.value(), 6, 40))
        color = self._fill.color()
        weight = "800" if self._bold.isChecked() else "400"
        style = "italic" if self._italic.isChecked() else "normal"
        self._preview.setStyleSheet(
            f"background: {PALETTE['paper']}; border-radius: 8px; padding: 8px; color: {color}; "
            f"font-family: '{family}'; font-size: {size}px; font-weight: {weight}; font-style: {style};"
        )
        content = self._text.toPlainText().strip() or "Typography preview"
        self._preview.setText(content[:220])

    def result_layer(self) -> TextLayer:
        layer = self._source
        layer.text = self._text.toPlainText()[:4000]
        layer.family = self._family.currentText()
        layer.size = int(self._size.value())
        layer.bold = self._bold.isChecked()
        layer.italic = self._italic.isChecked()
        layer.align = self._align.currentText()
        layer.fill = self._fill.color()
        layer.x = float(self._x.value())
        layer.y = float(self._y.value())
        layer.w = float(self._w.value())
        if not layer.name or layer.name == "Text":
            layer.name = (layer.text.strip().splitlines() or ["Text"])[0][:28] or "Text"
        return layer


class ShapeDialog(SheetDialog):
    def __init__(self, document: NoteDocument, kind: str = "rect", parent: QWidget | None = None) -> None:
        super().__init__(
            "Shape node",
            "Precision matrix: dimension, stroke outline and background fill.",
            parent,
            width=500,
        )
        self._document = document
        defaults = {
            "rect": (220, 150, "#4EA1FF", "#EAF3FF"),
            "ellipse": (200, 200, "#6FE7C1", "#E7FBF5"),
            "line": (320, 0, "#4EA1FF", "#ffffff00"),
        }
        width, height, stroke, fill = defaults.get(kind, defaults["rect"])

        self._shape = QComboBox()
        self._shape.addItems(["rect", "ellipse", "line"])
        self._shape.setCurrentText(kind)
        self.add_row("geometry", self._shape)

        self._x = _spin(-2000, 4000, 120)
        self._y = _spin(-2000, 4000, 140)
        self._w = _spin(0, 4000, width)
        self._h = _spin(0, 4000, height)
        self.add_pair("x · y · w · h", self._x, self._y, self._w, self._h)

        self._stroke = ColorField(stroke)
        self._fill = ColorField(fill, allow_clear=True)
        self.add_pair("stroke · fill", self._stroke, self._fill)

        self._stroke_width = _spin(0, 64, 3)
        self._radius = _spin(0, 200, 16)
        self.add_pair("stroke w · radius", self._stroke_width, self._radius)

        cancel = QPushButton("CANCEL")
        cancel.setProperty("role", "ghost")
        cancel.clicked.connect(self.reject)
        apply_button = QPushButton("APPLY NODE")
        apply_button.setProperty("role", "primary")
        apply_button.setDefault(True)
        apply_button.clicked.connect(self.accept)
        self.add_footer(cancel, primary=apply_button)

    def result_layer(self) -> ShapeLayer:
        shape = self._shape.currentText()
        return ShapeLayer(
            id=new_layer_id("shape"),
            name=f"{shape.title()} node",
            shape=shape,
            x=float(self._x.value()),
            y=float(self._y.value()),
            w=float(self._w.value()),
            h=float(self._h.value()),
            stroke=self._stroke.color(),
            stroke_width=int(self._stroke_width.value()),
            fill=self._fill.color(),
            radius=int(self._radius.value()),
        )


class ImageDialog(SheetDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Image ingestion",
            "External rasters are streamed, resized and embedded as PNG payloads.",
            parent,
            width=540,
        )
        self._path = QLineEdit()
        self._path.setPlaceholderText("/path/to/image.png")
        browse = QPushButton("BROWSE")
        browse.setProperty("role", "ghost")
        browse.clicked.connect(self._pick_file)
        self.add_pair("source", self._path, browse)

        self._fit = QComboBox()
        self._fit.addItems(["contain", "cover", "stretch"])
        self._radius = _spin(0, 200, 12)
        self.add_pair("fit · radius", self._fit, self._radius)

        self._w = _spin(8, 4000, 320)
        self._h = _spin(8, 4000, 220)
        self.add_pair("target width · height", self._w, self._h)

        hint = QLabel(
            "Rasters are re-encoded to PNG, capped at 2400 px and carried as an image node; "
            "on save the whole tree rides inside the PNG text chunks."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "faint")
        self.body.addWidget(hint)

        cancel = QPushButton("CANCEL")
        cancel.setProperty("role", "ghost")
        cancel.clicked.connect(self.reject)
        ingest = QPushButton("INGEST NODE")
        ingest.setProperty("role", "primary")
        ingest.setDefault(True)
        ingest.clicked.connect(self.accept)
        self.add_footer(cancel, primary=ingest)

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select an image",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff)",
        )
        if path:
            self._path.setText(path)

    def selected_path(self) -> Path | None:
        text = self._path.text().strip()
        if not text:
            return None
        path = Path(text).expanduser()
        return path if path.exists() else None

    def configure(self, layer: ImageLayer) -> ImageLayer:
        layer.fit = self._fit.currentText()
        layer.radius = int(self._radius.value())
        layer.w = float(self._w.value())
        layer.h = float(self._h.value())
        return layer


class ExportDialog(SheetDialog):
    def __init__(self, document: NoteDocument, suggested: Path, parent: QWidget | None = None) -> None:
        super().__init__(
            "Khai hoả export",
            "Serialization is released only by this command block.",
            parent,
            width=560,
        )
        self._document = document
        self._header_chip.set_text("WRITE")

        self._format = QComboBox()
        for spec in EXPORT_FORMATS:
            self._format.addItem(f"{spec.label}", spec.key)
        self._format.addItem("ZIP · SRP distribution bundle", "zip")
        self._format.currentIndexChanged.connect(self._on_format_changed)
        self.add_row("format", self._format)

        self._scale = QComboBox()
        for label, value in SCALE_CHOICES:
            self._scale.addItem(label, value)
        self.add_row("scale", self._scale)

        self._path = QLineEdit(str(suggested))
        browse = QPushButton("…")
        browse.setFixedWidth(34)
        browse.setProperty("role", "ghost")
        browse.clicked.connect(self._pick_target)
        self.add_pair("destination", self._path, browse)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setProperty("role", "faint")
        self.body.addWidget(self._hint)

        self._dimensions = QLabel()
        self._dimensions.setProperty("role", "muted")
        self.body.addWidget(self._dimensions)

        cancel = QPushButton("CANCEL")
        cancel.setProperty("role", "ghost")
        cancel.clicked.connect(self.reject)
        run = QPushButton("KHAI HOẢ EXPORT")
        run.setProperty("role", "primary")
        run.setMinimumWidth(190)
        run.setDefault(True)
        run.clicked.connect(self.accept)
        self.add_footer(cancel, primary=run)

        self._on_format_changed()

    def _spec(self):
        key = self._format.currentData()
        for spec in EXPORT_FORMATS:
            if spec.key == key:
                return spec
        return None

    def _on_format_changed(self) -> None:
        spec = self._spec()
        key = self._format.currentData()
        if key == "zip":
            self._hint.setText(
                "Bundles state, raster, vector twin and every embedded layer around "
                "core/index.html with relative paths only."
            )
            self._scale.setEnabled(True)
            self._dimensions.setText(
                f"bundle · {self._document.node_count()} nodes · 0 VND runtime cost"
            )
            current = Path(self._path.text())
            self._path.setText(str(current.with_suffix(".zip")))
            return
        if spec is None:
            return
        self._hint.setText(spec.hint)
        self._scale.setEnabled(spec.supports_scale)
        scale = float(self._scale.currentData() or 1.0) if spec.supports_scale else 1.0
        width = int(self._document.width * scale)
        height = int(self._document.height * scale)
        if spec.key == "ico":
            self._dimensions.setText("256 × 256 · 16/24/32/48/64/128/256 px icon shell")
        elif spec.key == "svg":
            self._dimensions.setText(
                f"{self._document.width} × {self._document.height} viewBox · raw vector attributes"
            )
        else:
            self._dimensions.setText(f"{width} × {height} px · {spec.mode}")

    def _pick_target(self) -> None:
        key = self._format.currentData()
        filters = "Mitchit bundle (*.zip)" if key == "zip" else "Note image (*)"
        path, _ = QFileDialog.getSaveFileName(self, "Export destination", self._path.text(), filters)
        if path:
            self._path.setText(path)

    def selection(self) -> tuple[str, Path, float]:
        key = str(self._format.currentData())
        path = Path(self._path.text().strip() or "note")
        scale = float(self._scale.currentData() or 1.0) if self._scale.isEnabled() else 1.0
        return key, path, scale
