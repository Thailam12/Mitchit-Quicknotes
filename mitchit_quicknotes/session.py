"""Studio session: single source of truth for document state and IO policy.

Every rasterisation request must name its cause. Requests that do not originate
from an explicit user command are counted and refused, which is what makes the
zero-debounce idle freeze verifiable rather than decorative.
"""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from PIL import Image

from .core.constants import EXPORT_DIR, NOTES_DIR, OCR_BUDGET_MS, OCR_MAX_SEGMENT_PIXELS
from .core import container
from .core.clipboard import ClipboardBuffer
from .core.exporter import ExportFactory, ExportReceipt, default_export_path
from .core.ledger import Ledger, process_cpu_seconds, process_rss_mb, probe_hardware
from .core.model import (
    MAX_LAYERS,
    ImageLayer,
    Layer,
    NoteDocument,
    NoteSummary,
    TextLayer,
    new_layer_id,
    slugify,
)
from .core.ocr import OCRCore, OCRResult
from .core.renderer import ImageCache, RenderReport, rasterize
from .core.shield import NetworkShield, ShieldReport
from .core.templates import build_template

RASTER_REASONS = frozenset(
    {
        "startup",
        "template",
        "open",
        "commit",
        "manual",
        "save",
        "export",
        "ocr_export",
    }
)

IDLE_REASONS = frozenset({"idle", "typing", "hover", "timer", "telemetry", "network"})

MAX_INGEST_SIDE = 2400

#: Mass manipulation is a single logical command: one raster pass at the end,
#: never one per touched node. These reasons are already inside the explicit
#: allow-list, so the idle freeze policy is not widened for the interceptor.
BATCH_REASON = "commit"


@dataclass
class RasterState:
    image: Image.Image | None = None
    report: RenderReport | None = None
    revision: int = 0
    reason: str = "startup"
    elapsed_ms: float = 0.0
    ops: int = 0
    suppressed: int = 0


