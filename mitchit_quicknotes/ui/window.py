"""Frameless 1280x720 studio window: header, dashboard regions, footer command bar."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QRegion,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_RELEASE, APP_VERSION, MILESTONE_TOKEN
from ..core import container
from ..core.constants import (
    DENSITY_DEFAULT,
    DENSITY_STEPS,
    EXPORT_DIR,
    NOTES_DIR,
    PACKAGE_DIR,
    PALETTE,
    PROJECT_ROOT,
    VERSION_LABEL,
    VIEWPORT_H,
    VIEWPORT_W,
)
from ..core.geometry import LayoutMetrics, clamp_position, clamp_scale, verify_viewport, zoom_percent
from ..core.milestones import MILESTONES
from ..core.model import ImageLayer, TextLayer, slugify
from ..core.renderer import rasterize_preview
from ..core.templates import TEMPLATES
from ..session import StudioSession
from .carousel import TemplateCarousel
from .dialogs import ExportDialog, ImageDialog, ShapeDialog, TextDialog
from .dock import ActionsDock
from .glass import NATIVE_BACKEND, PILLOW_BACKEND, native_blur_available
from .hud import TelemetryHUD
from .pixmap import pil_to_pixmap
from .recent import RecentGrid
from .theme import apply_theme, ui_font_family
from .widgets import Chip, DensityCombo, IconButton, SectionHeader

#: The five muscle keys of the interceptor contract. Each one is wired as a
#: window-level ``QShortcut`` and each one (except Ctrl+S) first checks whether a
#: text surface owns focus, so editing a note field never loses clipboard truth.
MUSCLE_KEYS = ("Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+S", "Ctrl+A")

#: Widgets that must keep native Qt editing semantics under the interceptor.
TEXT_SURFACES = (QLineEdit, QPlainTextEdit, QAbstractSpinBox)


class BrandMark(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 30)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 9, 9)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(PALETTE["accent"]))
        gradient.setColorAt(1.0, QColor(PALETTE["mint"]))
        painter.fillPath(path, gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        font = QFont(ui_font_family())
        font.setPixelSize(13)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#04121C"))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "MQ")
        painter.end()


class HeaderBar(QWidget):
    """Brand strip; doubles as the frameless window drag surface."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HeaderBar")
        self.setFixedHeight(48)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)


class FooterBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FooterBar")
        self.setFixedHeight(54)


