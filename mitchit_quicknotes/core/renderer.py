"""Pillow rasterisation engine.

The document is a stack of vector layers. This module converts that stack into
an RGBA bitmap, returning per-layer bounding boxes so the Qt canvas can hit-test
and draw selection handles without duplicating layout mathematics.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFilter

from .fonts import load_font
from .geometry import clamp, clamp_int
from .model import ImageLayer, Layer, NoteDocument, ShapeLayer, TextLayer


def rgba(color: str, opacity: float = 1.0) -> tuple[int, int, int, int]:
    """Parse ``#RRGGBB`` / ``#RRGGBBAA`` into a Pillow RGBA tuple."""
    text = (color or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        if len(text) == 6:
            r = int(text[0:2], 16)
            g = int(text[2:4], 16)
            b = int(text[4:6], 16)
            a = 255
        elif len(text) == 8:
            r = int(text[0:2], 16)
            g = int(text[2:4], 16)
            b = int(text[4:6], 16)
            a = int(text[6:8], 16)
        else:
            return (16, 22, 29, 255)
    except ValueError:
        return (16, 22, 29, 255)
    a = int(clamp(a * clamp(opacity, 0.0, 1.0), 0, 255))
    return (r, g, b, a)


def _is_transparent(color: str) -> bool:
    return rgba(color)[3] == 0


@dataclass(slots=True)
class RenderReport:
    width: int
    height: int
    scale: float
    elapsed_ms: float
    layer_boxes: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    drawn: int = 0
    skipped: int = 0
    raster_bytes: int = 0
    text_lines: int = 0
    image_decodes: int = 0

    def box_for(self, layer_id: str) -> tuple[float, float, float, float] | None:
        return self.layer_boxes.get(layer_id)


@lru_cache(maxsize=512)
def wrap_text(text: str, family: str, size: int, bold: bool, italic: bool, max_width: int) -> tuple[str, ...]:
    """Greedy word wrap honouring explicit newlines and single long tokens."""
    font = load_font(family, size, bold, italic)
    limit = max(12, int(max_width))
    lines: list[str] = []
    for paragraph in str(text).split("\n"):
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip() if current else word
            try:
                width = font.getlength(candidate)
            except (AttributeError, ValueError):
                width = len(candidate) * size * 0.55
            if width <= limit or not current:
                if width > limit and not current:
                    lines.extend(_break_token(word, font, limit, size))
                    current = ""
                else:
                    current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return tuple(lines) if lines else ("",)


def _break_token(token: str, font, limit: int, size: int) -> list[str]:
    pieces: list[str] = []
    buffer = ""
    for char in token:
        candidate = buffer + char
        try:
            width = font.getlength(candidate)
        except (AttributeError, ValueError):
            width = len(candidate) * size * 0.55
        if width > limit and buffer:
            pieces.append(buffer)
            buffer = char
        else:
            buffer = candidate
    if buffer:
        pieces.append(buffer)
    return pieces or [token]


def text_block(layer: TextLayer) -> tuple[tuple[str, ...], int, int]:
    """Return (lines, line_height, block_height) in document pixels."""
    font = load_font(layer.family, layer.size, layer.bold, layer.italic)
    lines = wrap_text(layer.text, layer.family, layer.size, layer.bold, layer.italic, int(layer.w))
    try:
        ascent, descent = font.getmetrics()
        base_height = ascent + descent
    except (AttributeError, ValueError):
        base_height = int(layer.size * 1.22)
    line_height = max(1, int(round(base_height * layer.line_spacing)))
    block_height = max(line_height, line_height * len(lines))
    return lines, line_height, block_height


def text_bounds(layer: TextLayer) -> tuple[float, float, float, float]:
    _, _, block_height = text_block(layer)
    return (layer.x, layer.y, layer.w, float(block_height))


class ImageCache:
    """Bounded in-memory decode cache for embedded raster layers."""

    def __init__(self, capacity: int = 24) -> None:
        self.capacity = capacity
        self._store: dict[str, Image.Image] = {}
        self._order: list[str] = []
        self.decodes = 0

    def get(self, layer: ImageLayer) -> Image.Image | None:
        key = f"{layer.id}:{len(layer.payload)}:{layer.source_name}"
        cached = self._store.get(key)
        if cached is not None:
            return cached
        raw = layer.decoded_bytes()
        if not raw:
            return None
        try:
            image = Image.open(BytesIO(raw))
            image.load()
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
        self.decodes += 1
        self._store[key] = image
        self._order.append(key)
        while len(self._order) > self.capacity:
            evicted = self._order.pop(0)
            self._store.pop(evicted, None)
        return image

    def clear(self) -> None:
        self._store.clear()
        self._order.clear()


_CACHE = ImageCache()


def _grid_background(size: tuple[int, int], spacing: int, color: str) -> Image.Image:
    tile = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile, "RGBA")
    step = clamp_int(spacing, 8, 200)
    dot = rgba(color, 0.55)
    for y in range(step, size[1], step):
        for x in range(step, size[0], step):
            draw.point((x, y), fill=dot)
    return tile