@dataclass
class StudioSession:
    ledger: Ledger = field(default_factory=Ledger)
    shield: NetworkShield = field(default_factory=NetworkShield)
    ocr: OCRCore = field(default_factory=lambda: OCRCore(OCR_BUDGET_MS))
    factory: ExportFactory = field(init=False)
    document: NoteDocument = field(default_factory=lambda: build_template("formula"))
    raster: RasterState = field(default_factory=RasterState)
    selected_id: str | None = None
    selection: list[str] = field(default_factory=list)
    clipboard: ClipboardBuffer = field(default_factory=ClipboardBuffer)
    zoom: float = 1.0
    show_grid: bool = True
    show_ocr_boxes: bool = False
    last_ocr: OCRResult | None = None
    last_receipt: ExportReceipt | None = None
    hardware: object | None = None
    image_cache: ImageCache = field(default_factory=ImageCache)
    boot_cpu_seconds: float = field(default_factory=process_cpu_seconds)
    last_container: dict[str, object] | None = None
    container_probe: dict[str, object] | None = None

    def __post_init__(self) -> None:
        self.factory = ExportFactory(self.ledger)
        self.ledger.log("session", "studio session online", "INFO")

    # -- lifecycle ---------------------------------------------------------- #
    def new_document(self, template_key: str = "formula") -> NoteDocument:
        self.document = build_template(template_key)
        self.clear_selection()
        self.last_ocr = None
        self.rasterize("template")
        self.ledger.log("document", f"template applied · {template_key}", "INFO")
        return self.document

    def open_document(self, path: Path) -> NoteDocument:
        """Restore a note from its own PNG chunks, or import it as pixels."""
        document, report = container.load_document(path)
        self.document = document
        self.clear_selection()
        self.last_ocr = None
        self.last_container = {
            "mode": report.mode,
            "chunks": report.chunks,
            "tree_bytes": report.tree_bytes,
            "nodes": report.nodes,
            "path": path,
        }
        self.rasterize("open")
        self.ledger.log(
            "document",
            f"opened · {path.name} · {report.detail}",
            "WARN" if report.warnings else "INFO",
        )
        for warning in report.warnings:
            self.ledger.log("container", warning, "WARN")
        return document

    def adopt_document(self, document: NoteDocument, reason: str = "template") -> None:
        self.document = document
        self.clear_selection()
        self.last_ocr = None
        self.rasterize(reason)

    def save_document(self, path: Path, channel: str = "SAVE") -> Path:
        """Seal the live layer tree inside a universal ``.png`` container.

        The pixels are never re-rendered merely to save: the buffer already on the
        canvas is re-enclosed with the fresh tree in its open text chunks. A render
        happens only when that buffer is missing or was published at another scale,
        and ``"save"`` is an explicit keystroke cause, never an idle one.
        """
        target = container.force_container(path)
        self.document.touch()
        image = self.raster.image
        if image is None or image.size != (self.document.width, self.document.height):
            self.rasterize("save", 1.0)
            image = self.raster.image
        if image is None:
            raise container.ContainerError("no raster buffer to seal the note into")
        payload, report = container.embed_with_report(self.document, image)
        self.ledger.write_bytes(target, payload, channel)
        self.document.path = target
        self.last_container = {
            "mode": report.mode,
            "chunks": report.chunks,
            "tree_bytes": report.tree_bytes,
            "nodes": report.nodes,
            "path": target,
        }
        self.ledger.log(
            "document",
            f"saved · {target.name} · {report.detail}",
            "WRITE",
        )
        return target

    def save_path(self) -> Path:
        if self.document.path is not None:
            return container.force_container(self.document.path)
        return NOTES_DIR / f"{slugify(self.document.title)}.png"

    def verify_container_ingestion(self) -> dict[str, object]:
        """Prove the chunk codec round-trips the live tree, entirely in memory.

        Run once at boot before the ingestion milestone is allowed to print: the
        tree is packed, sliced, re-assembled and re-validated through the same
        integrity gates the Open path uses. No disk write, no raster pass.
        """
        outcome: dict[str, object] = {
            "ok": False,
            "nodes": 0,
            "restored": 0,
            "tree_bytes": 0,
            "packed_bytes": 0,
            "slices": 0,
            "chunks": 0,
            "detail": "probe did not run",
        }
        try:
            payload = container.compact_tree(self.document)
            slices = container.slice_tree(payload)
            block = dict(
                (key, value) for key, value, _utf8 in container.header_block(self.document, payload, slices)
            )
            raw, warnings = container.extract_tree(block)
            if not raw:
                outcome["detail"] = "integrity gate rejected the block: " + "; ".join(warnings or ["unknown"])
                return outcome
            restored = NoteDocument.from_json(raw.decode("utf-8"))
        except (container.ContainerError, ValueError, UnicodeDecodeError) as exc:
            outcome["detail"] = f"probe failed: {exc}"
            self.ledger.log("container", str(exc), "ERROR")
            return outcome
        outcome.update(
            {
                "ok": restored.node_count() == self.document.node_count(),
                "nodes": self.document.node_count(),
                "restored": restored.node_count(),
                "tree_bytes": len(payload),
                "packed_bytes": sum(len(chunk) for chunk in slices),
                "slices": len(slices),
                "chunks": len(block),
                "detail": (
                    f"{restored.node_count()}/{self.document.node_count()} nodes restored · "
                    f"{len(payload) / 1024.0:.1f} KiB tree · {len(slices)} slice(s) · "
                    f"{len(block)} chunks"
                ),
            }
        )
        self.container_probe = outcome
        self.ledger.log(
            "container",
            f"ingestion probe · {outcome['detail']}" if outcome["ok"] else f"ingestion probe FAILED · {outcome['detail']}",
            "INFO" if outcome["ok"] else "ERROR",
        )
        return outcome

    # -- selection ---------------------------------------------------------- #
    def select(self, layer_id: str | None) -> None:
        """Anchor the studio on one node; a plain click resets the mass set."""
        if not layer_id:
            self.clear_selection()
            return
        self.selection = [layer_id]
        self.selected_id = layer_id

    def select_many(self, layer_ids: Iterable[str]) -> int:
        """Install an explicit multi-selection (marquee, mass move, tests)."""
        ordered: list[str] = []
        for layer_id in layer_ids:
            if not layer_id or layer_id in ordered:
                continue
            if self.document.find(layer_id) is None:
                continue
            ordered.append(layer_id)
        self.selection = ordered
        self.selected_id = ordered[-1] if ordered else None
        return len(ordered)

    def toggle_selection(self, layer_id: str) -> int:
        """Shift-click semantics: add one node to, or drop it out of, the set."""
        self._prune_selection()
        if layer_id in self.selection:
            self.selection.remove(layer_id)
        elif self.document.find(layer_id) is not None:
            self.selection.append(layer_id)
        self.selected_id = self.selection[-1] if self.selection else None
        return len(self.selection)

    def select_all(self) -> int:
        """``Ctrl+A``: flag every unlocked vector and text node for mass edits."""
        self.selection = [layer.id for layer in self.document.layers if not layer.locked]
        self.selected_id = self.selection[-1] if self.selection else None
        return len(self.selection)

    def clear_selection(self) -> None:
        self.selection = []
        self.selected_id = None

    def _prune_selection(self) -> None:
        """Drop ids that no longer resolve, so the set can never go stale."""
        self.selection = [layer_id for layer_id in self.selection if self.document.find(layer_id) is not None]
        self.selected_id = self.selection[-1] if self.selection else None

    @property
    def selected_layer(self) -> Layer | None:
        return self.document.find(self.selected_id)

    @property
    def selection_layers(self) -> list[Layer]:
        """The mass set in document (paint) order."""
        self._prune_selection()
        wanted = set(self.selection)
        return [layer for layer in self.document.layers if layer.id in wanted]

    def add_layer(self, layer: Layer, reason: str = BATCH_REASON) -> Layer:
        self.document.clamp_layer(layer)
        self.document.add_layer(layer)
        self.select(layer.id)
        self.rasterize(reason)
        self.ledger.log("layer", f"added {layer.kind} · {layer.name}", "INFO")
        return layer

    # -- muscle-key interceptor --------------------------------------------- #
    def copy_selection(self) -> int:
        """``Ctrl+C``: snapshot the targeted nodes into the local array."""
        nodes = self.selection_layers
        if not nodes:
            self.ledger.log("clipboard", "copy ignored · nothing targeted", "WARN")
            return 0
        captured = self.clipboard.capture(nodes)
        if captured == 0:
            self.ledger.log("clipboard", "copy refused · every node failed validation", "ERROR")
            return 0
        self.ledger.log("clipboard", f"COPY · {captured} nodes · {self.clipboard.label()}", "INFO")
        return captured

    def cut_selection(self) -> int:
        """``Ctrl+X``: capture into the buffer, then purge the nodes from the tree."""
        nodes = self.selection_layers
        if not nodes:
            self.ledger.log("clipboard", "cut ignored · nothing targeted", "WARN")
            return 0
        captured = self.clipboard.capture(nodes)
        if captured == 0:
            self.ledger.log("clipboard", "cut refused · every node failed validation", "ERROR")
            return 0
        purged = 0
        for layer in nodes:
            if self.document.remove_layer(layer.id):
                purged += 1
        self.clear_selection()
        self.rasterize(BATCH_REASON)
        self.ledger.log("clipboard", f"CUT · {captured} buffered · {purged} purged from tree", "INFO")
        return captured

    def paste_clipboard(self) -> list[Layer]:
        """``Ctrl+V``: clone the buffer with a geometric offset onto the canvas."""
        clones = self.clipboard.harvest()
        if not clones:
            self.ledger.log("clipboard", "paste ignored · buffer empty", "WARN")
            return []
        placed: list[Layer] = []
        for layer in clones:
            if self.document.node_count() >= MAX_LAYERS:
                self.ledger.log("clipboard", f"paste truncated · layer ceiling {MAX_LAYERS} reached", "ERROR")
                break
            self.document.clamp_layer(layer)
            self.document.add_layer(layer)
            placed.append(layer)
        if not placed:
            self.ledger.log("clipboard", "paste refused · canvas is full", "ERROR")
            return []
        self.selection = [layer.id for layer in placed]
        self.selected_id = placed[-1].id
        self.rasterize(BATCH_REASON)
        self.ledger.log(
            "clipboard",
            f"PASTE · {len(placed)} clones · offset {self.clipboard.offset():.0f} px",
            "INFO",
        )
        return placed

    def delete_selection(self) -> int:
        """Purge the whole mass set in one commit (``Delete`` on a multi-select)."""
        nodes = self.selection_layers
        purged = 0
        for layer in nodes:
            if self.document.remove_layer(layer.id):
                purged += 1
        self.clear_selection()
        if purged:
            self.rasterize(BATCH_REASON)
            self.ledger.log("layer", f"{purged} nodes purged from tree", "INFO")
        return purged

    def move_selection(self, dx: float, dy: float, reason: str = BATCH_REASON) -> int:
        """Mass geometric translation of every targeted node, one raster only."""
        nodes = self.selection_layers
        if not nodes or (dx == 0.0 and dy == 0.0):
            return 0
        for layer in nodes:
            setattr(layer, "x", getattr(layer, "x", 0.0) + dx)
            setattr(layer, "y", getattr(layer, "y", 0.0) + dy)
            self.document.clamp_layer(layer)
        self.document.touch()
        self.rasterize(reason)
        return len(nodes)

    def apply_to_selection(self, attributes: dict[str, object], reason: str = BATCH_REASON) -> int:
        """Mass parameter change: write validated attributes across the set."""
        nodes = self.selection_layers
        if not nodes or not attributes:
            return 0
        touched = 0
        for layer in nodes:
            for name, value in attributes.items():
                if not hasattr(layer, name):
                    continue
                try:
                    setattr(layer, name, value)
                except (AttributeError, TypeError, ValueError):
                    continue
            touched += 1
        if touched:
            self.document.touch()
            self.rasterize(reason)
        return touched

    # -- single-node operations ---------------------------------------------- #
    def delete_selected(self) -> bool:
        if not self.selected_id and not self.selection:
            return False
        return self.delete_selection() > 0

    def duplicate_selected(self) -> Layer | None:
        if not self.selected_id:
            return None
        clone = self.document.duplicate_layer(self.selected_id)
        if clone is not None:
            self.document.clamp_layer(clone)
            self.select(clone.id)
            self.rasterize(BATCH_REASON)
            self.ledger.log("layer", f"duplicated · {clone.name}", "INFO")
        return clone

    def raise_selected(self) -> bool:
        changed = self.document.raise_layer(self.selected_id or "")
        if changed:
            self.rasterize(BATCH_REASON)
        return changed

    def lower_selected(self) -> bool:
        changed = self.document.lower_layer(self.selected_id or "")
        if changed:
            self.rasterize(BATCH_REASON)
        return changed

    def commit_geometry(self, layer: Layer, reason: str = BATCH_REASON) -> None:
        self.document.clamp_layer(layer)
        self.document.touch()
        self.rasterize(reason)

    def commit_properties(self, reason: str = BATCH_REASON) -> None:
        self.document.touch()
        self.rasterize(reason)

    # -- rasterisation policy ----------------------------------------------- #
    def rasterize(self, reason: str, scale: float = 1.0) -> bool:
        if reason in IDLE_REASONS or reason not in RASTER_REASONS:
            self.raster.suppressed += 1
            self.ledger.bump("raster.suppressed")
            self.ledger.log("raster", f"suppressed '{reason}' request (idle freeze)", "WARN")
            return False
        started = time.perf_counter()
        image, report = rasterize(self.document, scale)
        elapsed = (time.perf_counter() - started) * 1000.0
        self.raster = RasterState(
            image=image,
            report=report,
            revision=self.raster.revision + 1,
            reason=reason,
            elapsed_ms=elapsed,
            ops=self.raster.ops + 1,
            suppressed=self.raster.suppressed,
        )
        self.ledger.bump("raster.ops")
        self.ledger.counters["raster.last_ms"] = int(elapsed)
        self.ledger.mark_action()
        return True

    def active_image(self) -> Image.Image | None:
        return self.raster.image

    # -- OCR ---------------------------------------------------------------- #
    def run_ocr(self, force_pixels: bool = False) -> OCRResult:
        started = time.perf_counter()
        if force_pixels:
            result = self.ocr.analyze_document_pixels(self.document, 1.0)
        else:
            result = self.ocr.analyze(self.document)
        self.last_ocr = result
        self.show_ocr_boxes = True
        self.ledger.bump("ocr.runs")
        self.ledger.counters["ocr.last_us"] = int(result.latency_ms * 1000)
        self.ledger.log(
            "ocr",
            f"{result.mode} · {len(result.lines)} lines · {result.glyph_count} glyphs · "
            f"{result.latency_ms:.3f} ms",
            "INFO",
        )
        self.ledger.mark_action()
        return result

    def ocr_latency_us(self) -> float:
        return (self.last_ocr.latency_ms * 1000.0) if self.last_ocr else 0.0

    # -- export ------------------------------------------------------------- #
    def export_note(self, format_key: str, path: Path | None, scale: float = 1.0) -> ExportReceipt:
        target = path or default_export_path(self.document, f".{format_key}")
        receipt = self.factory.export(self.document, format_key, target, scale, "KHAI_HOA")
        self.last_receipt = receipt
        self.ledger.log(
            "export",
            f"KHAI HOA · {receipt.format_key.upper()} · {receipt.size_bytes / 1024.0:.1f} KiB · "
            f"{receipt.elapsed_ms:.1f} ms",
            "WRITE",
        )
        self.ledger.mark_action()
        return receipt

    def export_bundle(self, path: Path | None, scale: float = 1.0) -> ExportReceipt:
        target = path or (EXPORT_DIR / f"{slugify(self.document.title)}_bundle.zip")
        receipt = self.factory.write_bundle(self.document, target, scale, "ZIP")
        self.last_receipt = receipt
        self.ledger.log(
            "export",
            f"ZIP bundle · {len(receipt.artifacts)} nodes · {receipt.size_bytes / 1024.0:.1f} KiB",
            "WRITE",
        )
        self.ledger.mark_action()
        return receipt

    # -- image ingestion ---------------------------------------------------- #
    def ingest_image(self, path: Path) -> ImageLayer | None:
        try:
            with Image.open(path) as handle:
                handle.load()
                image = handle.convert("RGBA")
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            self.ledger.log("ingest", f"image rejected {path.name}: {exc}", "ERROR")
            return None
        if max(image.size) > MAX_INGEST_SIDE:
            ratio = MAX_INGEST_SIDE / max(image.size)
            image = image.resize(
                (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                Image.Resampling.LANCZOS,
            )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        payload = base64.b64encode(buffer.getvalue()).decode("ascii")
        aspect = image.width / max(1, image.height)
        box_w = min(self.document.width * 0.62, 420.0)
        box_h = min(self.document.height * 0.62, box_w / max(0.2, aspect))
        box_w = max(120.0, min(box_w, box_h * aspect))
        x = (self.document.width - box_w) / 2
        y = (self.document.height - box_h) / 2
        layer = ImageLayer(
            id=new_layer_id("image"),
            name=path.stem[:48] or "Image",
            x=round(x, 1),
            y=round(y, 1),
            w=round(box_w, 1),
            h=round(box_h, 1),
            payload=payload,
            source_name=path.name,
            fit="contain",
            radius=12,
            border="#DCE6F2",
            border_width=2,
        )
        self.ledger.bump("ingest.bytes", len(payload))
        self.ledger.log("ingest", f"{path.name} · {image.width}×{image.height} · {len(payload) / 1024:.0f} KiB", "INFO")
        return layer

    # -- derived state ------------------------------------------------------ #
    def summaries(self, limit: int = 12) -> list[NoteSummary]:
        return container.scan(NOTES_DIR, limit)

    def refresh_network(self) -> ShieldReport:
        report = self.shield.evaluate()
        self.ledger.log(
            "shield",
            f"{report.headline} · {report.reason}",
            "WARN" if report.armed else "INFO",
        )
        return report

    def snapshot(self) -> dict[str, object]:
        if self.hardware is None:
            self.hardware = probe_hardware()
        counts = self.document.shape_counts()
        return {
            "title": self.document.title,
            "nodes": self.document.node_count(),
            "counts": counts,
            "raster_ops": self.raster.ops,
            "raster_ms": self.raster.elapsed_ms,
            "raster_reason": self.raster.reason,
            "suppressed": self.raster.suppressed,
            "ocr": self.last_ocr,
            "cpu_seconds": process_cpu_seconds() - self.boot_cpu_seconds,
            "rss_mb": process_rss_mb(),
            "hardware": self.hardware,
            "writes": self.ledger.counters.get("io.writes", 0),
            "blocked": self.ledger.counters.get("io.blocked", 0),
            "shape_total": counts["line"] + counts["rect"] + counts["ellipse"],
            "selection": len(self.selection),
            "clipboard_depth": self.clipboard.depth(),
            "clipboard_bytes": self.clipboard.bytes_used,
            "clipboard_pastes": self.clipboard.pastes,
            "container": self.last_container,
            "container_probe": self.container_probe,
        }

    def remaining_budget(self, doc_pixels: int) -> bool:
        return doc_pixels <= OCR_MAX_SEGMENT_PIXELS

    def status_line(self) -> str:
        if len(self.selection) > 1:
            return f"mass set · {len(self.selection)} nodes · {self.document.title}"
        layer = self.selected_layer
        if layer is None:
            return f"{self.document.node_count()} nodes · {self.document.title}"
        return f"selected · {layer.name} · {layer.kind}"

    def text_layers(self) -> Iterable[TextLayer]:
        return self.document.text_layers()
