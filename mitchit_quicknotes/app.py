"""Sovereign bootstrap: audits, vault, gallery seeding, Qt shell and selftest.

Everything here is local and offline by construction: the only processes spawned
are read-only hardware/link probes, the only writes are the `.env` vault, the
optional seeded gallery and the artefacts the user explicitly requests. Documents
live as universal PNG containers whose editable layer tree rides in the open text
chunks, so nothing this module writes can ever be held hostage by a vendor.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import tempfile
import time
import traceback
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image as PillowImage
from PIL import ImageDraw as PillowDraw
from PIL import ImageFont as PillowFont
from PIL import __version__ as PILLOW_VERSION

from . import APP_NAME, APP_RELEASE, APP_VERSION, MILESTONE_TOKEN
from .core import container
from .core.constants import (
    CONTAINER_SUFFIX,
    DENSITY_DEFAULT,
    DENSITY_STEPS,
    EXPORT_DIR,
    EXPORT_FORMATS,
    GITIGNORE_FILE,
    NOTES_DIR,
    OCR_BUDGET_MS,
    PACKAGE_DIR,
    PROJECT_ROOT,
    VIEWPORT_H,
    VIEWPORT_W,
)
from .core.geometry import LayoutMetrics, clamp_axis
from .core.ledger import probe_hardware
from .core.milestones import MILESTONE_KEYS, SEQUENCE, MilestoneLog
from .core.model import NoteDocument, layer_from_dict, slugify
from .core.renderer import rasterize
from .core.templates import build_template
from .core.vault import VaultReport, ensure_vault, env_float
from .session import BATCH_REASON, StudioSession

MILE_LINE = f"{MILESTONE_TOKEN} ["
SEED_KEYS = ("formula", "todo", "memo")


# --------------------------------------------------------------------------- #
# diagnostics: stdout is reserved for the seven milestone lines
# --------------------------------------------------------------------------- #
def _diag(message: str) -> None:
    try:
        sys.stderr.write(f"[bootstrap] {message}\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


class TeeStream:
    """Forward milestone writes to stdout and keep a copy for the audit."""

    def __init__(self, target, sink: list[str]) -> None:
        self._target = target
        self._sink = sink

    def write(self, text: str) -> int:
        self._sink.append(text.rstrip("\n"))
        try:
            return self._target.write(text)
        except (OSError, ValueError):
            return 0

    def flush(self) -> None:
        try:
            self._target.flush()
        except (OSError, ValueError):
            pass


# --------------------------------------------------------------------------- #
# environment probes
# --------------------------------------------------------------------------- #
def pillow_probe() -> str:
    """Prove Pillow can allocate, draw and load a vector face before boot."""
    canvas = PillowImage.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = PillowDraw.Draw(canvas, "RGBA")
    try:
        font = PillowFont.truetype("DejaVuSans-Bold.ttf", 22)
    except OSError:
        font = PillowFont.load_default()
    draw.rounded_rectangle((1, 1, 62, 62), radius=14, fill=(78, 161, 255, 255))
    draw.text((9, 18), "MQ", font=font, fill=(4, 18, 28, 255))
    probe = canvas.getpixel((32, 32))
    if not isinstance(probe, tuple) or len(probe) != 4:
        raise RuntimeError("Pillow RGBA buffer probe returned an unexpected colour tuple")
    canvas.close()
    return PILLOW_VERSION


def prepare_workspace() -> None:
    for directory in (NOTES_DIR, EXPORT_DIR):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _diag(f"workspace directory unavailable · {directory} · {exc}")


def apply_parameters(session: StudioSession, parameters: dict[str, str]) -> None:
    budget = env_float(parameters, "MQ_OCR_BUDGET_MS", OCR_BUDGET_MS)
    session.ocr.budget_ms = max(0.05, budget)
    policy = parameters.get("MQ_EGRESS_POLICY", "deny")
    session.ledger.log(
        "boot",
        f"parameters loaded · ocr budget {session.ocr.budget_ms:.2f} ms · egress policy {policy}",
        "INFO",
    )


def seed_gallery(ledger) -> list[Path]:
    """Write the three showcase notes once, as universal PNG containers."""
    written: list[Path] = []
    for key in SEED_KEYS:
        document = build_template(key)
        target = container.force_container(NOTES_DIR / slugify(document.title))
        if target.exists():
            continue
        try:
            image, _report = rasterize(document, 1.0)
            payload = container.embed(document, image)
            ledger.write_bytes(target, payload, "SEED")
        except (OSError, container.ContainerError, ValueError) as exc:
            _diag(f"seed failed · {target.name} · {exc}")
            continue
        written.append(target)
    if written:
        _diag(f"seeded {len(written)} sample containers into {NOTES_DIR}")
    return written


def build_session(parameters: dict[str, str]) -> StudioSession:
    session = StudioSession()
    apply_parameters(session, parameters)
    try:
        session.hardware = probe_hardware()
    except OSError as exc:
        session.hardware = None
        session.ledger.log("boot", f"hardware probe degraded · {exc}", "WARN")
    try:
        report = session.refresh_network()
        _diag(f"shield · {report.headline} · {report.reason}")
    except (OSError, ValueError) as exc:
        session.ledger.log("shield", f"network evaluation degraded · {exc}", "WARN")
    if not session.rasterize("startup"):
        session.ledger.log("boot", "startup raster suppressed", "ERROR")
    return session


# --------------------------------------------------------------------------- #
# Qt shell
# --------------------------------------------------------------------------- #
def create_application(offscreen: bool):
    if offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication

    from .ui.theme import apply_theme

    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0] or "mitchit-quicknotes"])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(f"{APP_VERSION} {APP_RELEASE}")
    app.setOrganizationName("The Mitchit Technologies")
    app.setDesktopFileName("mitchit-quicknotes")
    apply_theme(app, LayoutMetrics(DENSITY_DEFAULT))
    return app


def build_window(session: StudioSession, vault: VaultReport, milestones: MilestoneLog):
    from .ui.window import MainWindow

    return MainWindow(session, vault, milestones)


def pump(app, seconds: float = 0.0) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        app.processEvents()
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return
        time.sleep(min(0.02, remaining))


def capture_window(app, window, target: Path, delay: float = 0.4) -> tuple[bool, int, int]:
    pump(app, delay)
    try:
        window.refresh_glass()
    except (RuntimeError, ValueError):
        pass
    pump(app, 0.05)
    pixmap = window.grab()
    try:
        from PyQt6.QtCore import Qt

        if pixmap.devicePixelRatio() not in (0.0, 1.0):
            pixmap = pixmap.scaled(
                window.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    except (RuntimeError, ValueError):
        pass
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _diag(f"screenshot directory unavailable · {exc}")
        return False, 0, 0
    saved = pixmap.save(str(target), "PNG")
    return bool(saved), pixmap.width(), pixmap.height()


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class SelfTestReport:
    checks: list[Check] = field(default_factory=list)
    duration_ms: float = 0.0

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, bool(ok), detail))

    @property
    def passed(self) -> int:
        return sum(1 for check in self.checks if check.ok)

    @property
    def failed(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]

    def render(self) -> str:
        rows = [
            f"[selftest] {'PASS' if check.ok else 'FAIL'} · {check.name}"
            + (f" · {check.detail}" if check.detail else "")
            for check in self.checks
        ]
        rows.append(
            f"[selftest] {self.passed}/{len(self.checks)} checks passed in {self.duration_ms:.0f} ms"
        )
        return "\n".join(rows)


def _read_gitignore() -> set[str]:
    try:
        return {line.strip() for line in GITIGNORE_FILE.read_text(encoding="utf-8").splitlines()}
    except OSError:
        return set()


def run_selftest(
    app,
    window,
    session: StudioSession,
    vault: VaultReport,
    milestones: MilestoneLog,
    captured: list[str],
) -> SelfTestReport:
    started = time.perf_counter()
    report = SelfTestReport()
    from PyQt6.QtCore import Qt, QTimer

    pump(app, 0.15)

    expected = [f"{MILE_LINE}{token}]" for _, token in SEQUENCE]
    report.record(
        "milestones_stdout",
        captured == expected,
        f"{len(captured)}/{len(expected)} exact lines",
    )
    report.record(
        "milestones_emitted",
        milestones.emitted == list(MILESTONE_KEYS),
        " → ".join(milestones.emitted),
    )
    report.record(
        "geometry_lock",
        window.width() == VIEWPORT_W and window.height() == VIEWPORT_H,
        f"{window.width()}×{window.height()}",
    )
    central = window.centralWidget()
    report.record(
        "client_area_lock",
        central is not None
        and central.width() == VIEWPORT_W
        and central.height() == VIEWPORT_H,
        f"{central.width() if central else 0}×{central.height() if central else 0}",
    )

    report.record("clamp_negative", clamp_axis(-480, 320) == 0, "x=max(0,min(x,960))")
    report.record("clamp_overflow", clamp_axis(1500, 320) == 1280 - 320, "x=max(0,min(x,960))")
    report.record("clamp_inside", clamp_axis(412, 320) == 412, "interior positions are preserved")

    report.record("vault_env_present", vault.env_path.exists(), str(vault.env_path))
    report.record(".env", ".env" in _read_gitignore(), "token locked inside .gitignore")
    try:
        mode = stat.S_IMODE(vault.env_path.stat().st_mode)
    except OSError:
        mode = 0
    report.record("vault_permissions", mode == 0o600, oct(mode))

    dock = getattr(window, "_dock", None)
    triggers = sorted(getattr(dock, "buttons", {})) if dock is not None else []
    report.record(
        "dock_four_triggers",
        triggers == ["new", "open", "save", "save_as"],
        ", ".join(triggers),
    )
    carousel = getattr(window, "_carousel", None)
    card_count = 0
    if carousel is not None:
        try:
            card_count = carousel._row.content_layout.count()
        except (AttributeError, RuntimeError):
            card_count = 0
    report.record(
        "carousel_present",
        carousel is not None and carousel.height() > 0 and card_count > 0,
        f"{card_count} template cards",
    )
    report.record(
        "timers_absent",
        len(window.findChildren(QTimer)) == 0,
        "zero idle timers inside the shell",
    )
    report.record(
        "audits",
        all(
            window._audits.get(key, False)
            for key in (
                "banished",
                "steganography",
                "geometry",
                "shortcuts",
                "carousel",
                "dock",
                "ocr",
                "vault",
                "idle",
            )
        ),
        str(window._audits),
    )

    # ------------------------------------------------------------------ #
    # the five muscle keys of the interceptor contract, exercised for real:
    # the handlers wired to QShortcut are called directly, so binding,
    # delegation, clipboard validation and mass manipulation all get proven.
    # ------------------------------------------------------------------ #
    from PyQt6.QtGui import QKeySequence

    from .core.clipboard import ClipboardBuffer
    from .core.model import TextLayer
    from .ui.window import MUSCLE_KEYS, TEXT_SURFACES

    sequences = {shortcut.key().toString() for shortcut in window._shortcuts}
    report.record(
        "interceptor_bindings",
        all(QKeySequence(key).toString() in sequences for key in MUSCLE_KEYS)
        and set(window._interceptor) == set(MUSCLE_KEYS)
        and all(hasattr(window, name) for name in window._interceptor.values()),
        " · ".join(f"{key}→{window._interceptor.get(key, 'unbound')}" for key in MUSCLE_KEYS),
    )
    report.record(
        "interceptor_window_context",
        bool(window._shortcuts)
        and {shortcut.context() for shortcut in window._shortcuts}
        == {Qt.ShortcutContext.WindowShortcut},
        f"{len(window._shortcuts)} sequences registered as WindowShortcut",
    )

    # A WindowShortcut pre-empts the focused child, so the interceptor must hand
    # the key back the moment an editor owns focus and take it back afterwards.
    # Binding a text node first is what makes the inspector editors reachable.
    anchor = next((node for node in window.session.document.layers if isinstance(node, TextLayer)), None)
    if anchor is not None:
        window.session.select(anchor.id)
        window._sync_selection_ui()
        pump(app, 0.02)
    editors = [
        candidate
        for candidate in window.findChildren(TEXT_SURFACES)
        if candidate.isVisibleTo(window) and getattr(candidate, "isEditable", lambda: True)()
    ]
    delegated = None
    if editors:
        editors[0].setFocus(Qt.FocusReason.OtherFocusReason)
        pump(app, 0.02)
        delegated = window._text_surface()
    report.record(
        "interceptor_text_delegation",
        delegated is not None and delegated is app.focusWidget(),
        f"{len(editors)} editable surfaces · {type(delegated).__name__ if delegated is not None else 'none'} keeps native Ctrl+C",
    )
    window._host.canvas.setFocus(Qt.FocusReason.OtherFocusReason)
    pump(app, 0.02)
    report.record(
        "interceptor_canvas_ownership",
        window._text_surface() is None,
        "canvas focus routes the muscle keys to the document",
    )

    document = session.document
    frozen = list(document.layers)
    unlocked = sum(1 for node in frozen if not node.locked)
    rasters_start = session.raster.ops
    suppressed_start = session.raster.suppressed

    session.clear_selection()
    window._intercept_select_all()
    pump(app, 0.02)
    report.record(
        "muscle_select_all",
        bool(session.selection)
        and len(session.selection) == unlocked
        and session.selected_id == session.selection[-1],
        f"{len(session.selection)} of {len(frozen)} nodes flagged for mass manipulation",
    )

    buffer = session.clipboard
    known_ids = {node.id for node in document.layers}
    window._intercept_copy()
    pump(app, 0.02)
    report.record(
        "muscle_copy",
        buffer.depth() == len(session.selection) == unlocked and buffer.bytes_used > 0 and buffer.captures == 1,
        f"{buffer.depth()} entries · {buffer.bytes_used // 1024} KiB · {buffer.label()}",
    )

    origins = [
        (float(getattr(node, "x", 0.0)), float(getattr(node, "y", 0.0)))
        for node in session.selection_layers
    ]
    window._intercept_paste()
    pump(app, 0.02)
    clones = [node for node in document.layers if node.id not in known_ids]
    shifts = [
        (
            float(getattr(clone, "x", 0.0)) - origin_x,
            float(getattr(clone, "y", 0.0)) - origin_y,
        )
        for (origin_x, origin_y), clone in zip(origins, clones)
    ]
    offset_active = buffer.offset()
    report.record(
        "muscle_paste_offset",
        len(clones) == len(origins)
        and len({node.id for node in clones}) == len(clones)
        and all(-0.01 <= dx <= offset_active + 0.01 and -0.01 <= dy <= offset_active + 0.01 for dx, dy in shifts)
        and any(abs(dx - offset_active) <= 0.01 and abs(dy - offset_active) <= 0.01 for dx, dy in shifts),
        f"{len(clones)} fresh ids · {offset_active:.0f} px geometric offset",
    )

    trio = [node.id for node in clones[:3]]
    session.select_many(trio)
    window._intercept_cut()
    pump(app, 0.02)
    surviving = {node.id for node in document.layers}
    report.record(
        "muscle_cut_purges_tree",
        not any(node_id in surviving for node_id in trio)
        and buffer.depth() == len(trio)
        and not session.selection,
        f"{len(trio)} nodes purged from the tree · buffer holds {buffer.depth()} · mass set reset",
    )

    sample = [node.id for node in document.layers if node.id in {node.id for node in clones} - set(trio)][:4]
    session.select_many(sample)
    stamp = {
        node.id: (float(getattr(node, "x", 0.0)), float(getattr(node, "y", 0.0)))
        for node in session.selection_layers
    }
    rasters_before_move = session.raster.ops
    moved = session.move_selection(12.0, -6.0)
    moves = [
        (
            float(getattr(node, "x", 0.0)) - stamp[node.id][0],
            float(getattr(node, "y", 0.0)) - stamp[node.id][1],
        )
        for node in session.selection_layers
    ]
    report.record(
        "muscle_mass_translate",
        moved == len(sample)
        and all(-0.01 <= dx <= 12.01 and -6.01 <= dy <= 0.01 for dx, dy in moves)
        and any(abs(dx - 12.0) <= 0.01 and abs(dy + 6.0) <= 0.01 for dx, dy in moves)
        and session.raster.ops - rasters_before_move == 1,
        f"{moved} nodes in one rigid pass · {session.raster.ops - rasters_before_move} raster",
    )

    rewritten = session.apply_to_selection({"opacity": 0.55})
    report.record(
        "muscle_mass_parameter_write",
        rewritten == len(sample)
        and all(abs(float(node.opacity) - 0.55) < 0.001 for node in session.selection_layers),
        f"{rewritten} nodes rewritten by a single commit",
    )

    report.record(
        "interceptor_idle_freeze_intact",
        session.raster.suppressed == suppressed_start and session.raster.ops > rasters_start,
        f"{session.raster.ops - rasters_start} rasters for the whole muscle block · suppression {suppressed_start} → {session.raster.suppressed}",
    )

    class _Impostor:
        """A node that is not a canvas object: the buffer must refuse it."""

        kind = "telemetry-packet"
        name = "impostor"
        id = "impostor-01"
        visible = True
        locked = False

        def to_dict(self) -> dict[str, str]:
            return {"id": self.id, "kind": self.kind}

    sandbox = ClipboardBuffer()
    refused = sandbox.capture([_Impostor()])
    report.record(
        "clipboard_validation",
        refused == 0 and sandbox.rejected == 1 and sandbox.is_empty() and sandbox.harvest() == [],
        "unknown node kind refused before materialisation",
    )

    document.layers = frozen
    session.clear_selection()
    buffer.clear()
    session.rasterize(BATCH_REASON)
    window._host.canvas.invalidate_boxes()
    window._host.canvas.refresh_bitmap()
    window._sync_selection_ui()
    pump(app, 0.02)
    report.record(
        "interceptor_document_restored",
        document.node_count() == len(frozen) and buffer.is_empty() and not session.selection,
        f"{len(frozen)} nodes back from the frozen snapshot · buffer flushed",
    )

    # The glass frame is painted by the base class, so no subclass control may ever
    # shadow its maths: a corner-radius QSpinBox in the inspector once landed on the
    # base `_radius` slot and crashed the live repaint.
    from PyQt6.QtGui import QColor

    from .ui.glass import GlassPanel

    surfaces = window.findChildren(GlassPanel)
    frame_fields: dict[str, type] = {
        "_glass_radius": float,
        "_glass_blur_radius": float,
        "_glass_tint_alpha": int,
        "_glass_tint": QColor,
        "_glass_border": QColor,
    }
    shadowed: list[str] = []
    for surface in surfaces:
        for name, expected_type in frame_fields.items():
            value = getattr(surface, name, None)
            if not isinstance(value, expected_type):
                shadowed.append(f"{type(surface).__name__}.{name}={type(value).__name__}")
    report.record(
        "glass_field_types",
        len(surfaces) > 0 and not shadowed,
        f"{len(surfaces)} surfaces · " + (", ".join(shadowed) or "frame maths intact"),
    )

    host = getattr(window, "_host", None)
    if host is not None:
        try:
            host.toggle_inspector()
        except (AttributeError, RuntimeError):
            pass
    pump(app, 0.05)

    # Replay the exact statement that crashed the live inspector, driven by each
    # surface's own frame state, plus the backdrop path. Calling paintEvent
    # directly would only earn "QPainter::begin: Paint device returned engine" noise.
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QPainterPath

    paint_errors: list[str] = []
    for surface in surfaces:
        label = type(surface).__name__
        try:
            radius = surface._frame_radius()
            if not isinstance(radius, float) or radius != radius or radius < 0.0:
                paint_errors.append(f"{label}: radius {radius!r}")
                continue
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, surface.width(), surface.height()), radius, radius)
            surface._blurred()
        except Exception as failure:  # a repaint crash must be reported, never fatal
            paint_errors.append(f"{label}: {type(failure).__name__}: {failure}")
    report.record(
        "glass_paint_geometry",
        len(surfaces) > 0 and not paint_errors,
        " || ".join(paint_errors) or f"{len(surfaces)} frame paths built clean",
    )

    image = session.raster.image
    report.record(
        "raster_buffer",
        image is not None and image.size == (session.document.width, session.document.height),
        f"{image.size if image else 'none'} · {session.raster.elapsed_ms:.1f} ms",
    )
    before = session.raster.suppressed
    suppressed = session.rasterize("idle") is False and session.raster.suppressed == before + 1
    report.record("idle_freeze", suppressed, f"suppressed counter {before} → {session.raster.suppressed}")

    try:
        vector = session.run_ocr(force_pixels=False)
        report.record(
            "ocr_vector_fast_path",
            vector.mode == "VECTOR_TEXT_LAYERS"
            and len(vector.lines) > 0
            and vector.latency_ms <= session.ocr.budget_ms,
            f"{vector.latency_ms * 1000:.0f} µs · {len(vector.lines)} lines · {vector.char_count} chars",
        )
    except (OSError, ValueError) as exc:
        report.record("ocr_vector_fast_path", False, str(exc))
    try:
        atlas = session.run_ocr(force_pixels=True)
        report.record(
            "ocr_raster_pipeline",
            len(atlas.lines) > 0 and atlas.char_count > 0,
            f"{atlas.mode} · {atlas.latency_ms:.1f} ms · conf {atlas.confidence:.3f}",
        )
    except (OSError, ValueError) as exc:
        report.record("ocr_raster_pipeline", False, str(exc))

    try:
        with tempfile.TemporaryDirectory(prefix="mitchit-selftest-") as sandbox:
            root = Path(sandbox)
            for spec in EXPORT_FORMATS:
                receipt = session.export_note(spec.key, root / f"note{spec.extension}", 1.0)
                target = receipt.path
                report.record(
                    f"export_{spec.key}",
                    receipt.ok and target.exists() and target.stat().st_size > 0,
                    f"{receipt.size_bytes / 1024.0:.1f} KiB · {receipt.elapsed_ms:.1f} ms",
                )
            svg_text = (root / "note.svg").read_text(encoding="utf-8")
            report.record(
                "svg_vector_valid",
                svg_text.startswith("<?xml") and svg_text.rstrip().endswith("</svg>"),
                f"{len(svg_text)} chars",
            )
            bundle = session.export_bundle(root / "bundle.zip", 1.0)
            with zipfile.ZipFile(bundle.path) as archive:
                names = set(archive.namelist())
            required = {
                "core/index.html",
                "assets/note.png",
                "assets/note.svg",
                "assets/thumb.png",
                "state/document.json",
                "state/manifest.json",
            }
            report.record(
                "zip_bundle_core_node",
                required.issubset(names),
                f"{len(names)} nodes · core/index.html present",
            )
            with zipfile.ZipFile(bundle.path) as archive:
                html = archive.read("core/index.html").decode("utf-8")
            report.record(
                "zip_relative_routing",
                'src="../assets/note.png"' in html and "http://" not in html and "https://" not in html,
                "every asset path is relative",
            )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        report.record("export_sandbox", False, str(exc))

    # ------------------------------------------------------------------ #
    # anti-vendor-lock-in container: a save has to land as a universal PNG that
    # any outside reader displays, while its open text chunks restore the exact
    # editable tree. Everything below runs in a throwaway directory, and the
    # document's live path is put back afterwards so the shell stays unsaved.
    # ------------------------------------------------------------------ #
    path_before = session.document.path
    try:
        with tempfile.TemporaryDirectory(prefix="mitchit-container-") as sandbox:
            root = Path(sandbox)
            forced_named = container.force_container(root / "Bao Cao Ke Hoach.any")
            forced_bare = container.force_container(root / "no-extension")
            report.record(
                "container_suffix_policy",
                forced_named.suffix == CONTAINER_SUFFIX and forced_bare.suffix == CONTAINER_SUFFIX,
                f"every save target collapses onto {CONTAINER_SUFFIX} · {forced_named.name}",
            )

            writes_before = session.ledger.counters.get("io.writes", 0)
            rasters_before = session.raster.ops
            saved = session.save_document(root / "sealed-document", "SAVE_AS")
            signature = saved.read_bytes()[:8]
            report.record(
                "container_save_is_png",
                saved.suffix == CONTAINER_SUFFIX
                and saved.exists()
                and signature == b"\x89PNG\r\n\x1a\n",
                f"{saved.name} · {saved.stat().st_size / 1024.0:.1f} KiB · PNG signature intact",
            )
            report.record(
                "container_save_costs_no_raster",
                session.raster.ops == rasters_before
                and session.ledger.counters.get("io.writes", 0) == writes_before + 1,
                f"{session.raster.ops - rasters_before} rasters · one gated disk write",
            )

            chunks = container.read_chunks(saved)
            report.record(
                "container_open_chunks",
                chunks.get(container.chunk_key("magic")) == container.MAGIC_TOKEN
                and chunks.get(container.chunk_key("format")) == container.FORMAT_TOKEN
                and len(chunks) >= 12,
                f"{len(chunks)} text chunks · {chunks.get(container.chunk_key('slices'))} slice(s) · "
                f"{chunks.get(container.chunk_key('bytes'))} B tree",
            )

            restored, ingestion = container.load_document(saved)

            def canonical(layer: object) -> dict[str, object] | None:
                """Round a layer through the loader so both sides are comparable."""
                clone = layer_from_dict(layer.to_dict())  # type: ignore[attr-defined]
                return clone.to_dict() if clone is not None else None

            originals = [canonical(layer) for layer in session.document.layers]
            mirrors = [canonical(layer) for layer in restored.layers]
            identical = [
                before == after
                for before, after in zip(originals, mirrors)
                if before is not None and after is not None
            ]
            report.record(
                "container_roundtrip_exact",
                ingestion.mode == "vector"
                and isinstance(restored, NoteDocument)
                and restored.node_count() == session.document.node_count()
                and len(identical) == session.document.node_count()
                and all(identical)
                and [layer.id for layer in restored.layers] == [layer.id for layer in session.document.layers],
                f"{ingestion.nodes} nodes · {ingestion.tree_bytes / 1024.0:.1f} KiB tree · "
                f"{sum(1 for layer in restored.layers if getattr(layer, 'text', ''))} text layers identical",
            )

            paper = session.document.background.lstrip("#")
            expected = tuple(int(paper[index:index + 2], 16) for index in (0, 2, 4))
            with PillowImage.open(saved) as blind:
                blind.load()
                blind_size = blind.size
                corner = blind.getpixel((blind_size[0] - 3, blind_size[1] - 3))
            report.record(
                "container_static_anywhere",
                blind_size == (session.document.width, session.document.height)
                and tuple(corner[:3]) == expected,
                f"{blind_size[0]}×{blind_size[1]} opens in a bare Pillow reader · paper corner {corner[:3]} "
                f"matches the declared background {expected}",
            )

            block = dict(chunks)
            slice_prefix = container.chunk_key("slice|")
            slice_keys = [key for key in sorted(block) if key.startswith(slice_prefix)]
            slice_key = max(slice_keys, key=lambda key: len(block[key])) if slice_keys else ""
            if slice_key:
                cut = min(12, len(block[slice_key]) - 1)
                seed_char = block[slice_key][cut]
                swapped = "B" if seed_char != "B" else "C"
                tampered = dict(block)
                tampered[slice_key] = block[slice_key][:cut] + swapped + block[slice_key][cut + 1:]
            else:
                tampered = {container.chunk_key("slice|0000"): "AAAA"}
            leaked, warnings = container.extract_tree(tampered)
            report.record(
                "container_integrity_gate",
                leaked == b"" and bool(warnings),
                "; ".join(warnings) or "corruption slipped through",
            )

            flat = PillowImage.new("RGB", (640, 400), (24, 60, 96))
            foreign = root / "holiday-scan.png"
            flat.save(foreign)
            imported, import_report = container.load_document(foreign)
            report.record(
                "container_foreign_image_editable",
                import_report.mode == "raster"
                and imported.node_count() == 1
                and imported.layers[0].kind == "image"
                and imported.width == session.document.width,
                f"{foreign.name} imported as one editable image node · {import_report.detail}",
            )

            library = container.scan(root, 12)
            report.record(
                "container_library_scan",
                len(library) >= 2 and sum(1 for item in library if item.editable) >= 1,
                " · ".join(f"{item.title[:14]}({'VEC' if item.editable else 'FLAT'},{item.nodes}n)" for item in library),
            )

            export = session.export_note("png", root / "khai-hoa.png", 1.0)
            export_chunks = container.read_chunks(export.path)
            report.record(
                "export_png_carries_tree",
                export_chunks.get(container.chunk_key("magic")) == container.MAGIC_TOKEN
                and int(export_chunks.get(container.chunk_key("nodes"), 0) or 0) == session.document.node_count(),
                f"{len(export_chunks)} chunks shipped inside the KHAI HOA raster · re-openable as a note",
            )
            jpg = session.export_note("jpg", root / "khai-hoa.jpg", 1.0)
            with PillowImage.open(jpg.path) as handle:
                description = str(handle.getexif().get(0x010E, ""))
            report.record(
                "export_jpg_exif_block",
                "Mitchit Quicknotes" in description and "nodes=" in description,
                description[:78] or "no EXIF description written",
            )
    except (OSError, ValueError, container.ContainerError) as exc:
        report.record("container_sandbox", False, str(exc))
    finally:
        session.document.path = path_before

    ban = container.audit_banishment(PACKAGE_DIR, (PROJECT_ROOT / "main.py", PROJECT_ROOT / "run.sh"))
    report.record(
        "prop_format_banished",
        ban.clean and ban.container == CONTAINER_SUFFIX and window._audits.get("banished", False),
        f"{ban.scanned} sources scanned · {ban.headline()}",
    )
    probe = session.container_probe or {}
    report.record(
        "steganography_ingestion",
        bool(probe.get("ok")) and int(probe.get("slices", 0) or 0) >= 1 and window._audits.get("steganography", False),
        str(probe.get("detail", "probe never ran")),
    )

    saved_density = window._density.currentIndex()
    densities_ok = True
    density_detail = []
    try:
        for index, step in enumerate(DENSITY_STEPS):
            window._density.setCurrentIndex(index)
            pump(app, 0.02)
            metrics = LayoutMetrics(step)
            sizes_ok = (
                window.width() == VIEWPORT_W
                and window.height() == VIEWPORT_H
                and window._rail.width() == metrics.rail_w
                and window._header.height() == metrics.header_h
                and window._footer.height() == metrics.footer_h
            )
            densities_ok = densities_ok and sizes_ok
            density_detail.append(f"{int(step * 100)}%:{'ok' if sizes_ok else 'drift'}")
    finally:
        window._density.setCurrentIndex(saved_density)
        pump(app, 0.02)
    report.record(
        "density_ladder_50_200",
        densities_ok,
        " ".join(density_detail),
    )

    session.ledger.log("selftest", f"{report.passed}/{len(report.checks)} checks passed", "INFO")
    report.duration_ms = (time.perf_counter() - started) * 1000.0
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mitchit-quicknotes",
        description="Mitchit Quicknotes Studio v1.0 RTM · local PyQt6 + Pillow note engine",
        add_help=True,
    )
    parser.add_argument("--selftest", action="store_true", help="run the headless audit suite and exit")
    parser.add_argument("--onscreen", action="store_true", help="keep the platform plugin even with --selftest")
    parser.add_argument("--offscreen", action="store_true", help="force the offscreen Qt platform plugin")
    parser.add_argument(
        "--seed-samples",
        action="store_true",
        help="write the three showcase notes once, as universal PNG containers",
    )
    parser.add_argument("--screenshot", metavar="PATH", default="", help="capture the shell to a PNG and exit")
    parser.add_argument(
        "--capture-delay",
        type=float,
        default=0.45,
        metavar="SECONDS",
        help="settle time before the screenshot is taken (default 0.45)",
    )
    parser.add_argument("--version", action="store_true", help="print the release triple and exit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(f"{APP_NAME} {APP_VERSION} {APP_RELEASE} · python {sys.version.split()[0]} · Pillow {PILLOW_VERSION}")
        return 0

    offscreen = args.offscreen or (args.selftest and not args.onscreen)
    captured: list[str] = []
    milestones = MilestoneLog(stream=TeeStream(sys.stdout, captured))
    _diag(f"{APP_NAME} {APP_VERSION} {APP_RELEASE} booting · root {PROJECT_ROOT}")
    _diag(f"python {sys.version.split()[0]} · 64-bit {sys.maxsize > 2**32} · pillow {PILLOW_VERSION}")

    session: StudioSession | None = None
    window = None
    exit_code = 0
    try:
        app = create_application(offscreen)
        milestones.emit("shell_ready")

        version = pillow_probe()
        _diag(f"pillow engine online · {version}")

        vault = ensure_vault()
        _diag(vault.describe())
        prepare_workspace()

        session = build_session(vault.parameters)
        if args.seed_samples:
            seed_gallery(session.ledger)

        window = build_window(session, vault, milestones)
        window.show()
        pump(app, 0.1)
        try:
            window.refresh_glass()
        except (RuntimeError, ValueError):
            pass
        pump(app, 0.05)

        if args.screenshot:
            target = Path(args.screenshot).expanduser()
            saved, width, height = capture_window(app, window, target, args.capture_delay)
            _diag(f"screenshot {'saved' if saved else 'FAILED'} · {width}×{height} · {target}")
            if not saved:
                exit_code = 1

        if args.selftest:
            report = run_selftest(app, window, session, vault, milestones, captured)
            try:
                sys.stderr.write(report.render() + "\n")
                sys.stderr.flush()
            except (OSError, ValueError):
                pass
            if report.failed:
                exit_code = 1
            window.close()
            pump(app, 0.05)
            return exit_code

        if args.screenshot:
            window.close()
            pump(app, 0.05)
            return exit_code

        return app.exec()
    except KeyboardInterrupt:
        _diag("interrupted by the operator")
        return 130
    except Exception:
        _diag("fatal bootstrap failure\n" + traceback.format_exc())
        return 1
    finally:
        if window is not None and args.selftest:
            try:
                window.deleteLater()
            except RuntimeError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