def _render_text(canvas: Image.Image, draw: ImageDraw.ImageDraw, layer: TextLayer, scale: float) -> tuple[float, float, float, float]:
    font = _font_for(layer, scale)
    lines, line_height, _ = text_block(layer)
    fill = rgba(layer.fill, layer.opacity)
    x = layer.x * scale
    y = layer.y * scale
    width = layer.w * scale
    line_height_s = line_height * scale
    for index, line in enumerate(lines):
        if not line:
            continue
        try:
            line_width = font.getlength(line) * scale
        except (AttributeError, ValueError):
            line_width = len(line) * layer.size * 0.55 * scale
        if layer.align == "center":
            offset = max(0.0, (width - line_width) / 2.0)
        elif layer.align == "right":
            offset = max(0.0, width - line_width)
        else:
            offset = 0.0
        draw.text(
            (x + offset, y + index * line_height_s),
            line,
            font=font,
            fill=fill,
            anchor="la",
        )
    block_height = line_height * len(lines)
    return (layer.x, layer.y, layer.w, float(block_height))


@lru_cache(maxsize=192)
def _scaled_font(layer_family: str, size: int, bold: bool, italic: bool, scale_key: int):
    return load_font(layer_family, max(6, int(round(size * scale_key / 1000.0))), bold, italic)


def _font_for(layer: TextLayer, scale: float):
    return _scaled_font(layer.family, layer.size, layer.bold, layer.italic, int(round(scale * 1000)))


def _render_shape(draw: ImageDraw.ImageDraw, layer: ShapeLayer, scale: float) -> tuple[float, float, float, float]:
    x = layer.x * scale
    y = layer.y * scale
    w = layer.w * scale
    h = layer.h * scale
    stroke = rgba(layer.stroke, layer.opacity)
    fill = None if _is_transparent(layer.fill) else rgba(layer.fill, layer.opacity)
    width = max(0, int(round(layer.stroke_width * scale)))
    if layer.shape == "line":
        if width <= 0:
            width = 1
        draw.line([(x, y), (x + w, y + h)], fill=stroke, width=width, joint="curve")
        return (layer.x, layer.y, layer.w, layer.h)
    if layer.shape == "ellipse":
        draw.ellipse([x, y, x + w, y + h], fill=fill, outline=stroke if width else None, width=width)
        return (layer.x, layer.y, layer.w, layer.h)
    radius = min(int(round(layer.radius * scale)), int(min(w, h) / 2)) if min(w, h) > 2 else 0
    if radius > 0:
        draw.rounded_rectangle(
            [x, y, x + w, y + h],
            radius=radius,
            fill=fill,
            outline=stroke if width else None,
            width=width,
        )
    else:
        draw.rectangle([x, y, x + w, y + h], fill=fill, outline=stroke if width else None, width=width)
    return (layer.x, layer.y, layer.w, layer.h)