class MainWindow(QMainWindow):
    def __init__(self, session: StudioSession, vault_report, milestones=None) -> None:
        super().__init__()
        self.session = session
        self.vault = vault_report
        self.milestones = milestones or MILESTONES
        self._metrics = LayoutMetrics(DENSITY_DEFAULT)
        self._backend = NATIVE_BACKEND if native_blur_available() else PILLOW_BACKEND
        self._audits: dict[str, bool] = {}
        self._banishment: container.BanReport | None = None
        self._ingestion: dict[str, object] = {}
        self._shown = False

        self.setWindowTitle(f"Mitchit Quicknotes Studio {VERSION_LABEL}")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setFixedSize(VIEWPORT_W, VIEWPORT_H)

        self._build_ui()
        self._wire_shortcuts()
        self._refresh_everything()
        self._center_on_screen()

    # ------------------------------------------------------------------ #
    # construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        root.setFixedSize(VIEWPORT_W, VIEWPORT_H)
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = HeaderBar(root)
        outer.addWidget(self._build_header())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(12, 8, 8, 8)
        body_layout.setSpacing(8)
        body_layout.addWidget(self._build_center(), 1)
        body_layout.addWidget(self._build_rail(), 0)
        outer.addWidget(body, 1)

        self._footer = FooterBar(root)
        outer.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        header = self._header
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(10)

        layout.addWidget(BrandMark(header))

        brand_box = QVBoxLayout()
        brand_box.setContentsMargins(0, 0, 0, 0)
        brand_box.setSpacing(0)
        brand = QLabel("MITCHIT QUICKNOTES")
        brand.setProperty("role", "brand")
        sub = QLabel("STUDIO · OPEN CONTAINER · ANTI-LOCK-IN")
        sub.setProperty("role", "faint")
        brand_box.addWidget(brand)
        brand_box.addWidget(sub)
        layout.addLayout(brand_box)

        version = Chip(f"v{APP_VERSION} {APP_RELEASE}", "accent")
        layout.addWidget(version, 0, Qt.AlignmentFlag.AlignVCenter)

        divider = QWidget()
        divider.setFixedSize(1, 24)
        divider.setStyleSheet(f"background: {PALETTE['line_hi']};")
        layout.addWidget(divider)
        layout.addStretch(1)

        self._doc_title = QLabel("UNTITLED NOTE")
        self._doc_title.setProperty("role", "title")
        layout.addWidget(self._doc_title)

        self._doc_chip = Chip("0 NODES", "mint")
        layout.addWidget(self._doc_chip)

        self._shield_button = QPushButton("SHIELD · EVALUATING")
        self._shield_button.setProperty("role", "ghost")
        self._shield_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shield_button.setFixedHeight(26)
        self._shield_button.clicked.connect(self._evaluate_shield)
        layout.addWidget(self._shield_button)

        geometry_chip = Chip(f"{VIEWPORT_W}×{VIEWPORT_H} · LOCKED", "violet")
        geometry_chip.setToolTip("Hard-locked client area, containment clamps active")
        layout.addWidget(geometry_chip)

        self._density = DensityCombo()
        self._density.setFixedHeight(26)
        self._density.setFixedWidth(84)
        for step in DENSITY_STEPS:
            self._density.addItem(f"{int(step * 100)}%", step)
        self._density.setCurrentIndex(DENSITY_STEPS.index(DENSITY_DEFAULT))
        self._density.setToolTip("Compact layout transformation (50%–200%)")
        self._density.currentIndexChanged.connect(self._on_density_changed)
        layout.addWidget(self._density)

        minimise = IconButton("minimise", "Minimise window", PALETTE["text_dim"], 26)
        minimise.clicked.connect(self.showMinimized)
        close = IconButton("close", "Close studio", PALETTE["danger"], 26)
        close.clicked.connect(self.close)
        layout.addWidget(minimise)
        layout.addWidget(close)
        return header

    def _build_center(self) -> QWidget:
        center = QWidget()
        layout = QVBoxLayout(center)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        entries = []
        for spec in TEMPLATES:
            document = spec.build()
            entries.append((spec, pil_to_pixmap(rasterize_preview(document, 340))))
        self._carousel = TemplateCarousel(entries)
        self._carousel.setFixedHeight(118)
        self._carousel.templateChosen.connect(self._apply_template)
        layout.addWidget(self._carousel)

        panel = QWidget()
        panel.setObjectName("Panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(8, 8, 8, 6)
        panel_layout.setSpacing(4)

        tool_row = QWidget()
        tools = QHBoxLayout(tool_row)
        tools.setContentsMargins(2, 0, 2, 0)
        tools.setSpacing(3)

        self._canvas_section = SectionHeader("Canvas", "pillow buffer · qpainter viewport")
        tools.addWidget(self._canvas_section)
        tools.addStretch(1)

        for kind, tip, handler in (
            ("text", "Insert text node", self._insert_text),
            ("rect", "Insert rectangle", lambda: self._insert_shape("rect")),
            ("ellipse", "Insert ellipse", lambda: self._insert_shape("ellipse")),
            ("line", "Insert line", lambda: self._insert_shape("line")),
            ("image", "Ingest image", self._insert_image),
        ):
            button = IconButton(kind, tip, PALETTE["text_dim"], 24)
            button.clicked.connect(handler)
            tools.addWidget(button)

        tools.addWidget(self._divider())
        for kind, tip, handler in (
            ("duplicate", "Duplicate selected node", self._duplicate),
            ("up", "Bring forward", self._raise),
            ("down", "Send backward", self._lower),
            ("trash", "Delete selected node", self._delete),
        ):
            button = IconButton(kind, tip, PALETTE["text_dim"], 24)
            button.clicked.connect(handler)
            tools.addWidget(button)

        tools.addWidget(self._divider())
        zoom_out = IconButton("minus", "Zoom out", PALETTE["text_dim"], 24)
        zoom_out.clicked.connect(lambda: self._host.canvas.zoom_out())
        self._zoom_label = QLabel("100%")
        self._zoom_label.setProperty("role", "mono")
        self._zoom_label.setFixedWidth(38)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom_in = IconButton("plus", "Zoom in", PALETTE["text_dim"], 24)
        zoom_in.clicked.connect(lambda: self._host.canvas.zoom_in())
        fit = IconButton("fit", "Fit to viewport", PALETTE["text_dim"], 24)
        fit.clicked.connect(self._zoom_fit)
        for widget in (zoom_out, self._zoom_label, zoom_in, fit):
            tools.addWidget(widget)

        tools.addWidget(self._divider())
        self._grid_button = IconButton("grid", "Toggle canvas guides", PALETTE["text_dim"], 24, checkable=True)
        self._grid_button.setChecked(True)
        self._grid_button.set_selected(True)
        self._grid_button.clicked.connect(self._toggle_grid)
        scan = IconButton("scan", "Run local auto-OCR", PALETTE["text_dim"], 24)
        scan.clicked.connect(self._run_ocr)
        self._inspector_button = IconButton("sliders", "Toggle layer inspector", PALETTE["text_dim"], 24, checkable=True)
        self._inspector_button.clicked.connect(self._toggle_inspector)
        for widget in (self._grid_button, scan, self._inspector_button):
            tools.addWidget(widget)

        panel_layout.addWidget(tool_row)

        from .canvas import CanvasHost

        self._host = CanvasHost(self.session, panel)
        self._host.canvas.sigSelection.connect(self._on_selection)
        self._host.canvas.sigCommitted.connect(self._on_canvas_commit)
        self._host.canvas.sigStatus.connect(self._on_canvas_status)
        self._host.canvas.sigActivate.connect(self._activate_layer)
        self._host.canvas.sigZoom.connect(self._on_zoom)
        panel_layout.addWidget(self._host, 1)
        layout.addWidget(panel, 1)

        self._recent = RecentGrid(self.session)
        self._recent.setFixedHeight(114)
        self._recent.noteChosen.connect(self._open_path)
        self._recent.refreshRequested.connect(self._reload_recent)
        layout.addWidget(self._recent)
        return center

    def _build_rail(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("Rail")
        rail.setFixedWidth(264)
        self._rail = rail
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(8)

        self._dock = ActionsDock(rail, backend=self._backend)
        self._dock.setFixedHeight(200)
        self._dock.sigAction.connect(self._on_dock_action)
        layout.addWidget(self._dock)

        self._hud = TelemetryHUD(self.session, rail, backend=self._backend)
        self._hud.refreshRequested.connect(self._refresh_telemetry)
        layout.addWidget(self._hud, 1)
        return rail

    def _build_footer(self) -> QWidget:
        footer = self._footer
        grid = QGridLayout(footer)
        grid.setContentsMargins(14, 0, 14, 0)
        grid.setHorizontalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)

        left = QWidget()
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self._idle_chip = Chip("IDLE FREEZE", "mint")
        left_layout.addWidget(self._idle_chip)
        self._footer_status = QLabel("0 rasters · 0 writes · network listeners 0")
        self._footer_status.setProperty("role", "faint")
        left_layout.addWidget(self._footer_status)
        grid.addWidget(left, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._khai_hoa = QPushButton("KHAI HOẢ EXPORT NOTE IMAGE")
        self._khai_hoa.setProperty("role", "primary")
        self._khai_hoa.setFixedSize(342, 36)
        self._khai_hoa.setCursor(Qt.CursorShape.PointingHandCursor)
        self._khai_hoa.setToolTip("Render the vector stack and release the bitmap to disk")
        self._khai_hoa.clicked.connect(self._open_export)
        grid.addWidget(self._khai_hoa, 0, 1, Qt.AlignmentFlag.AlignCenter)

        right = QWidget()
        right_layout = QHBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        self._ocr_button = QPushButton("AUTO-OCR")
        self._ocr_button.setProperty("role", "ghost")
        self._ocr_button.setFixedHeight(28)
        self._ocr_button.clicked.connect(self._run_ocr)
        self._zip_button = QPushButton("ZIP BUNDLE")
        self._zip_button.setProperty("role", "ghost")
        self._zip_button.setFixedHeight(28)
        self._zip_button.clicked.connect(self._export_bundle)
        right_layout.addWidget(self._ocr_button)
        right_layout.addWidget(self._zip_button)
        grid.addWidget(right, 0, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return footer

    @staticmethod
    def _divider() -> QWidget:
        widget = QWidget()
        widget.setFixedSize(1, 16)
        widget.setStyleSheet(f"background: {PALETTE['line_hi']};")
        return widget

    # ------------------------------------------------------------------ #
    # shortcuts
    # ------------------------------------------------------------------ #
    def _wire_shortcuts(self) -> None:
        bindings = (
            # The muscle-key block is registered first: the interceptor must own
            # the sequence before any convenience binding can shadow it.
            ("Ctrl+C", self._intercept_copy),
            ("Ctrl+V", self._intercept_paste),
            ("Ctrl+X", self._intercept_cut),
            ("Ctrl+S", self._save_note),
            ("Ctrl+A", self._intercept_select_all),
            ("Ctrl+Shift+S", self._save_as_note),
            ("Ctrl+N", self._new_note),
            ("Ctrl+O", self._open_note),
            ("Ctrl+E", self._open_export),
            ("Ctrl+Shift+O", self._run_ocr),
            ("Ctrl+R", self._refresh_telemetry),
            ("Delete", self._delete),
            ("Ctrl+D", self._duplicate),
        )
        self._shortcuts: list[QShortcut] = []
        self._interceptor: dict[str, str] = {}
        for sequence, handler in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)
            if sequence in MUSCLE_KEYS:
                self._interceptor[sequence] = handler.__name__

    def _text_surface(self) -> QWidget | None:
        """Return the focused editor that must keep its own clipboard behaviour.

        A ``WindowShortcut`` pre-empts the focused child widget, so the
        interceptor hands the key back whenever a line edit, spin box or text
        area owns focus; otherwise Ctrl+C would stop copying selected text.
        """
        focus = QApplication.focusWidget()
        if isinstance(focus, TEXT_SURFACES):
            return focus
        return None

    def _intercept_copy(self) -> None:
        editor = self._text_surface()
        if editor is not None:
            editor.copy()
            return
        captured = self.session.copy_selection()
        if captured:
            self._sync_selection_ui()
            self._hud.update_all()
            self._toast(f"COPY · {captured} nodes → {self.session.clipboard.label()}")

    def _intercept_cut(self) -> None:
        editor = self._text_surface()
        if editor is not None:
            editor.cut()
            return
        captured = self.session.cut_selection()
        if captured:
            self._host.canvas.invalidate_boxes()
            self._sync_selection_ui()
            self._after_document_change(f"CUT · {captured} purged · buffer holds {self.session.clipboard.depth()}")

    def _intercept_paste(self) -> None:
        editor = self._text_surface()
        if editor is not None:
            editor.paste()
            return
        placed = self.session.paste_clipboard()
        if placed:
            self._host.canvas.invalidate_boxes()
            self._after_document_change(
                f"PASTE · {len(placed)} clones · offset {self.session.clipboard.offset():.0f} px"
            )
            self._sync_selection_ui()

    def _intercept_select_all(self) -> None:
        editor = self._text_surface()
        if editor is not None:
            editor.selectAll()
            return
        flagged = self.session.select_all()
        self._host.canvas.invalidate_boxes()
        self._sync_selection_ui()
        self._hud.update_all()
        self._toast(f"SELECT ALL · {flagged} nodes flagged for mass manipulation")

    def _sync_selection_ui(self) -> None:
        """Mirror the mass set across canvas, glass inspector and telemetry."""
        anchor = self.session.selected_id or ""
        self._host.inspector.bind(anchor)
        if self.session.selection:
            self._inspector_button.setChecked(True)
            self._inspector_button.set_selected(True)
            self._host.show_inspector()
        else:
            self._inspector_button.setChecked(False)
            self._inspector_button.set_selected(False)
            self._host.hide_inspector()
        self._refresh_canvas_state()
        self._host.canvas.update()

    # ------------------------------------------------------------------ #
    # refresh
    # ------------------------------------------------------------------ #
    def _refresh_everything(self) -> None:
        self._refresh_bitmap()
        self._refresh_header()
        self._refresh_canvas_state()
        self._refresh_telemetry()
        self._reload_recent()

    def _refresh_bitmap(self) -> None:
        self._host.canvas.refresh_bitmap()
        if self.session.raster.report is None:
            self._host.canvas.invalidate_boxes()
        self._host.inspector.bind(self.session.selected_id or "")
        self._refresh_canvas_state()

    def _refresh_header(self) -> None:
        document = self.session.document
        self._doc_title.setText(document.title.upper()[:42])
        counts = document.shape_counts()
        self._doc_chip.set_text(f"{document.node_count()} NODES")
        self._dock.set_badge(f"{document.node_count()} NODES")
        self._canvas_section.set_hint(
            f"{counts['text']} text · {counts['line'] + counts['rect'] + counts['ellipse']} shapes · "
            f"{counts['image']} images"
        )

    def _refresh_canvas_state(self) -> None:
        has_selection = self.session.selected_layer is not None
        self._inspector_button.setEnabled(has_selection)
        self._zoom_label.setText(f"{zoom_percent(self._host.canvas.zoom)}%")

    def _refresh_telemetry(self) -> None:
        self._hud.update_all()
        counters = self.session.ledger.counters
        self._footer_status.setText(
            f"{self.session.raster.ops} rasters · {counters.get('io.writes', 0)} writes · "
            f"network listeners 0 · budget {self.session.ocr.budget_ms:.1f} ms"
        )
        self._refresh_shield_chip()

    def _reload_recent(self) -> None:
        self._recent.reload()

    def _refresh_shield_chip(self) -> None:
        report = self.session.shield.report
        if report is None:
            self._shield_button.setText("SHIELD · NOT EVALUATED")
            return
        self._shield_button.setText(report.headline)
        tone = PALETTE["danger"] if report.armed else PALETTE["mint"]
        self._shield_button.setStyleSheet(
            f"QPushButton {{ color: {tone}; border: 1px solid {tone}55; background: {tone}14; "
            f"border-radius: 8px; padding: 2px 10px; font-size: 10px; font-weight: 700; }}"
        )

    def _toast(self, message: str) -> None:
        self._footer_status.setText(message)
        self.session.ledger.log("ui", message, "INFO")
        self._hud.update_all()

    # ------------------------------------------------------------------ #
    # commands
    # ------------------------------------------------------------------ #
    def _on_dock_action(self, kind: str) -> None:
        handlers = {
            "new": self._new_note,
            "open": self._open_note,
            "save": self._save_note,
            "save_as": self._save_as_note,
        }
        handler = handlers.get(kind)
        if handler is not None:
            handler()

    def _new_note(self) -> None:
        if not self._confirm_discard("Start a new note?"):
            return
        self.session.new_document("blank")
        self.session.document.title = "Untitled Note"
        self._after_document_change("new blank note")

    def _open_note(self) -> None:
        if not self._confirm_discard("Open another note?"):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open a universal PNG note",
            str(NOTES_DIR),
            "PNG containers (*.png);;Any image (*.png *.jpg *.jpeg *.svg *.ico);;All files (*)",
        )
        if not path:
            return
        self._open_path(path)

    def _open_path(self, raw_path: str) -> None:
        path = Path(raw_path)
        if not path.exists():
            self._toast(f"missing file · {path.name}")
            return
        try:
            document = self.session.open_document(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Open failed", f"{path.name}\n{exc}")
            self._toast(f"open failed · {path.name}")
            return
        record = self.session.last_container or {}
        mode = str(record.get("mode", "raster"))
        self._after_document_change(
            f"opened {path.name} · {'vector tree restored' if mode == 'vector' else 'flat raster imported'} · "
            f"{document.node_count()} nodes"
        )

    def _save_note(self) -> None:
        """``Ctrl+S``: low-level local disk writer for the canvas stack.

        The layer tree is packed into the open text chunks of a universal PNG and
        streamed through the ledger's single IO gate, so one keystroke produces a
        file that is simultaneously a static picture anywhere and a fully
        editable note here. Re-encoding to .jpg/.svg/.ico stays behind the
        KHAI HỎA command block, so a keystroke never costs an extra raster pass.
        """
        if self.session.document.path is None:
            self._save_as_note()
            return
        try:
            target = self.session.save_document(self.session.document.path, "SAVE")
        except (OSError, container.ContainerError) as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return
        self._toast(f"sealed · {target.name} · {self._container_line()}")
        self._reload_recent()
        self._refresh_telemetry()

    def _container_line(self) -> str:
        """Human-readable description of the container written last."""
        record = self.session.last_container or {}
        tree = float(record.get("tree_bytes", 0) or 0)
        return (
            f"{record.get('nodes', self.session.document.node_count())} nodes · "
            f"{tree / 1024.0:.1f} KiB tree in {record.get('chunks', 0)} chunks"
        )

    def _save_as_note(self) -> None:
        default = container.force_container(NOTES_DIR / f"{slugify(self.session.document.title)}")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save note as", str(default), "Universal PNG container (*.png)"
        )
        if not path:
            return
        try:
            target = self.session.save_document(Path(path), "SAVE_AS")
        except (OSError, container.ContainerError) as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return
        self._toast(f"sealed · {target.name} · {self._container_line()}")
        self._reload_recent()
        self._refresh_telemetry()

    def _apply_template(self, key: str) -> None:
        if not self._confirm_discard("Replace the current canvas with this template?"):
            return
        self.session.new_document(key)
        from ..core.templates import TEMPLATE_BY_KEY

        spec = TEMPLATE_BY_KEY.get(key)
        if spec is not None:
            self.session.document.title = spec.label
        self._after_document_change(f"template · {key}")

    def _after_document_change(self, message: str) -> None:
        self._refresh_bitmap()
        self._refresh_header()
        self._refresh_telemetry()
        self._host.inspector.bind(self.session.selected_id or "")
        self._toast(message)

    def _insert_text(self) -> None:
        dialog = TextDialog(self.session.document, None, self)
        dialog.center_on_parent()
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        layer = dialog.result_layer()
        if not layer.text.strip():
            self._toast("text node aborted · empty content")
            return
        self.session.add_layer(layer)
        self._after_document_change(f"text node · {layer.name}")

    def _insert_shape(self, kind: str) -> None:
        dialog = ShapeDialog(self.session.document, kind, self)
        dialog.center_on_parent()
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        layer = dialog.result_layer()
        self.session.add_layer(layer)
        self._after_document_change(f"shape node · {kind}")

    def _insert_image(self) -> None:
        dialog = ImageDialog(self)
        dialog.center_on_parent()
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        path = dialog.selected_path()
        if path is None:
            self._toast("image ingestion aborted · no readable file")
            return
        layer = self.session.ingest_image(path)
        if layer is None:
            self._toast("image rejected by the decode guard")
            return
        dialog.configure(layer)
        self.session.add_layer(layer)
        self._after_document_change(f"image node · {layer.name}")

    def _activate_layer(self, layer_id: str) -> None:
        layer = self.session.document.find(layer_id)
        if isinstance(layer, TextLayer):
            dialog = TextDialog(self.session.document, layer, self)
        elif isinstance(layer, ImageLayer):
            self._toast("image node · drag the corner handle to resize")
            return
        else:
            dialog = ShapeDialog(self.session.document, getattr(layer, "shape", "rect"), self)
        dialog.center_on_parent()
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        document = self.session.document
        if isinstance(layer, TextLayer):
            updated = dialog.result_layer()
            for attribute in (
                "text",
                "family",
                "size",
                "bold",
                "italic",
                "align",
                "fill",
                "x",
                "y",
                "w",
                "name",
            ):
                setattr(layer, attribute, getattr(updated, attribute))
        else:
            updated = dialog.result_layer()
            for attribute in ("shape", "x", "y", "w", "h", "stroke", "stroke_width", "fill", "radius"):
                setattr(layer, attribute, getattr(updated, attribute))
        document.clamp_layer(layer)
        self.session.commit_properties("commit")
        self._after_document_change("node updated")

    def _delete(self) -> None:
        purged = self.session.delete_selection()
        if purged:
            self._host.canvas.invalidate_boxes()
            self._sync_selection_ui()
            self._after_document_change(f"{purged} nodes purged from tree")

    def _duplicate(self) -> None:
        clone = self.session.duplicate_selected()
        if clone is not None:
            self._after_document_change(f"duplicated · {clone.name}")

    def _raise(self) -> None:
        if self.session.raise_selected():
            self._after_document_change("node raised")

    def _lower(self) -> None:
        if self.session.lower_selected():
            self._after_document_change("node lowered")

    def _run_ocr(self) -> None:
        try:
            result = self.session.run_ocr(force_pixels=False)
        except (OSError, ValueError) as exc:
            self._toast(f"OCR guard tripped · {exc}")
            return
        self._host.canvas.update()
        self._hud.update_all()
        budget_state = "within budget" if result.within_budget else "over budget"
        self._toast(
            f"OCR · {result.mode} · {len(result.lines)} lines · {result.latency_ms:.3f} ms ({budget_state})"
        )
        self._refresh_telemetry()

    def _open_export(self) -> None:
        document = self.session.document
        suggested = EXPORT_DIR / f"{slugify(document.title)}.png"
        dialog = ExportDialog(document, suggested, self)
        dialog.center_on_parent()
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        key, path, scale = dialog.selection()
        try:
            if key == "zip":
                receipt = self.session.export_bundle(path, scale)
            else:
                receipt = self.session.export_note(key, path, scale)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            self._toast(f"export failed · {exc}")
            return
        self._after_export(receipt)

    def _export_bundle(self) -> None:
        default = EXPORT_DIR / f"{slugify(self.session.document.title)}_bundle.zip"
        path, _ = QFileDialog.getSaveFileName(self, "Export ZIP bundle", str(default), "Mitchit bundle (*.zip)")
        if not path:
            return
        try:
            receipt = self.session.export_bundle(Path(path), 1.0)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Bundle failed", str(exc))
            return
        self._after_export(receipt)

    def _after_export(self, receipt) -> None:
        self._refresh_telemetry()
        self._toast(
            f"{receipt.format_key.upper()} · {receipt.size_bytes / 1024.0:.1f} KiB · "
            f"{receipt.elapsed_ms:.1f} ms → {receipt.path.name}"
        )

    def _confirm_discard(self, question: str) -> bool:
        document = self.session.document
        if document.node_count() == 0:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved vector state",
            f"{question}\n\nThe current stack holds {document.node_count()} nodes and has not been "
            "sealed to disk. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------------ #
    # canvas glue
    # ------------------------------------------------------------------ #
    def _on_selection(self, layer_id: str) -> None:
        self._refresh_canvas_state()
        if layer_id:
            self._inspector_button.setChecked(True)
            self._inspector_button.set_selected(True)
        else:
            self._inspector_button.setChecked(False)
            self._inspector_button.set_selected(False)

    def _on_canvas_commit(self, reason: str) -> None:
        self._refresh_bitmap()
        self._refresh_header()
        self._refresh_telemetry()
        self._toast(f"raster released · {reason}")

    def _on_canvas_status(self, message: str) -> None:
        self._footer_status.setText(message)

    def _on_zoom(self, zoom: float) -> None:
        self._zoom_label.setText(f"{zoom_percent(zoom)}%")

    def _zoom_fit(self) -> None:
        self._host.canvas.zoom_fit()
        self._zoom_label.setText("100%")

    def _toggle_grid(self) -> None:
        self.session.show_grid = self._grid_button.isChecked()
        self._grid_button.set_selected(self._grid_button.isChecked())
        self._host.canvas.update()

    def _toggle_inspector(self) -> None:
        visible = self._host.toggle_inspector()
        self._inspector_button.setChecked(visible)
        self._inspector_button.set_selected(visible)
        if visible:
            self._host.inspector.bind(self.session.selected_id or "")

    def _evaluate_shield(self) -> None:
        report = self.session.refresh_network()
        self._refresh_shield_chip()
        self._hud.update_all()
        if report.armed:
            QMessageBox.warning(
                self,
                "Egress veto armed",
                f"{report.reason}\n\nEvery non-loopback socket in this process is now denied. "
                "The studio has no cloud dependency, so note editing is unaffected.",
            )

    # ------------------------------------------------------------------ #
    # density + geometry
    # ------------------------------------------------------------------ #
    def _on_density_changed(self) -> None:
        value = clamp_scale(float(self._density.currentData() or DENSITY_DEFAULT))
        self._metrics = LayoutMetrics(value)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self._metrics)
        self._header.setFixedHeight(self._metrics.header_h)
        self._footer.setFixedHeight(self._metrics.footer_h)
        self._rail.setFixedWidth(self._metrics.rail_w)
        if self.centralWidget() is not None:
            self.centralWidget().setFixedSize(VIEWPORT_W, VIEWPORT_H)
        self._host.position_inspector()
        self._host.refresh_inspector_backdrop()
        self._refresh_glass()
        self._audits["density"] = True
        self._toast(f"layout density · {int(self._metrics.density * 100)}%")
        self._refresh_telemetry()

    def _refresh_glass(self) -> None:
        """Feed the right-rail glass panels a real backdrop for the C++ blur."""
        root = self.centralWidget()
        if root is None:
            return
        for panel in (self._dock, self._hud):
            if panel is None or panel.width() <= 0 or panel.height() <= 0:
                continue
            try:
                origin = panel.mapTo(root, QPoint(0, 0))
                pixmap = QPixmap(panel.size())
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                try:
                    root.render(
                        painter,
                        QPoint(0, 0),
                        QRegion(QRect(origin, panel.size())),
                        QWidget.RenderFlag.DrawWindowBackground,
                    )
                finally:
                    painter.end()
                panel.set_backdrop(pixmap)
            except (RuntimeError, ValueError):
                continue

    def refresh_glass(self) -> None:
        """Public hook used by the bootstrap screenshot and selftest passes."""
        self._refresh_glass()
        self._host.refresh_inspector_backdrop()

    def _center_on_screen(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x, y = clamp_position(
            available.x() + (available.width() - VIEWPORT_W) / 2,
            available.y() + (available.height() - VIEWPORT_H) / 2,
            VIEWPORT_W,
            VIEWPORT_H,
            available.width(),
            available.height(),
        )
        self.move(x, y)

    # ------------------------------------------------------------------ #
    # audits + lifecycle
    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._shown:
            return
        self._shown = True
        self._emit_build_milestones()
        self._refresh_glass()
        self._host.refresh_inspector_backdrop()

    def _emit_build_milestones(self) -> None:
        # BƯỚC 1 first: the proprietary suffix has to be provably gone from every
        # shipped source before the studio is allowed to announce it on stdout.
        ban = container.audit_banishment(
            PACKAGE_DIR,
            (PROJECT_ROOT / "main.py", PROJECT_ROOT / "run.sh"),
        )
        self._banishment = ban
        self._audits["banished"] = ban.clean
        self.milestones.emit("banished")
        if not ban.clean:
            self.milestones.diag(f"banishment offenders: {', '.join(ban.offenders)}")

        # Steganographic ingestion is claimed only after the codec has actually
        # round-tripped the live tree through its own integrity gates in memory.
        self._ingestion = self.session.verify_container_ingestion()
        self._audits["steganography"] = bool(self._ingestion.get("ok"))
        self.milestones.emit("steganography")

        # The interceptor audit proves all five muscle keys are live, window
        # scoped, and that four of them route through a delegating handler.
        # PyQt6 6.11 exposes QShortcut.key() (not keySequence()) and real enum
        # members for context(), so the comparison stays on the enum objects.
        sequences = {shortcut.key().toString() for shortcut in self._shortcuts}
        contexts = {shortcut.context() for shortcut in self._shortcuts}
        self._audits["shortcuts"] = (
            all(QKeySequence(key).toString() in sequences for key in MUSCLE_KEYS)
            and len(self._interceptor) == len(MUSCLE_KEYS)
            and contexts == {Qt.ShortcutContext.WindowShortcut}
            and all(
                getattr(self, name, None) is not None
                for name in self._interceptor.values()
            )
        )
        self.milestones.emit("shortcuts")

        central = self.centralWidget()
        width = central.width() if central is not None else 0
        height = central.height() if central is not None else 0
        self._audits["geometry"] = verify_viewport(width, height)
        if not self._audits["geometry"]:
            self.setFixedSize(VIEWPORT_W, VIEWPORT_H)
        self.milestones.emit("geometry")

        # Dashboard regions and the local OCR core stay contractual audits of
        # their own; the open-container sequence replaced their stdout lines.
        self._audits["carousel"] = self._carousel.isVisible() and self._carousel.height() > 0
        self._audits["dock"] = len(getattr(self._dock, "buttons", {})) == 4 and self._dock.x() > 0
        self._audits["ocr"] = self.session.ocr is not None and self.session.ocr.budget_ms > 0

        self._audits["vault"] = bool(self.vault is not None and self.vault.isolated)
        self.milestones.emit("vault")

        self._audits["idle"] = len(self.findChildren(QTimer)) == 0
        self.milestones.emit("idle")

    def closeEvent(self, event) -> None:
        self.session.ledger.log("session", "studio closed · no cloud session to tear down", "INFO")
        super().closeEvent(event)
