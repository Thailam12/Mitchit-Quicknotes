"""Universal serialization factory: PNG, JPG, ICO, native SVG and ZIP bundles.

Every byte leaves this module through ``Ledger.write_bytes`` on a sanctioned
channel, so the anti-cash-drain IO gate remains authoritative. All payloads are
assembled in memory first; a failed write can never leave a torn file behind.

The PNG writer additionally attaches the editable layer tree in the open text
chunks (see :mod:`core.container`), so a KHAI HỎA export is both a static image
anywhere and a re-openable note here. The JPG writer carries a compact EXIF
description block instead, because JPEG has no general text chunk namespace.
"""

from __future__ import annotations

import base64
import hashlib
import io
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape, quoteattr

from PIL import Image, PngImagePlugin

from .constants import (
    BUNDLE_ASSET_DIR,
    BUNDLE_CORE_NODE,
    BUNDLE_LAYER_DIR,
    BUNDLE_MANIFEST_NODE,
    BUNDLE_STATE_NODE,
    EXPORT_DIR,
    EXPORT_FORMAT_BY_KEY,
    PALETTE,
)
from .ledger import Ledger
from . import container
from .model import ImageLayer, NoteDocument, ShapeLayer, TextLayer, slugify
from .renderer import rasterize, rgba, text_block, wrap_text


class ExportError(RuntimeError):
    """Raised when a serialization target cannot be produced."""


@dataclass(slots=True)
class ExportReceipt:
    ok: bool
    format_key: str
    path: Path
    size_bytes: int
    elapsed_ms: float
    width: int
    height: int
    message: str
    artifacts: list[str] = field(default_factory=list)