def _fit_image(image: Image.Image, box_w: int, box_h: int, mode: str) -> Image.Image:
    box_w = max(1, box_w)
    box_h = max(1, box_h)
    if mode == "stretch":
        return image.resize((box_w, box_h), Image.Resampling.LANCZOS)
    src_ratio = image.width / max(1, image.height)
    box_ratio = box_w / max(1, box_h)
    if mode == "cover":
        if src_ratio > box_ratio:
            new_h = box_h
            new_w = int(round(box_h * src_ratio))
        else:
            new_w = box_w
            new_h = int(round(box_w / max(0.0001, src_ratio)))
        resized = image.resize((max(1, new_w), max(1, new_h)), Image.Resampling.LANCZOS)
        left = max(0, (resized.width - box_w) // 2)
        top = max(0, (resized.height - box_h) // 2)
        return resized.crop((left, top, left + box_w, top + box_h))
    scale = min(box_w / image.width, box_h / image.height)
    new_w = max(1, int(round(image.width * scale)))
    new_h = max(1, int(round(image.height * scale)))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    tile.alpha_composite(resized.convert("RGBA"), ((box_w - new_w) // 2, (box_h - new_h) // 2))
    return tile


def _render_image(canvas: Image.Image, layer: ImageLayer, scale: float) -> tuple[float, float, float, float]:
    source = _CACHE.get(layer)
    if source is None:
        return (layer.x, layer.y, layer.w, layer.h)
    box_w = max(1, int(round(layer.w * scale)))
    box_h = max(1, int(round(layer.h * scale)))
    mode = layer.fit if layer.fit in ("contain", "cover", "stretch") else "contain"
    tile = _fit_image(source.convert("RGBA"), box_w, box_h, mode)

    radius = max(0, int(round(layer.radius * scale)))
    if radius:
        mask = Image.new("L", (box_w, box_h), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=min(radius, min(box_w, box_h) // 2), fill=255)
        tile.putalpha(Image.composite(tile.getchannel("A"), Image.new("L", (box_w, box_h), 0), mask))

    border_width = max(0, int(round(layer.border_width * scale)))
    if border_width and not _is_transparent(layer.border):
        border_layer = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        border_draw = ImageDraw.Draw(border_layer, "RGBA")
        inset = border_width // 2
        border_draw.rounded_rectangle(
            [inset, inset, box_w - 1 - inset, box_h - 1 - inset],
            radius=max(0, min(radius, min(box_w, box_h) // 2)),
            outline=rgba(layer.border, layer.opacity),
            width=border_width,
        )
        tile.alpha_composite(border_layer)

    if layer.opacity < 1.0:
        alpha = tile.getchannel("A").point(lambda value: int(value * layer.opacity))
        tile.putalpha(alpha)

    canvas.alpha_composite(tile, (int(round(layer.x * scale)), int(round(layer.y * scale))))
    return (layer.x, layer.y, layer.w, layer.h)


def rasterize(document: NoteDocument, scale: float = 1.0) -> tuple[Image.Image, RenderReport]:
    """Rasterise the whole document at ``scale``. Pure function, no I/O."""
    started = time.perf_counter()
    scale = float(clamp(scale, 0.05, 8.0))
    width = max(1, int(round(document.width * scale)))
    height = max(1, int(round(document.height * scale)))
    canvas = Image.new("RGBA", (width, height), rgba(document.background))
    report = RenderReport(width=width, height=height, scale=scale, elapsed_ms=0.0)

    if document.meta.grid and document.meta.grid_spacing >= 8:
        canvas.alpha_composite(_grid_background((width, height), int(document.meta.grid_spacing * scale), document.meta.grid_color))

    draw = ImageDraw.Draw(canvas, "RGBA")
    for layer in document.layers:
        if not layer.visible or layer.opacity <= 0.001:
            report.skipped += 1
            continue
        try:
            if isinstance(layer, TextLayer):
                if not layer.text.strip():
                    report.skipped += 1
                    continue
                box = _render_text(canvas, draw, layer, scale)
                report.text_lines += len(wrap_text(layer.text, layer.family, layer.size, layer.bold, layer.italic, int(layer.w)))
            elif isinstance(layer, ShapeLayer):
                box = _render_shape(draw, layer, scale)
            elif isinstance(layer, ImageLayer):
                box = _render_image(canvas, layer, scale)
            else:
                report.skipped += 1
                continue
        except (OSError, ValueError, TypeError):
            report.skipped += 1
            continue
        report.layer_boxes[layer.id] = box
        report.drawn += 1

    if document.meta.footer_note.strip():
        _render_footer(canvas, document, scale)

    report.elapsed_ms = (time.perf_counter() - started) * 1000.0
    report.raster_bytes = width * height * 4
    report.image_decodes = _CACHE.decodes
    return canvas, report


def _render_footer(canvas: Image.Image, document: NoteDocument, scale: float) -> None:
    text = document.meta.footer_note.strip()
    if not text:
        return
    size = max(8, int(round(12 * scale)))
    font = load_font("DejaVu Sans", size, False, False)
    draw = ImageDraw.Draw(canvas, "RGBA")
    margin = int(round(22 * scale))
    try:
        width = font.getlength(text)
    except (AttributeError, ValueError):
        width = len(text) * size * 0.55
    draw.text(
        (canvas.width - margin - width, canvas.height - margin - size),
        text,
        font=font,
        fill=rgba(document.meta.accent, 0.72),
        anchor="la",
    )


def rasterize_preview(document: NoteDocument, max_width: int = 420) -> Image.Image:
    """Fast, small raster for thumbnails and carousel cards."""
    scale = clamp(max_width / max(1, document.width), 0.05, 1.0)
    image, _ = rasterize(document, scale)
    return image


def blur_backdrop(image: Image.Image, radius: int = 18) -> Image.Image:
    """Pillow fallback for the glass filter when Qt's native blur is absent."""
    return image.filter(ImageFilter.GaussianBlur(radius=max(0, radius)))


def boxes_for(document: NoteDocument, layer_ids: Iterable[str] | None = None) -> dict[str, tuple[float, float, float, float]]:
    wanted = set(layer_ids) if layer_ids is not None else None
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for layer in document.layers:
        if wanted is not None and layer.id not in wanted:
            continue
        if isinstance(layer, TextLayer):
            boxes[layer.id] = text_bounds(layer)
        else:
            boxes[layer.id] = (layer.x, layer.y, getattr(layer, "w", 0), getattr(layer, "h", 0))
    return boxes


def layer_box(layer: Layer) -> Sequence[float]:
    if isinstance(layer, TextLayer):
        return text_bounds(layer)
    return (
        getattr(layer, "x", 0.0),
        getattr(layer, "y", 0.0),
        getattr(layer, "w", 1.0),
        getattr(layer, "h", 1.0),
    )
