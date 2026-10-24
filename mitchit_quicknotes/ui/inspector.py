"""Floating glass layer inspector.

Edits are applied to the model on discrete commit events only — focus loss,
combo activation, checkbox toggle, slider release, colour acceptance — so typing
never triggers a raster pass.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import PALETTE
from ..core.fonts import known_families
from ..core.geometry import clamp, clamp_position
from ..core.model import ImageLayer, Layer, ShapeLayer, TextLayer
from ..session import StudioSession
from .glass import GlassPanel
from .widgets import Chip, IconButton


class ColorField(QAbstractButton):
    """Swatch button that opens the native colour dialog with alpha support."""

    colorCommitted = pyqtSignal(str)

    def __init__(self, color: str = "#4EA1FF", allow_clear: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self._allow_clear = allow_clear
        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Click to pick · right click to clear to transparent")

    def color(self) -> str:
        return self._color

    def set_color(self, color: str, emit: bool = True) -> None:
        self._color = color
        self.update()
        if emit:
            self.colorCommitted.emit(color)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton and self._allow_clear:
            self.set_color("#ffffff00")
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        initial = QColor(self._color)
        dialog = QColorDialog(initial, self)
        dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, True)
        dialog.setWindowTitle("Layer colour")
        if dialog.exec():
            selected = dialog.currentColor()
            if selected.isValid():
                name = selected.name(QColor.NameFormat.HexArgb)
                literal = f"#{name[3:9]}{name[1:3]}".lower()
                self.set_color(literal)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(PALETTE["line_hi"]), 1))
        painter.setBrush(QColor(PALETTE["panel_alt"]))
        painter.drawRoundedRect(rect, 6, 6)

        swatch = QRectF(rect.left() + 3, rect.top() + 3, 22, rect.height() - 6)
        color = QColor(self._color)
        if color.alpha() < 255:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 26))
            painter.drawRoundedRect(swatch, 4, 4)
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 120))
        else:
            painter.setBrush(color)
        painter.drawRoundedRect(swatch, 4, 4)
        painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(swatch, 4, 4)

        font = painter.font()
        font.setPixelSize(9)
        painter.setFont(font)
        painter.setPen(QColor(PALETTE["text_dim"]))
        painter.drawText(
            QRectF(swatch.right() + 7, rect.top(), rect.width() - 34, rect.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self._color.upper(),
        )
        painter.end()


class TextCommitEdit(QPlainTextEdit):
    committed = pyqtSignal(str)

    def focusOutEvent(self, event) -> None:
        self.committed.emit(self.toPlainText())
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.committed.emit(self.toPlainText())
            event.accept()
            return
        super().keyPressEvent(event)


class LayerInspector(GlassPanel):
    sigChanged = pyqtSignal(str)
    sigAction = pyqtSignal(str)

    def __init__(
        self,
        session: StudioSession,
        parent: QWidget | None = None,
        backend: str = "CXX_QT_GRAPHICSBLUR",
        backdrop_provider=None,
    ) -> None:
        super().__init__(
            parent,
            radius=14,
            tint_alpha=186,
            border_color="#2C4054",
            blur_radius=24.0,
            backdrop_provider=backdrop_provider,
            backend=backend,
        )
        self.session = session
        self._loading = False
        self._layer_id: str | None = None
        self._dragging = False
        self._drag_origin = QPoint()
        self._drag_start = QPoint()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        self._header = QWidget(self)
        header = QHBoxLayout(self._header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        title = QLabel("LAYER")
        title.setProperty("role", "section")
        header.addWidget(title)
        self._kind_chip = Chip("TEXT", "accent")
        header.addWidget(self._kind_chip)
        header.addStretch(1)
        close = IconButton("close", "Close inspector", PALETTE["text_faint"], 20)
        close.clicked.connect(self.hide)
        header.addWidget(close)
        root.addWidget(self._header)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.setStyleSheet("background: transparent;")
        root.addWidget(self._scroll, 1)

        body = QWidget()
        body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 0, 2, 0)
        self._body.setSpacing(5)
        self._scroll.setWidget(body)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Layer name")
        self._name.setFixedHeight(24)
        self._name.editingFinished.connect(self._commit_name)
        self._body.addWidget(self._name)

        self._text = TextCommitEdit()
        self._text.setFixedHeight(56)
        self._text.setPlaceholderText("Note text")
        self._text.committed.connect(self._commit_text)
        self._body.addWidget(self._text)

        self._family = QComboBox()
        self._family.setFixedHeight(24)
        self._family.addItems(list(known_families()))
        self._family.setMaxVisibleItems(18)
        self._family.activated.connect(self._commit_font)

        self._size = QSpinBox()
        self._size.setRange(6, 240)
        self._size.setFixedWidth(58)
        self._size.setFixedHeight(24)
        self._size.editingFinished.connect(self._commit_font)
        self._font_row = self._row("FONT", self._family, self._size)

        self._bold = QCheckBox("B")
        self._bold.setFixedWidth(28)
        self._bold.toggled.connect(self._commit_style)
        self._italic = QCheckBox("I")
        self._italic.setFixedWidth(28)
        self._italic.toggled.connect(self._commit_style)
        self._align = QComboBox()
        self._align.setFixedHeight(24)
        self._align.addItems(["left", "center", "right"])
        self._align.activated.connect(self._commit_style)
        self._style_row = self._row("STYLE", self._bold, self._italic, self._align)

        self._fill = ColorField("#10161D")
        self._fill.colorCommitted.connect(self._commit_fill)
        self._color_row = self._row("COLOUR", self._fill)

        self._w = QSpinBox()
        self._w.setRange(4, 4000)
        self._w.setFixedHeight(24)
        self._w.editingFinished.connect(self._commit_geometry)
        self._h = QSpinBox()
        self._h.setRange(4, 4000)
        self._h.setFixedHeight(24)
        self._h.editingFinished.connect(self._commit_geometry)
        self._size_row = self._row("SIZE", self._w, self._h)

        self._stroke = ColorField("#4EA1FF")
        self._stroke.colorCommitted.connect(self._commit_stroke)
        self._stroke_row = self._row("STROKE", self._stroke)

        self._shape_fill = ColorField("#ffffff00", allow_clear=True)
        self._shape_fill.colorCommitted.connect(self._commit_shape_fill)
        self._shape_fill_row = self._row("FILL", self._shape_fill)

        self._stroke_width = QSpinBox()
        self._stroke_width.setRange(0, 64)
        self._stroke_width.setFixedHeight(24)
        self._stroke_width.editingFinished.connect(self._commit_stroke_width)
        self._radius = QSpinBox()
        self._radius.setRange(0, 200)
        self._radius.setFixedHeight(24)
        self._radius.editingFinished.connect(self._commit_radius)
        self._edge_row = self._row("EDGE", self._stroke_width, self._radius)

        self._fit = QComboBox()
        self._fit.setFixedHeight(24)
        self._fit.addItems(["contain", "cover", "stretch"])
        self._fit.activated.connect(self._commit_fit)
        self._fit_row = self._row("FIT", self._fit)

        self._border = ColorField("#DCE6F2", allow_clear=True)
        self._border.colorCommitted.connect(self._commit_border)
        self._border_row = self._row("BORDER", self._border)

        self._opacity = QSlider(Qt.Orientation.Horizontal)
        self._opacity.setRange(0, 100)
        self._opacity.setFixedHeight(20)
        self._opacity.valueChanged.connect(self._on_opacity_preview)
        self._opacity.sliderReleased.connect(self._commit_opacity)
        self._opacity_label = QLabel("100%")
        self._opacity_label.setProperty("role", "faint")
        self._opacity_label.setFixedWidth(34)
        self._opacity_row = self._row("OPACITY", self._opacity, self._opacity_label)

        self._actions = QWidget()
        actions = QHBoxLayout(self._actions)
        actions.setContentsMargins(0, 2, 0, 0)
        actions.setSpacing(4)
        for kind, tip, action in (
            ("duplicate", "Duplicate node", "duplicate"),
            ("up", "Bring forward", "raise"),
            ("down", "Send backward", "lower"),
            ("trash", "Delete node", "delete"),
        ):
            button = IconButton(kind, tip, PALETTE["text_dim"], 24)
            button.clicked.connect(lambda _=False, name=action: self.sigAction.emit(name))
            actions.addWidget(button)
        actions.addStretch(1)
        self._body.addWidget(self._actions)

        self._empty = QLabel("Select a node on the canvas to inspect its vector attributes.")
        self._empty.setWordWrap(True)
        self._empty.setProperty("role", "faint")
        self._body.addWidget(self._empty)
        self._body.addStretch(1)

        font = self.font()
        font.setPixelSize(10)
        self.setFont(font)
        self._bind_visibility(None)

    # -- layout helpers ----------------------------------------------------- #
    def _row(self, label: str, *widgets: QWidget) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        caption = QLabel(label)
        caption.setProperty("role", "faint")
        caption.setFixedWidth(48)
        layout.addWidget(caption)
        for widget in widgets:
            stretch = 0 if isinstance(widget, (QSpinBox, QCheckBox)) else 1
            layout.addWidget(widget, stretch)
        self._body.addWidget(container)
        return container

    def _bind_visibility(self, layer: Layer | None) -> None:
        is_text = isinstance(layer, TextLayer)
        is_shape = isinstance(layer, ShapeLayer)
        is_image = isinstance(layer, ImageLayer)
        for widget in (self._text, self._font_row, self._style_row, self._color_row):
            widget.setVisible(is_text)
        for widget in (self._size_row, self._stroke_row, self._shape_fill_row, self._edge_row):
            widget.setVisible(is_shape)
        for widget in (self._fit_row, self._border_row):
            widget.setVisible(is_image)
        for widget in (self._name, self._opacity_row, self._actions):
            widget.setVisible(layer is not None)
        self._empty.setVisible(layer is None)

        # The chip doubles as the mass-set readout: after Ctrl+A it announces how
        # many nodes a single parameter edit is about to rewrite.
        mass = len(self.session.selection)
        suffix = f" ×{mass}" if mass > 1 else ""
        if is_shape:
            self._kind_chip.set_text(f"{str(getattr(layer, 'shape', 'shape')).upper()}{suffix}")
            self._kind_chip.set_tone("mint")
        elif is_image:
            self._kind_chip.set_text(f"IMAGE{suffix}")
            self._kind_chip.set_tone("amber")
        elif is_text:
            self._kind_chip.set_text(f"TEXT{suffix}")
            self._kind_chip.set_tone("accent")
        else:
            self._kind_chip.set_text("NONE")
            self._kind_chip.set_tone("neutral")

    # -- binding ------------------------------------------------------------ #
    def bind(self, layer_id: str) -> None:
        layer = self.session.document.find(layer_id)
        self._layer_id = layer.id if layer else None
        self._loading = True
        try:
            self._bind_visibility(layer)
            if layer is None:
                return
            self._name.setText(layer.name)
            if isinstance(layer, TextLayer):
                self._text.setPlainText(layer.text)
                index = self._family.findText(layer.family)
                if index >= 0:
                    self._family.setCurrentIndex(index)
                else:
                    self._family.addItem(layer.family)
                    self._family.setCurrentText(layer.family)
                self._size.setValue(int(layer.size))
                self._bold.setChecked(layer.bold)
                self._italic.setChecked(layer.italic)
                self._align.setCurrentText(layer.align)
                self._fill.set_color(layer.fill, emit=False)
            elif isinstance(layer, ShapeLayer):
                self._w.setValue(int(layer.w))
                self._h.setValue(int(layer.h))
                self._stroke.set_color(layer.stroke, emit=False)
                self._shape_fill.set_color(layer.fill, emit=False)
                self._stroke_width.setValue(int(layer.stroke_width))
                self._radius.setValue(int(layer.radius))
            elif isinstance(layer, ImageLayer):
                self._fit.setCurrentText(layer.fit)
                self._radius.setValue(int(layer.radius))
                self._border.set_color(layer.border, emit=False)
            self._opacity.setValue(int(round(layer.opacity * 100)))
            self._opacity_label.setText(f"{int(round(layer.opacity * 100))}%")
        finally:
            self._loading = False

    # -- commits ------------------------------------------------------------ #
    def _current(self) -> Layer | None:
        return self.session.document.find(self._layer_id)

    def _targets(self) -> list[Layer]:
        """Resolve the nodes a committed edit must be written to.

        With a mass set active (shift-click or ``Ctrl+A``) the edit is applied
        across every selected node, kind-filtered so an incompatible attribute
        is simply skipped. With one node it behaves exactly like a classic
        inspector. The kind of the anchored node decides which rows are visible,
        so the compatible subset is always what the operator can see.
        """
        anchor = self._current()
        if anchor is None:
            return []
        nodes = self.session.selection_layers
        if len(nodes) <= 1:
            return [anchor]
        return nodes

    def _commit_name(self) -> None:
        if self._loading:
            return
        name = self._name.text().strip()[:64]
        if not name:
            return
        changed = 0
        for layer in self._targets():
            if layer.name != name:
                layer.name = name
                changed += 1
        if changed:
            self.sigChanged.emit("name")

    def _commit_text(self, text: str) -> None:
        if self._loading:
            return
        body = text[:4000]
        changed = 0
        for layer in self._targets():
            if isinstance(layer, TextLayer) and layer.text != body:
                layer.text = body
                changed += 1
        if changed:
            self.sigChanged.emit("text")

    def _commit_font(self) -> None:
        if self._loading:
            return
        family = self._family.currentText()
        size = int(self._size.value())
        changed = 0
        for layer in self._targets():
            if not isinstance(layer, TextLayer):
                continue
            if layer.family != family or layer.size != size:
                layer.family = family
                layer.size = size
                changed += 1
        if changed:
            self.sigChanged.emit("font")

    def _commit_style(self) -> None:
        if self._loading:
            return
        bold = self._bold.isChecked()
        italic = self._italic.isChecked()
        align = self._align.currentText()
        changed = 0
        for layer in self._targets():
            if not isinstance(layer, TextLayer):
                continue
            if (layer.bold, layer.italic, layer.align) != (bold, italic, align):
                layer.bold = bold
                layer.italic = italic
                layer.align = align
                changed += 1
        if changed:
            self.sigChanged.emit("style")

    def _commit_fill(self, value: str) -> None:
        if self._loading:
            return
        changed = 0
        for layer in self._targets():
            if isinstance(layer, TextLayer) and layer.fill != value:
                layer.fill = value
                changed += 1
        if changed:
            self.sigChanged.emit("fill")

    def _commit_geometry(self) -> None:
        if self._loading:
            return
        width = float(self._w.value())
        height = float(self._h.value())
        changed = 0
        for layer in self._targets():
            if not isinstance(layer, ShapeLayer):
                continue
            if layer.w != width or layer.h != height:
                layer.w = width
                layer.h = height
                changed += 1
        if changed:
            self.sigChanged.emit("size")

    def _commit_stroke(self, value: str) -> None:
        if self._loading:
            return
        changed = 0
        for layer in self._targets():
            if isinstance(layer, ShapeLayer) and layer.stroke != value:
                layer.stroke = value
                changed += 1
        if changed:
            self.sigChanged.emit("stroke")

    def _commit_shape_fill(self, value: str) -> None:
        if self._loading:
            return
        changed = 0
        for layer in self._targets():
            if isinstance(layer, ShapeLayer) and layer.fill != value:
                layer.fill = value
                changed += 1
        if changed:
            self.sigChanged.emit("fill")

    def _commit_stroke_width(self) -> None:
        if self._loading:
            return
        width = int(self._stroke_width.value())
        changed = 0
        for layer in self._targets():
            if isinstance(layer, ShapeLayer) and layer.stroke_width != width:
                layer.stroke_width = width
                changed += 1
        if changed:
            self.sigChanged.emit("edge")

    def _commit_radius(self) -> None:
        if self._loading:
            return
        radius = int(self._radius.value())
        changed = 0
        for layer in self._targets():
            if isinstance(layer, (ShapeLayer, ImageLayer)) and layer.radius != radius:
                layer.radius = radius
                changed += 1
        if changed:
            self.sigChanged.emit("radius")

    def _commit_fit(self) -> None:
        if self._loading:
            return
        fit = self._fit.currentText()
        changed = 0
        for layer in self._targets():
            if isinstance(layer, ImageLayer) and layer.fit != fit:
                layer.fit = fit
                changed += 1
        if changed:
            self.sigChanged.emit("fit")

    def _commit_border(self, value: str) -> None:
        if self._loading:
            return
        changed = 0
        for layer in self._targets():
            if not isinstance(layer, ImageLayer) or layer.border == value:
                continue
            layer.border = value
            layer.border_width = 0 if value.endswith("00") else max(1, layer.border_width)
            changed += 1
        if changed:
            self.sigChanged.emit("border")

    def _on_opacity_preview(self, value: int) -> None:
        self._opacity_label.setText(f"{value}%")

    def _commit_opacity(self) -> None:
        if self._loading:
            return
        opacity = clamp(self._opacity.value() / 100.0, 0.0, 1.0)
        changed = 0
        for layer in self._targets():
            if abs(layer.opacity - opacity) > 0.001:
                layer.opacity = opacity
                changed += 1
        if changed:
            self.sigChanged.emit("opacity")

    # -- drag --------------------------------------------------------------- #
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._header.geometry().contains(event.position().toPoint()):
            self._dragging = True
            self._drag_start = self.pos()
            self._drag_origin = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and self.parent() is not None:
            delta = event.globalPosition().toPoint() - self._drag_origin
            target = self._drag_start + delta
            parent = self.parentWidget()
            x, y = clamp_position(
                target.x(),
                target.y(),
                self.width(),
                self.height(),
                max(1, parent.width()),
                max(1, parent.height()),
            )
            self.move(x, y)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self.refresh_backdrop()
            event.accept()
            return
        super().mouseReleaseEvent(event)