def default_export_path(document: NoteDocument, extension: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return EXPORT_DIR / f"{slugify(document.title)}_{stamp}{extension}"


def _ensure_extension(path: Path, extension: str) -> Path:
    if path.suffix.lower() != extension.lower():
        return path.with_suffix(extension)
    return path


def _flatten(image: Image.Image, background: str) -> Image.Image:
    base = Image.new("RGB", image.size, rgba(background)[:3])
    if image.mode in ("RGBA", "LA"):
        base.paste(image.convert("RGBA"), (0, 0), image.convert("RGBA"))
    else:
        base.paste(image.convert("RGB"), (0, 0))
    return base


def _png_bytes(
    image: Image.Image,
    dpi: int = 144,
    info: PngImagePlugin.PngInfo | None = None,
) -> bytes:
    buffer = io.BytesIO()
    if info is None:
        image.save(buffer, format="PNG", optimize=True, dpi=(dpi, dpi))
    else:
        image.save(buffer, format="PNG", optimize=True, dpi=(dpi, dpi), pnginfo=info)
    return buffer.getvalue()


def _ascii_fold(text: str, limit: int = 220) -> str:
    """EXIF ASCII tags are Latin-1 at best, so diacritics are folded away."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(folded.split())[:limit]


def _jpg_bytes(image: Image.Image, background: str, description: str = "") -> bytes:
    buffer = io.BytesIO()
    exif: Image.Exif | None = None
    if description:
        exif = Image.Exif()
        exif[0x010E] = description          # ImageDescription
        exif[0x0131] = _ascii_fold(f"mitchit-quicknotes/{container.FORMAT_TOKEN}")  # Software
    _flatten(image, background).save(
        buffer,
        format="JPEG",
        quality=93,
        optimize=True,
        progressive=True,
        subsampling=1,
        exif=exif.tobytes() if exif is not None else None,
    )
    return buffer.getvalue()


def _ico_bytes(image: Image.Image, background: str, sizes: Iterable[int] = (16, 24, 32, 48, 64, 128, 256)) -> bytes:
    square = Image.new("RGBA", (256, 256), rgba(background))
    ratio = min(256 / max(1, image.width), 256 / max(1, image.height))
    target = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    resized = image.convert("RGBA").resize(target, Image.Resampling.LANCZOS)
    square.alpha_composite(resized, ((256 - target[0]) // 2, (256 - target[1]) // 2))
    buffer = io.BytesIO()
    square.save(buffer, format="ICO", sizes=[(size, size) for size in sizes])
    return buffer.getvalue()


def _svg_color(color: str, opacity: float = 1.0) -> tuple[str, float]:
    r, g, b, a = rgba(color, opacity)
    return f"#{r:02x}{g:02x}{b:02x}", a / 255.0


def _svg_image_href(payload: bytes) -> str:
    if payload.startswith(b"\x89PNG"):
        mime = "image/png"
    elif payload.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    elif payload.startswith(b"GIF8"):
        mime = "image/gif"
    elif payload.startswith(b"RIFF") and b"WEBP" in payload[:16]:
        mime = "image/webp"
    else:
        mime = "image/png"
    return f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")


def build_svg(document: NoteDocument) -> bytes:
    """Native vector serialization: raw geometric attributes, no rasterising."""
    width = document.width
    height = document.height
    background, background_alpha = _svg_color(document.background)
    parts: list[str] = []
    parts.append('<?xml version="1.0" encoding="UTF-8" standalone="no"?>')
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'data-generator="Mitchit Quicknotes Studio">'
    )
    parts.append(f"<title>{escape(document.title)}</title>")
    parts.append("<defs>")
    if document.meta.grid:
        spacing = max(8, int(document.meta.grid_spacing))
        grid_color, grid_alpha = _svg_color(document.meta.grid_color, 0.55)
        parts.append(
            f'<pattern id="mq-grid" width="{spacing}" height="{spacing}" patternUnits="userSpaceOnUse">'
            f'<circle cx="{spacing - 1}" cy="{spacing - 1}" r="1" fill="{grid_color}" '
            f'fill-opacity="{grid_alpha:.3f}"/></pattern>'
        )
    parts.append("</defs>")
    parts.append(
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="{background}" '
        f'fill-opacity="{background_alpha:.3f}"/>'
    )
    if document.meta.grid:
        parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="url(#mq-grid)"/>')

    for layer in document.layers:
        if not layer.visible or layer.opacity <= 0.001:
            continue
        opacity = f' opacity="{layer.opacity:.3f}"' if layer.opacity < 1.0 else ""
        if isinstance(layer, ShapeLayer):
            stroke_color, stroke_alpha = _svg_color(layer.stroke, layer.opacity)
            fill_color, fill_alpha = _svg_color(layer.fill, layer.opacity)
            stroke_attr = (
                f' stroke="{stroke_color}" stroke-width="{layer.stroke_width}" '
                f'stroke-opacity="{stroke_alpha:.3f}"' if layer.stroke_width > 0 else ' stroke="none"'
            )
            fill_attr = f'fill="{fill_color}" fill-opacity="{fill_alpha:.3f}"'
            if layer.shape == "line":
                stroke_attr_line = (
                    f' stroke="{stroke_color}" stroke-width="{max(1, layer.stroke_width)}" '
                    f'stroke-opacity="{stroke_alpha:.3f}" stroke-linecap="round"'
                )
                parts.append(
                    f'<line x1="{layer.x:.2f}" y1="{layer.y:.2f}" x2="{layer.x + layer.w:.2f}" '
                    f'y2="{layer.y + layer.h:.2f}"{stroke_attr_line}{opacity}/>'
                )
            elif layer.shape == "ellipse":
                parts.append(
                    f'<ellipse cx="{layer.x + layer.w / 2:.2f}" cy="{layer.y + layer.h / 2:.2f}" '
                    f'rx="{abs(layer.w) / 2:.2f}" ry="{abs(layer.h) / 2:.2f}" {fill_attr}{stroke_attr}{opacity}/>'
                )
            else:
                radius = min(layer.radius, int(min(abs(layer.w), abs(layer.h)) / 2)) if min(abs(layer.w), abs(layer.h)) > 2 else 0
                radius_attr = f' rx="{radius}" ry="{radius}"' if radius else ""
                parts.append(
                    f'<rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{abs(layer.w):.2f}" '
                    f'height="{abs(layer.h):.2f}"{radius_attr} {fill_attr}{stroke_attr}{opacity}/>'
                )
        elif isinstance(layer, TextLayer):
            fill_color, fill_alpha = _svg_color(layer.fill, layer.opacity)
            lines, line_height, _ = text_block(layer)
            anchor = {"left": "start", "center": "middle", "right": "end"}.get(layer.align, "start")
            if layer.align == "center":
                x = layer.x + layer.w / 2
            elif layer.align == "right":
                x = layer.x + layer.w
            else:
                x = layer.x
            weight = ' font-weight="bold"' if layer.bold else ""
            style = ' font-style="italic"' if layer.italic else ""
            parts.append(
                f'<g font-family={quoteattr(layer.family + ", DejaVu Sans, sans-serif")} '
                f'font-size="{layer.size}"{weight}{style} text-anchor="{anchor}" '
                f'fill="{fill_color}" fill-opacity="{fill_alpha:.3f}"{opacity}>'
            )
            for index, line in enumerate(lines):
                if not line:
                    continue
                baseline = layer.y + index * line_height + layer.size * 0.92
                parts.append(
                    f'<text x="{x:.2f}" y="{baseline:.2f}">{escape(line)}</text>'
                )
            parts.append("</g>")
        elif isinstance(layer, ImageLayer):
            payload = layer.decoded_bytes()
            if not payload:
                continue
            href = _svg_image_href(payload)
            parts.append(
                f'<image x="{layer.x:.2f}" y="{layer.y:.2f}" width="{abs(layer.w):.2f}" '
                f'height="{abs(layer.h):.2f}" preserveAspectRatio="xMidYMid meet" '
                f'xlink:href="{href}"{opacity}/>'
            )
    if document.meta.footer_note.strip():
        footer_color, footer_alpha = _svg_color(document.meta.accent, 0.72)
        parts.append(
            f'<text x="{width - 22}" y="{height - 22}" text-anchor="end" '
            f'font-family="DejaVu Sans, sans-serif" font-size="12" fill="{footer_color}" '
            f'fill-opacity="{footer_alpha:.3f}">{escape(document.meta.footer_note)}</text>'
        )
    parts.append("</svg>")
    payload = "\n".join(parts).encode("utf-8")
    _validate_svg(payload)
    return payload


def _validate_svg(payload: bytes) -> None:
    from xml.etree import ElementTree

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ExportError(f"generated SVG is not well formed: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ExportError("generated SVG root element is not <svg>")


@dataclass(frozen=True, slots=True)
class BundleEntry:
    path: str
    role: str
    description: str


class ExportFactory:
    """Strict single-responsibility serializer. One method per output format."""

    def __init__(self, ledger: Ledger | None = None) -> None:
        self.ledger = ledger or Ledger()

    # -- open-container hooks ---------------------------------------------- #
    def _tree_info(self, document: NoteDocument) -> PngImagePlugin.PngInfo | None:
        """Editable layer tree as PNG text chunks, or ``None`` after a warning.

        An export must never fail because metadata got oversized: the raster is
        the contract, the chunks are a bonus, so a codec refusal degrades to a
        flat image and is reported through the ledger instead of raising.
        """
        try:
            return container.pnginfo_for(document)
        except (container.ContainerError, ValueError, TypeError) as exc:
            self.ledger.log("container", f"png chunks omitted from export: {exc}", "WARN")
            return None

    def _description(self, document: NoteDocument) -> str:
        """One ASCII-folded EXIF line naming the note and its node census."""
        counts = document.shape_counts()
        shapes = counts["line"] + counts["rect"] + counts["ellipse"]
        return _ascii_fold(
            f"Mitchit Quicknotes {container.FORMAT_TOKEN} · title={document.title} · "
            f"nodes={document.node_count()} · shapes={shapes} · "
            f"buffer={document.width}x{document.height} · editable container is PNG"
        )

    # -- single-format writers -------------------------------------------- #
    def write_png(self, document: NoteDocument, path: Path, scale: float = 1.0, channel: str = "KHAI_HOA") -> ExportReceipt:
        image, report = rasterize(document, scale)
        payload = _png_bytes(image, dpi=int(144 * max(1.0, scale)), info=self._tree_info(document))
        return self._commit("png", path, payload, report.width, report.height, report.elapsed_ms, channel)

    def write_jpg(self, document: NoteDocument, path: Path, scale: float = 1.0, channel: str = "KHAI_HOA") -> ExportReceipt:
        image, report = rasterize(document, scale)
        payload = _jpg_bytes(image, document.background, self._description(document))
        return self._commit("jpg", path, payload, report.width, report.height, report.elapsed_ms, channel)

    def write_ico(self, document: NoteDocument, path: Path, scale: float = 1.0, channel: str = "KHAI_HOA") -> ExportReceipt:
        image, report = rasterize(document, 1.0)
        payload = _ico_bytes(image, document.background)
        return self._commit("ico", path, payload, 256, 256, report.elapsed_ms, channel)

    def write_svg(self, document: NoteDocument, path: Path, scale: float = 1.0, channel: str = "KHAI_HOA") -> ExportReceipt:
        started = time.perf_counter()
        payload = build_svg(document)
        elapsed = (time.perf_counter() - started) * 1000.0
        return self._commit("svg", path, payload, document.width, document.height, elapsed, channel)

    # -- dispatcher -------------------------------------------------------- #
    def export(
        self,
        document: NoteDocument,
        format_key: str,
        path: Path,
        scale: float = 1.0,
        channel: str = "KHAI_HOA",
    ) -> ExportReceipt:
        spec = EXPORT_FORMAT_BY_KEY.get(format_key)
        if spec is None:
            raise ExportError(f"unsupported format: {format_key!r}")
        target = _ensure_extension(path, spec.extension)
        writer = getattr(self, spec.writer, None)
        if writer is None:
            raise ExportError(f"writer missing for format {format_key!r}")
        effective_scale = scale if spec.supports_scale else 1.0
        receipt = writer(document, target, effective_scale, channel)
        return receipt

    def _commit(
        self,
        format_key: str,
        path: Path,
        payload: bytes,
        width: int,
        height: int,
        elapsed_ms: float,
        channel: str,
    ) -> ExportReceipt:
        size = self.ledger.write_bytes(path, payload, channel)
        return ExportReceipt(
            ok=True,
            format_key=format_key,
            path=path,
            size_bytes=size,
            elapsed_ms=elapsed_ms,
            width=width,
            height=height,
            message=f"{format_key.upper()} · {path.name}",
            artifacts=[path.name],
        )

    # -- ZIP compiler factory ---------------------------------------------- #
    def write_bundle(
        self,
        document: NoteDocument,
        path: Path,
        scale: float = 1.0,
        channel: str = "ZIP",
    ) -> ExportReceipt:
        """Bundle state + assets + vector twin around the ``core/index.html`` node."""
        started = time.perf_counter()
        target = _ensure_extension(path, ".zip")
        slug = slugify(document.title)
        image, report = rasterize(document, scale)
        png_payload = _png_bytes(image, dpi=int(144 * max(1.0, scale)), info=self._tree_info(document))
        thumb = image.copy()
        thumb.thumbnail((320, 320), Image.Resampling.LANCZOS)
        thumb_payload = _png_bytes(thumb)
        svg_payload = build_svg(document)
        document_payload = document.to_json().encode("utf-8")

        html_payload = _bundle_html(document, scale, report.width, report.height)
        layer_assets: list[tuple[str, bytes, str]] = []
        for index, layer in enumerate(document.layers):
            if not isinstance(layer, ImageLayer):
                continue
            raw = layer.decoded_bytes()
            if not raw:
                continue
            name = f"{index:02d}_{slugify(layer.name, 'layer')}.png"
            layer_assets.append((f"{BUNDLE_LAYER_DIR}/{name}", raw, layer.name))

        entries: list[BundleEntry] = [
            BundleEntry(BUNDLE_CORE_NODE, "core-node", "Primary presentation node; every asset path converges here."),
            BundleEntry(f"{BUNDLE_ASSET_DIR}/note.png", "raster", f"{report.width}×{report.height} flattened note raster."),
            BundleEntry(f"{BUNDLE_ASSET_DIR}/note.svg", "vector", "Native vector twin with raw geometric attributes."),
            BundleEntry(f"{BUNDLE_ASSET_DIR}/thumb.png", "preview", "Small preview raster for grids and manifests."),
            BundleEntry(BUNDLE_STATE_NODE, "state", "Full document state, replayable inside the studio."),
        ]
        for asset_path, _, label in layer_assets:
            entries.append(BundleEntry(asset_path, "layer-asset", f"Embedded raster layer: {label}."))
        entries.append(BundleEntry(BUNDLE_MANIFEST_NODE, "manifest", "SRP manifest: roles, relations, SHA-256 digests."))

        payloads: dict[str, bytes] = {
            BUNDLE_CORE_NODE: html_payload,
            f"{BUNDLE_ASSET_DIR}/note.png": png_payload,
            f"{BUNDLE_ASSET_DIR}/note.svg": svg_payload,
            f"{BUNDLE_ASSET_DIR}/thumb.png": thumb_payload,
            BUNDLE_STATE_NODE: document_payload,
        }
        for asset_path, raw, _ in layer_assets:
            payloads[asset_path] = raw
        payloads[BUNDLE_MANIFEST_NODE] = _bundle_manifest(document, entries, payloads, scale)

        buffer = io.BytesIO()
        try:
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for entry in entries:
                    data = payloads.get(entry.path)
                    if data is None:
                        continue
                    info = zipfile.ZipInfo(entry.path, date_time=time.localtime()[:6])
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    archive.writestr(info, data)
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            raise ExportError(f"bundle assembly failed: {exc}") from exc

        data = buffer.getvalue()
        size = self.ledger.write_bytes(target, data, channel)
        elapsed = (time.perf_counter() - started) * 1000.0
        return ExportReceipt(
            ok=True,
            format_key="zip",
            path=target,
            size_bytes=size,
            elapsed_ms=elapsed,
            width=report.width,
            height=report.height,
            message=f"ZIP · {len(entries)} nodes · {target.name}",
            artifacts=[entry.path for entry in entries],
        )


def _bundle_manifest(
    document: NoteDocument,
    entries: list[BundleEntry],
    payloads: dict[str, bytes],
    scale: float,
) -> bytes:
    import json

    files = []
    for entry in entries:
        data = payloads.get(entry.path, b"")
        files.append(
            {
                "path": entry.path,
                "role": entry.role,
                "description": entry.description,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    manifest = {
        "bundle": "mitchit-quicknotes-bundle/1",
        "core_node": BUNDLE_CORE_NODE,
        "title": document.title,
        "canvas": {"width": document.width, "height": document.height, "scale": scale},
        "counts": document.shape_counts(),
        "nodes": document.node_count(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "runtime_cost_vnd": 0,
        "relations": {
            entry.path: {"parent": BUNDLE_CORE_NODE, "role": entry.role} for entry in entries if entry.path != BUNDLE_CORE_NODE
        },
        "files": files,
    }
    return json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")


def _bundle_html(document: NoteDocument, scale: float, width: int, height: int) -> bytes:
    counts = document.shape_counts()
    title = escape(document.title)
    footer = escape(document.meta.footer_note or "Mitchit Quicknotes Studio")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="Mitchit Quicknotes Studio 1.0.0 RTM">
<meta name="runtime-cloud-cost" content="0 VND">
<title>{title} · Mitchit Quicknotes Bundle</title>
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
  background: radial-gradient(circle at 20% 0%, #16283a 0%, {PALETTE["void"]} 55%);
  color: {PALETTE["text"]}; font-family: "DejaVu Sans", system-ui, sans-serif; }}
main {{ width: min(1100px, 94vw); padding: 32px 0 48px; }}
header {{ display: flex; align-items: baseline; justify-content: space-between; gap: 24px; }}
h1 {{ margin: 0; font-size: 26px; letter-spacing: -0.02em; }}
.badge {{ font-size: 11px; letter-spacing: 0.16em; text-transform: uppercase;
  color: {PALETTE["mint"]}; border: 1px solid #1f4a3d; border-radius: 999px; padding: 6px 12px; }}
.stage {{ margin-top: 22px; border-radius: 18px; overflow: hidden;
  border: 1px solid {PALETTE["line_hi"]}; background: #060a0f;
  box-shadow: 0 30px 80px rgba(0,0,0,0.55); }}
.stage img {{ display: block; width: 100%; height: auto; }}
.meta {{ margin-top: 18px; display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }}
.card {{ border: 1px solid {PALETTE["line"]}; border-radius: 12px; padding: 12px 14px;
  background: {PALETTE["panel"]}; }}
.card span {{ display: block; font-size: 10px; letter-spacing: 0.14em;
  text-transform: uppercase; color: {PALETTE["text_faint"]}; }}
.card strong {{ font-size: 17px; font-variant-numeric: tabular-nums; }}
footer {{ margin-top: 22px; display: flex; justify-content: space-between;
  font-size: 12px; color: {PALETTE["text_dim"]}; }}
a {{ color: {PALETTE["accent"]}; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<main>
  <header>
    <h1>{title}</h1>
    <span class="badge">Zero-cloud bundle</span>
  </header>
  <div class="stage"><img src="../assets/note.png" alt="{title}" width="{width}" height="{height}"></div>
  <section class="meta">
    <div class="card"><span>Canvas</span><strong>{document.width}×{document.height}</strong></div>
    <div class="card"><span>Export scale</span><strong>{scale:.2f}×</strong></div>
    <div class="card"><span>Layer nodes</span><strong>{document.node_count()}</strong></div>
    <div class="card"><span>Shapes</span><strong>{counts["line"] + counts["rect"] + counts["ellipse"]}</strong></div>
    <div class="card"><span>Text blocks</span><strong>{counts["text"]}</strong></div>
    <div class="card"><span>Runtime cost</span><strong>0 VND</strong></div>
  </section>
  <footer>
    <span>{footer}</span>
    <span><a href="../assets/note.svg">Vector twin (.svg)</a> · <a href="../state/document.json">Document state</a></span>
  </footer>
</main>
</body>
</html>
"""
    return html.encode("utf-8")
