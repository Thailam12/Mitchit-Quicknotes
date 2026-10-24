"""Hardware-accelerated note canvas: Pillow bitmap under a QPainter viewport.

The canvas never rasterises on its own. It renders whatever bitmap the session
published, draws selection chrome with QPainter, and emits a commit signal when
a drag or resize ends — the single moment the document is re-rasterised.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
    QWheelEvent,
)
from PyQt6.QtWidgets import QWidget

from ..core.constants import PALETTE
from ..core.geometry import clamp, clamp_position, clamp_rect, fit_scale, union
from ..core.model import Layer, TextLayer
from ..core.renderer import layer_box
from ..session import StudioSession
from .glass import GlassPanel, native_blur_available
from .inspector import LayerInspector

HANDLE = 10
PAD = 20

#: Arrow-key nudge ladder for the whole mass set (Shift multiplies the step).
NUDGE_STEP = 1.0
NUDGE_STEP_FAST = 10.0


def pil_to_qimage(image) -> QImage:
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


def pil_to_pixmap(image) -> QPixmap:
    return QPixmap.fromImage(pil_to_qimage(image))


class NoteCanvas(QWidget):
    sigSelection = pyqtSignal(str)
    sigCommitted = pyqtSignal(str)
    sigStatus = pyqtSignal(str)
    sigActivate = pyqtSignal(str)
    sigZoom = pyqtSignal(float)

    def __init__(self, session: StudioSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.setMinimumSize(240, 150)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._pixmap: QPixmap | None = None
        self._box_cache: dict[str, tuple[float, float, float, float]] = {}
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._mode: str | None = None
        self._press_widget = QPointF()
        self._press_doc = QPointF()
        self._origin = QPointF()
        self._origin_size = (0.0, 0.0)
        self._preview = QPointF(0.0, 0.0)
        self._preview_size = (0.0, 0.0)
        self._hover_layer: str | None = None
        # Mass drag state: every node of the multi-selection is moved rigidly,
        # so the group keeps its internal geometry while the union box is
        # funnelled through the containment clamp.
        self._drag_ids: list[str] = []
        self._drag_origins: dict[str, tuple[float, float]] = {}
        self._drag_union = (0.0, 0.0, 0.0, 0.0)

    # -- bitmap publication ------------------------------------------------ #
    def refresh_bitmap(self) -> None:
        image = self.session.active_image()
        if image is None:
            self._pixmap = None
            self._box_cache = {}
        else:
            self._pixmap = pil_to_pixmap(image)
            report = self.session.raster.report
            self._box_cache = dict(report.layer_boxes) if report else {}
        self.update()

    def invalidate_boxes(self) -> None:
        self._box_cache = {layer.id: layer_box(layer) for layer in self.session.document.layers}

    # -- geometry ---------------------------------------------------------- #
    def current_scale(self) -> float:
        fit = fit_scale(
            self.session.document.width,
            self.session.document.height,
            max(1, self.width()),
            max(1, self.height()),
            PAD,
        )
        return max(0.02, fit * self._zoom)

    def page_rect(self) -> QRectF:
        scale = self.current_scale()
        doc = self.session.document
        width = doc.width * scale
        height = doc.height * scale
        x = (self.width() - width) / 2.0 + self._pan.x()
        y = (self.height() - height) / 2.0 + self._pan.y()
        return QRectF(x, y, width, height)

    def _clamp_pan(self) -> None:
        scale = self.current_scale()
        doc = self.session.document
        width = doc.width * scale
        height = doc.height * scale
        overflow_x = max(0.0, (width - (self.width() - PAD * 2)) / 2.0)
        overflow_y = max(0.0, (height - (self.height() - PAD * 2)) / 2.0)
        self._pan = QPointF(
            clamp(self._pan.x(), -overflow_x, overflow_x),
            clamp(self._pan.y(), -overflow_y, overflow_y),
        )

    def to_widget(self, x: float, y: float) -> QPointF:
        rect = self.page_rect()
        scale = self.current_scale()
        return QPointF(rect.x() + x * scale, rect.y() + y * scale)

    def to_doc(self, point: QPointF) -> QPointF:
        rect = self.page_rect()
        scale = self.current_scale()
        return QPointF((point.x() - rect.x()) / scale, (point.y() - rect.y()) / scale)

    def layer_box(self, layer: Layer) -> tuple[float, float, float, float]:
        cached = self._box_cache.get(layer.id)
        if cached is not None:
            return cached
        return layer_box(layer)

    def layer_widget_rect(self, layer: Layer) -> QRectF:
        x, y, w, h = self.layer_box(layer)
        if w < 0:
            x, w = x + w, -w
        if h < 0:
            y, h = y + h, -h
        top_left = self.to_widget(x, y)
        scale = self.current_scale()
        return QRectF(top_left.x(), top_left.y(), max(1.0, w * scale), max(1.0, h * scale))

    # -- zoom -------------------------------------------------------------- #
    def set_zoom(self, value: float) -> None:
        self._zoom = clamp(value, 0.5, 2.0)
        self._clamp_pan()
        self.sigZoom.emit(self._zoom)
        self.update()

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom + 0.1)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom - 0.1)

    def zoom_fit(self) -> None:
        self._pan = QPointF(0.0, 0.0)
        self.set_zoom(1.0)

    @property
    def zoom(self) -> float:
        return self._zoom

    # -- hit testing ------------------------------------------------------- #
    def hit_test(self, doc_point: QPointF) -> Layer | None:
        layers = self.session.document.layers
        for layer in reversed(layers):
            if not layer.visible:
                continue
            x, y, w, h = self.layer_box(layer)
            if w < 0:
                x, w = x + w, -w
            if h < 0:
                y, h = y + h, -h
            if x - 2 <= doc_point.x() <= x + w + 2 and y - 2 <= doc_point.y() <= y + h + 2:
                return layer
        return None

    def handle_rect(self, layer: Layer) -> QRectF:
        rect = self.layer_widget_rect(layer)
        return QRectF(rect.right() - HANDLE / 2, rect.bottom() - HANDLE / 2, HANDLE, HANDLE)

    # -- painting ---------------------------------------------------------- #
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_stage(painter)
        self._paint_page(painter)
        self._paint_selection(painter)
        self._paint_ocr(painter)
        self._paint_chrome(painter)
        painter.end()

    def _paint_stage(self, painter: QPainter) -> None:
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QColor("#080D13"))
        gradient.setColorAt(1.0, QColor("#04070A"))
        painter.fillRect(self.rect(), gradient)

        glow = QRadialGradient(self.width() * 0.5, self.height() * 0.15, self.height() * 1.15)
        glow.setColorAt(0.0, QColor(78, 161, 255, 26))
        glow.setColorAt(1.0, QColor(78, 161, 255, 0))
        painter.fillRect(self.rect(), glow)

        if self.session.show_grid:
            painter.setPen(QPen(QColor(255, 255, 255, 12), 1))
            step = 24
            for y in range(step, self.height(), step):
                painter.drawLine(0, y, self.width(), y)

    def _paint_page(self, painter: QPainter) -> None:
        rect = self.page_rect()
        for spread, alpha in ((10, 12), (6, 22), (3, 34)):
            shadow = rect.adjusted(-spread, -spread + 2, spread, spread + 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(shadow, 6 + spread * 0.4, 6 + spread * 0.4)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self._pixmap is None:
            painter.fillRect(rect, QColor(PALETTE["panel"]))
            painter.setPen(QColor(PALETTE["text_faint"]))
            font = QFont(self.font())
            font.setPixelSize(12)
            painter.setFont(font)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "RASTER PENDING")
        else:
            painter.drawPixmap(rect, self._pixmap, QRectF(self._pixmap.rect()))

        painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def _paint_selection(self, painter: QPainter) -> None:
        nodes = self.session.selection_layers
        if not nodes:
            return
        scale = self.current_scale()
        translate = self._preview if self._mode == "move" else QPointF(0.0, 0.0)
        frames: list[QRectF] = []
        for layer in nodes:
            rect = self.layer_widget_rect(layer)
            rect.translate(translate.x() * scale, translate.y() * scale)
            frames.append(rect)
            self._paint_node_frame(painter, rect, layer.id == self.session.selected_id)

        if self._mode == "resize":
            self._paint_resize_ghost(painter)

        if len(nodes) > 1:
            group = union([(r.x(), r.y(), r.width(), r.height()) for r in frames])
            if group is not None:
                outline = QRectF(group[0], group[1], group[2], group[3]).adjusted(-6, -6, 6, 6)
                painter.setPen(QPen(QColor(111, 231, 193, 150), 1.1, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(outline)
                self._paint_tag(
                    painter,
                    outline,
                    f"{len(nodes)} SELECTED · MASS MOVE",
                    QColor(PALETTE["mint"]),
                )
            return

        if frames:
            self._paint_tag(painter, frames[0], f"{nodes[0].name} · {nodes[0].kind}", QColor(PALETTE["accent"]))

    def _paint_node_frame(self, painter: QPainter, rect: QRectF, anchor: bool) -> None:
        accent = QColor(PALETTE["accent"]) if anchor else QColor(PALETTE["mint"])
        painter.setPen(QPen(accent, 1.4))
        painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 20))
        painter.drawRect(rect)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        tick = 8.0 if anchor else 5.0
        painter.setPen(QPen(accent, 2.0 if anchor else 1.5))
        for corner_x, corner_y, dx, dy in (
            (rect.left(), rect.top(), 1, 1),
            (rect.right(), rect.top(), -1, 1),
            (rect.left(), rect.bottom(), 1, -1),
            (rect.right(), rect.bottom(), -1, -1),
        ):
            painter.drawLine(QPointF(corner_x, corner_y), QPointF(corner_x + tick * dx, corner_y))
            painter.drawLine(QPointF(corner_x, corner_y), QPointF(corner_x, corner_y + tick * dy))

        if anchor and len(self.session.selection) == 1:
            handle = QRectF(rect.right() - HANDLE / 2, rect.bottom() - HANDLE / 2, HANDLE, HANDLE)
            painter.setBrush(accent)
            painter.setPen(QPen(QColor("#041018"), 1.2))
            painter.drawRect(handle)
            painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_resize_ghost(self, painter: QPainter) -> None:
        layer = self.session.selected_layer
        if layer is None:
            return
        rect = self.layer_widget_rect(layer)
        scale = self.current_scale()
        rect.setWidth(max(12.0, rect.width() + self._preview_size[0] * scale))
        rect.setHeight(max(12.0, rect.height() + self._preview_size[1] * scale))
        painter.setPen(QPen(QColor(111, 231, 193, 170), 1.2, Qt.PenStyle.DashLine))
        painter.setBrush(QColor(111, 231, 193, 26))
        painter.drawRect(rect)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_tag(self, painter: QPainter, rect: QRectF, label: str, color: QColor) -> None:
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(label) + 14
        tag = QRectF(rect.left(), max(2.0, rect.top() - 20), width, 17)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(tag, 5, 5)
        painter.setPen(QColor("#04121C"))
        painter.drawText(
            tag.adjusted(7, 0, -5, 0),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            label,
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_ocr(self, painter: QPainter) -> None:
        result = self.session.last_ocr
        if not self.session.show_ocr_boxes or result is None:
            return
        scale = self.current_scale()
        rect = self.page_rect()
        painter.setPen(QPen(QColor(255, 182, 72, 190), 1.1, Qt.PenStyle.DashLine))
        painter.setBrush(QColor(255, 182, 72, 18))
        font = QFont(self.font())
        font.setPixelSize(9)
        painter.setFont(font)
        for line in result.lines[:120]:
            x, y, w, h = line.box
            box = QRectF(rect.x() + x * scale, rect.y() + y * scale, max(6.0, w * scale), max(8.0, h * scale))
            painter.drawRect(box)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_chrome(self, painter: QPainter) -> None:
        doc = self.session.document
        font = QFont(self.font())
        font.setPixelSize(10)
        painter.setFont(font)
        painter.setPen(QColor(PALETTE["text_faint"]))
        status = (
            f"{doc.width} × {doc.height} · {self.current_scale():.2f}× · "
            f"{doc.node_count()} NODES · RASTER {self.session.raster.elapsed_ms:.1f} ms"
        )
        painter.drawText(
            QRect(12, self.height() - 20, self.width() - 24, 16),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            status,
        )
        if doc.node_count() == 0:
            painter.setPen(QColor(PALETTE["text_dim"]))
            font.setPixelSize(13)
            painter.setFont(font)
            painter.drawText(
                self.page_rect(),
                int(Qt.AlignmentFlag.AlignCenter),
                "Empty canvas — insert text, shapes or an image",
            )

    # -- interaction ------------------------------------------------------- #
    def _begin_mass_drag(self) -> None:
        """Freeze the mass set so the group can be dragged as one rigid body."""
        document = self.session.document
        self._drag_ids = [layer.id for layer in self.session.selection_layers]
        self._drag_origins = {}
        boxes: list[tuple[float, float, float, float]] = []
        for layer in document.layers:
            if layer.id not in self._drag_ids:
                continue
            self._drag_origins[layer.id] = (
                float(getattr(layer, "x", 0.0)),
                float(getattr(layer, "y", 0.0)),
            )
            _, _, w, h = self.layer_box(layer)
            boxes.append((self._drag_origins[layer.id][0], self._drag_origins[layer.id][1], w, h))
        self._drag_union = union(boxes) or (0.0, 0.0, 0.0, 0.0)

    def _clamped_group_delta(self, delta: QPointF) -> QPointF:
        """Containment clamp for the drag: one node, or the whole mass set."""
        document = self.session.document
        if len(self._drag_ids) != 1:
            if not self._drag_ids:
                return QPointF(0.0, 0.0)
            ux, uy, uw, uh = self._drag_union
            x, y, _, _ = clamp_rect(ux + delta.x(), uy + delta.y(), uw, uh, document.width, document.height)
            return QPointF(x - ux, y - uy)
        layer = document.find(self._drag_ids[0])
        if layer is None:
            return QPointF(0.0, 0.0)
        origin_x, origin_y = self._drag_origins.get(layer.id, (0.0, 0.0))
        raw_x = origin_x + delta.x()
        raw_y = origin_y + delta.y()
        width = float(getattr(layer, "w", 1.0))
        if isinstance(layer, TextLayer):
            clamped_x = clamp(raw_x, -width + 24, document.width - 24)
            clamped_y = clamp(raw_y, -20, document.height - 20)
        else:
            height = float(getattr(layer, "h", 1.0))
            clamped_x, clamped_y, _, _ = clamp_rect(raw_x, raw_y, width, height, document.width, document.height)
        return QPointF(clamped_x - origin_x, clamped_y - origin_y)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        point = event.position()
        doc_point = self.to_doc(point)
        additive = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        selected = self.session.selected_layer
        mass_set = len(self.session.selection)

        if (
            selected is not None
            and mass_set <= 1
            and self.handle_rect(selected).adjusted(-3, -3, 3, 3).contains(point)
        ):
            self._mode = "resize"
            self._press_widget = point
            self._press_doc = doc_point
            self._origin = QPointF(getattr(selected, "x", 0.0), getattr(selected, "y", 0.0))
            self._origin_size = (getattr(selected, "w", 1.0), getattr(selected, "h", 1.0))
            self._preview = QPointF(0.0, 0.0)
            self._preview_size = (0.0, 0.0)
            self._drag_ids = []
            self._drag_origins = {}
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            return

        layer = self.hit_test(doc_point)
        if layer is not None:
            if additive:
                # Shift-click grows or shrinks the mass set without dropping the
                # anchor, which is what the mass parameter edits operate on.
                self.session.toggle_selection(layer.id)
            elif layer.id not in self.session.selection:
                self.session.select(layer.id)
            self.sigSelection.emit(self.session.selected_id or "")
            self._mode = "move"
            self._press_widget = point
            self._press_doc = doc_point
            self._preview = QPointF(0.0, 0.0)
            self._preview_size = (0.0, 0.0)
            self._begin_mass_drag()
            anchor = self.session.selected_layer
            self._origin = QPointF(getattr(anchor, "x", 0.0), getattr(anchor, "y", 0.0)) if anchor else QPointF()
            self._origin_size = (
                (getattr(anchor, "w", 1.0), getattr(anchor, "h", 1.0)) if anchor else (1.0, 1.0)
            )
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
            return

        if not additive:
            self.session.select(None)
            self.sigSelection.emit("")
        self._mode = "pan"
        self._press_widget = point
        self._pan_origin = QPointF(self._pan)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        point = event.position()
        if self._mode is None:
            layer = self.hit_test(self.to_doc(point))
            hover_id = layer.id if layer else None
            if hover_id != self._hover_layer:
                self._hover_layer = hover_id
                self.setCursor(
                    Qt.CursorShape.OpenHandCursor if layer is not None else Qt.CursorShape.ArrowCursor
                )
                self.update()
            return

        if self._mode == "pan":
            delta = point - self._press_widget
            self._pan = self._pan_origin + delta
            self._clamp_pan()
            self.update()
            return

        doc_point = self.to_doc(point)
        if self._mode == "move":
            if not self._drag_ids:
                return
            raw = QPointF(doc_point.x() - self._press_doc.x(), doc_point.y() - self._press_doc.y())
            self._preview = self._clamped_group_delta(raw)
            label = (
                f"mass move · {len(self._drag_ids)} nodes · "
                f"Δ {self._preview.x():+.0f}, {self._preview.y():+.0f}"
                if len(self._drag_ids) > 1
                else (
                    f"move · x {self._origin.x() + self._preview.x():.0f} · "
                    f"y {self._origin.y() + self._preview.y():.0f}"
                )
            )
            self.sigStatus.emit(label)
            self.update()
            return

        if self._mode == "resize":
            layer = self.session.selected_layer
            if layer is None:
                return
            delta_x = doc_point.x() - self._press_doc.x()
            delta_y = doc_point.y() - self._press_doc.y()
            if isinstance(layer, TextLayer):
                self._preview_size = (max(48.0 - self._origin_size[0], delta_x), 0.0)
            else:
                self._preview_size = (
                    max(16.0 - self._origin_size[0], delta_x),
                    max(16.0 - self._origin_size[1], delta_y),
                )
            self.sigStatus.emit(
                f"size · {self._origin_size[0] + self._preview_size[0]:.0f} × "
                f"{self._origin_size[1] + self._preview_size[1]:.0f}"
            )
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        layer = self.session.selected_layer
        mode = self._mode
        dragged = list(self._drag_ids)
        self._mode = None
        self._drag_ids = []
        self._drag_origins = {}
        self.setCursor(
            Qt.CursorShape.OpenHandCursor if self.session.selection else Qt.CursorShape.ArrowCursor
        )

        if mode == "move" and dragged:
            offset_x, offset_y = self._preview.x(), self._preview.y()
            self._preview = QPointF(0.0, 0.0)
            self._preview_size = (0.0, 0.0)
            if abs(offset_x) > 0.4 or abs(offset_y) > 0.4:
                moved = self.session.move_selection(offset_x, offset_y)
                if moved:
                    self.invalidate_boxes()
                    self.sigCommitted.emit("mass-move" if moved > 1 else "move")
            self.update()
            return

        if layer is not None and mode == "resize":
            new_w = self._origin_size[0] + self._preview_size[0]
            new_h = self._origin_size[1] + self._preview_size[1]
            changed = False
            if not isinstance(layer, TextLayer):
                setattr(layer, "h", max(16.0, new_h))
                changed = True
            if abs(new_w - self._origin_size[0]) > 0.4:
                setattr(layer, "w", max(48.0, new_w))
                changed = True
            if changed:
                self.session.commit_geometry(layer)
                self.invalidate_boxes()
                self.sigCommitted.emit("resize")

        self._preview = QPointF(0.0, 0.0)
        self._preview_size = (0.0, 0.0)
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseDoubleClickEvent(event)
        layer = self.hit_test(self.to_doc(event.position()))
        if layer is not None:
            self.sigActivate.emit(layer.id)

    def wheelEvent(self, event: QWheelEvent) -> None:
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            self.set_zoom(self._zoom + (0.1 if delta > 0 else -0.1))
            event.accept()
            return
        if self.current_scale() * self.session.document.width > self.width() - PAD * 2:
            delta = event.angleDelta().y() or event.angleDelta().x()
            self._pan = QPointF(self._pan.x() + delta * 0.65, self._pan.y())
            self._clamp_pan()
            self.update()
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down) and self.session.selection:
            if event.isAutoRepeat():
                event.accept()
                return
            step = NUDGE_STEP_FAST if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else NUDGE_STEP
            delta_x = -step if key == Qt.Key.Key_Left else step if key == Qt.Key.Key_Right else 0.0
            delta_y = -step if key == Qt.Key.Key_Up else step if key == Qt.Key.Key_Down else 0.0
            moved = self.session.move_selection(delta_x, delta_y)
            if moved:
                self.invalidate_boxes()
                self.sigCommitted.emit("nudge-all" if moved > 1 else "nudge")
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self.session.selection:
            self.session.clear_selection()
            self.sigSelection.emit("")
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._clamp_pan()

    def sizeHint(self) -> QSize:
        return QSize(720, 340)


class CanvasHost(QWidget):
    """Canvas viewport plus floating glass inspector overlay."""

    def __init__(self, session: StudioSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self._backend = "CXX_QT_GRAPHICSBLUR" if native_blur_available() else "PILLOW_GAUSSIAN_BLUR"
        self.canvas = NoteCanvas(session, self)
        self.canvas.setGeometry(self.rect())

        self.inspector = LayerInspector(
            session,
            self,
            backend=self._backend,
            backdrop_provider=self._canvas_backdrop,
        )
        self.inspector.setFixedWidth(272)
        self.inspector.hide()

        self.canvas.sigSelection.connect(self._on_selection)
        self.inspector.sigChanged.connect(self._on_inspector_change)
        self.inspector.sigAction.connect(self._on_inspector_action)

    def _on_selection(self, layer_id: str) -> None:
        self.inspector.bind(layer_id)
        if layer_id:
            self.show_inspector()
        else:
            self.hide_inspector()
        self.refresh_inspector_backdrop()

    def _on_inspector_change(self, reason: str) -> None:
        self.session.commit_properties("commit")
        self.canvas.invalidate_boxes()
        self.canvas.refresh_bitmap()

    def _on_inspector_action(self, action: str) -> None:
        if action == "delete":
            self.session.delete_selected()
        elif action == "duplicate":
            self.session.duplicate_selected()
        elif action == "raise":
            self.session.raise_selected()
        elif action == "lower":
            self.session.lower_selected()
        self.canvas.invalidate_boxes()
        self.canvas.refresh_bitmap()
        self.inspector.bind(self.session.selected_id or "")

    def _canvas_backdrop(self) -> QPixmap:
        pixmap = self.canvas.grab()
        geometry = self.inspector.geometry()
        region = QRect(geometry.x(), geometry.y(), max(1, geometry.width()), max(1, geometry.height()))
        cropped = pixmap.copy(region)
        return cropped if not cropped.isNull() else pixmap

    def show_inspector(self) -> None:
        self.inspector.bind(self.session.selected_id or "")
        self.inspector.show()
        self.inspector.raise_()
        self.position_inspector()
        self.refresh_inspector_backdrop()

    def hide_inspector(self) -> None:
        self.inspector.hide()

    def toggle_inspector(self) -> bool:
        if self.inspector.isVisible():
            self.hide_inspector()
            return False
        self.show_inspector()
        return True

    def position_inspector(self) -> None:
        height = min(420, max(240, self.height() - 24))
        self.inspector.setFixedHeight(height)
        x, y = clamp_position(
            self.width() - self.inspector.width() - 14,
            14,
            self.inspector.width(),
            height,
            max(1, self.width()),
            max(1, self.height()),
        )
        self.inspector.move(x, y)

    def refresh_inspector_backdrop(self) -> None:
        if self.inspector.isVisible():
            self.inspector.refresh_backdrop()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.canvas.setGeometry(self.rect())
        self.position_inspector()
        self.refresh_inspector_backdrop()

    def backend_label(self) -> str:
        return self._backend
