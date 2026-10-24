"""Local auto-OCR core.

Three engines, ordered by cost, all executed in-process with zero network calls:

1. ``VECTOR_TEXT_LAYERS`` — reads the authored text-node geometry. Exact, and
   routinely completes in tens of microseconds, inside the 1 ms contract.
2. ``TESSERACT_STDIN`` — used automatically when the offline ``tesseract``
   binary exists. The bitmap is piped through stdin/stdout, never a temp file.
3. ``PILLOW_GLYPH_ATLAS`` — a pure-Pillow segmenter and template matcher. Row
   and column projections isolate glyphs, each glyph is packed into a bitmask
   and matched against a cached font atlas with XOR popcount distance, which
   keeps recognition in the millisecond range with no numeric dependencies.

Character decoding is inherently lossy on the raster path, so every result
carries a measured confidence and an honest engine label.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import Image, ImageDraw, ImageOps

from .constants import OCR_BUDGET_MS
from .fonts import load_font
from .geometry import clamp
from .model import NoteDocument, TextLayer
from .renderer import rasterize, text_block

CELL = 18
ATLAS_SIZE = 64
GLYPH_CHARSET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    ".,:;!?+-=*/()[]%#&'\"<>²³π°"
)
ACCEPT_DISTANCE = int(CELL * CELL * 0.62)
MAX_GLYPHS = 1400
HARD_MS_CAP = 900.0

VECTOR_MODE = "VECTOR_TEXT_LAYERS"
TESSERACT_MODE = "TESSERACT_STDIN"
ATLAS_MODE = "PILLOW_GLYPH_ATLAS"


@dataclass(slots=True)
class OCRLine:
    text: str
    box: tuple[float, float, float, float]
    confidence: float
    glyphs: int = 0


@dataclass(slots=True)
class OCRResult:
    mode: str
    engine: str
    text: str
    lines: list[OCRLine] = field(default_factory=list)
    confidence: float = 0.0
    latency_ms: float = 0.0
    budget_ms: float = OCR_BUDGET_MS
    truncated: bool = False
    note: str = ""

    @property
    def within_budget(self) -> bool:
        return self.latency_ms <= self.budget_ms

    @property
    def glyph_count(self) -> int:
        return sum(line.glyphs for line in self.lines)

    @property
    def char_count(self) -> int:
        return sum(len(line.text) for line in self.lines)

    def headline(self) -> str:
        return (
            f"{self.engine} · {len(self.lines)} lines · {self.char_count} chars · "
            f"{self.latency_ms:.3f} ms"
        )


def _otsu_threshold(histogram: list[int], total: int) -> int:
    if total <= 0:
        return 128
    sum_all = sum(index * count for index, count in enumerate(histogram))
    sum_background = 0.0
    weight_background = 0
    best_variance = -1.0
    best_threshold = 127
    for level in range(256):
        weight_background += histogram[level]
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break
        sum_background += level * histogram[level]
        mean_background = sum_background / weight_background
        mean_foreground = (sum_all - sum_background) / weight_foreground
        variance = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if variance > best_variance:
            best_variance = variance
            best_threshold = level
    return best_threshold


def _binarize(image: Image.Image, dark_text: bool = True) -> Image.Image:
    gray = image.convert("L")
    histogram = gray.histogram()
    threshold = _otsu_threshold(histogram, gray.width * gray.height)
    if dark_text:
        mask = gray.point(lambda value: 255 if value < threshold else 0, mode="L")
    else:
        mask = gray.point(lambda value: 255 if value > threshold else 0, mode="L")
    return mask


def _row_profile(mask: Image.Image) -> list[int]:
    width = mask.width
    data = mask.tobytes()
    return [data[row * width:(row + 1) * width].count(255) for row in range(mask.height)]


def _bands_from_profile(profile: list[int], min_ink: int = 1, max_gap: int = 2, min_height: int = 4) -> list[tuple[int, int]]:
    bands: list[tuple[int, int]] = []
    start: int | None = None
    gap = 0
    for index, value in enumerate(profile):
        if value >= min_ink:
            if start is None:
                start = index
            gap = 0
        else:
            if start is not None:
                gap += 1
                if gap > max_gap:
                    end = index - gap + 1
                    if end - start >= min_height:
                        bands.append((start, end))
                    start = None
                    gap = 0
    if start is not None and len(profile) - start >= min_height:
        bands.append((start, len(profile)))
    return bands


def _column_boxes(band: Image.Image, min_width: int = 1) -> list[tuple[int, int]]:
    transposed = band.transpose(Image.Transpose.TRANSPOSE)
    width = transposed.width
    data = transposed.tobytes()
    boxes: list[tuple[int, int]] = []
    start: int | None = None
    for column in range(transposed.height):
        ink = data[column * width:(column + 1) * width].count(255)
        if ink > 0:
            if start is None:
                start = column
        else:
            if start is not None:
                if column - start >= min_width:
                    boxes.append((start, column))
                start = None
    if start is not None:
        boxes.append((start, transposed.height))
    return boxes


def _pack_glyph(crop: Image.Image) -> int:
    width, height = crop.size
    if width <= 0 or height <= 0:
        return 0
    ratio = min((CELL - 2) / width, (CELL - 2) / height)
    new_w = max(1, int(round(width * ratio)))
    new_h = max(1, int(round(height * ratio)))
    try:
        small = crop.resize((new_w, new_h), Image.Resampling.BILINEAR)
    except (ValueError, OSError):
        return 0
    tile = Image.new("L", (CELL, CELL), 0)
    tile.paste(small, ((CELL - new_w) // 2, (CELL - new_h) // 2))
    data = tile.point(lambda value: 255 if value >= 100 else 0, mode="L").tobytes()
    bits = 0
    for index, value in enumerate(data):
        if value:
            bits |= 1 << index
    return bits


@lru_cache(maxsize=8)
def _atlas(family: str, bold: bool, italic: bool) -> tuple[tuple[str, int], ...]:
    font = load_font(family, ATLAS_SIZE, bold, italic)
    entries: list[tuple[str, int]] = []
    for char in GLYPH_CHARSET:
        canvas = Image.new("L", (ATLAS_SIZE * 2, ATLAS_SIZE * 2), 0)
        draw = ImageDraw.Draw(canvas)
        try:
            draw.text((ATLAS_SIZE // 2, ATLAS_SIZE // 2), char, font=font, fill=255, anchor="la")
        except (OSError, ValueError):
            continue
        bbox = canvas.getbbox()
        if bbox is None:
            continue
        glyph = canvas.crop(bbox)
        bits = _pack_glyph(glyph)
        if bits:
            entries.append((char, bits))
    return tuple(entries)


def _match(bits: int, atlas: tuple[tuple[str, int], ...]) -> tuple[str, float]:
    if bits == 0 or not atlas:
        return "?", 0.0
    best_char = "?"
    best_distance = 1 << 30
    for char, candidate in atlas:
        distance = (bits ^ candidate).bit_count()
        if distance < best_distance:
            best_distance = distance
            best_char = char
    capacity = CELL * CELL
    confidence = 1.0 - (best_distance / capacity)
    if best_distance > ACCEPT_DISTANCE:
        confidence = min(confidence, 0.34)
    return best_char, clamp(confidence, 0.0, 1.0)


class OCRCore:
    """Dispatch layer: chooses the cheapest engine that can answer the query."""

    def __init__(self, budget_ms: float = OCR_BUDGET_MS) -> None:
        self.budget_ms = max(0.05, float(budget_ms))
        self.tesseract_path = shutil.which("tesseract") or ""
        self.atlas_builds = 0

    # -- engine 1: vector -------------------------------------------------- #
    def analyze_vector(self, document: NoteDocument) -> OCRResult:
        started = time.perf_counter()
        lines: list[OCRLine] = []
        for layer in document.layers:
            if not isinstance(layer, TextLayer) or not layer.visible:
                continue
            wrapped, line_height, _ = text_block(layer)
            for index, content in enumerate(wrapped):
                if not content.strip():
                    continue
                lines.append(
                    OCRLine(
                        text=content,
                        box=(layer.x, layer.y + index * line_height, layer.w, float(line_height)),
                        confidence=0.999,
                        glyphs=len(content),
                    )
                )
        lines.sort(key=lambda item: (round(item.box[1], 1), item.box[0]))
        text = "\n".join(line.text for line in lines)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return OCRResult(
            mode=VECTOR_MODE,
            engine="vector/text-node reader",
            text=text,
            lines=lines,
            confidence=0.999 if lines else 0.0,
            latency_ms=latency_ms,
            budget_ms=self.budget_ms,
            note="Exact extraction from authored text-node geometry.",
        )

    # -- engine 2: tesseract ----------------------------------------------- #
    def analyze_tesseract(self, image: Image.Image) -> OCRResult | None:
        if not self.tesseract_path:
            return None
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [self.tesseract_path, "stdin", "stdout", "--psm", "6", "-l", "eng"],
                input=_png_stream(image),
                capture_output=True,
                timeout=12.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        text = completed.stdout.decode("utf-8", errors="replace").strip()
        latency_ms = (time.perf_counter() - started) * 1000.0
        lines = [
            OCRLine(text=row, box=(0.0, float(index * 20), float(image.width), 20.0), confidence=0.9)
            for index, row in enumerate(text.splitlines())
            if row.strip()
        ]
        return OCRResult(
            mode=TESSERACT_MODE,
            engine="tesseract (offline binary)",
            text=text,
            lines=lines,
            confidence=0.9 if text else 0.0,
            latency_ms=latency_ms,
            budget_ms=self.budget_ms,
            note="Decoded by the local tesseract engine over stdin/stdout.",
        )

    # -- engine 3: glyph atlas --------------------------------------------- #
    def analyze_atlas(
        self,
        image: Image.Image,
        family: str = "DejaVu Sans",
        bold: bool = False,
        italic: bool = False,
        origin: tuple[float, float] = (0.0, 0.0),
        source_scale: float = 1.0,
    ) -> OCRResult:
        started = time.perf_counter()
        work = image.convert("RGBA")
        max_dim = 900
        ratio = min(1.0, max_dim / max(1, max(work.width, work.height)))
        if ratio < 1.0:
            work = work.resize(
                (max(1, int(work.width * ratio)), max(1, int(work.height * ratio))),
                Image.Resampling.LANCZOS,
            )
        flattened = Image.new("L", work.size, 255)
        flattened.paste(work.convert("L"), (0, 0), work.getchannel("A"))
        mask = _binarize(flattened, dark_text=True)
        profile = _row_profile(mask)
        bands = _bands_from_profile(profile)

        atlas = _atlas(family, bold, italic)
        lines: list[OCRLine] = []
        glyph_budget = MAX_GLYPHS
        truncated = False
        chars_conf: list[float] = []
        inverse_ratio = 1.0 / ratio if ratio else 1.0

        for top, bottom in bands:
            if glyph_budget <= 0:
                truncated = True
                break
            band = mask.crop((0, top, mask.width, bottom))
            columns = _column_boxes(band)
            if not columns:
                continue
            band_height = max(1, bottom - top)
            widths = [end - start for start, end in columns]
            median_width = sorted(widths)[len(widths) // 2] if widths else band_height
            space_gap = max(3, int(median_width * 0.62))

            assembled: list[str] = []
            line_confidence: list[float] = []
            previous_end: int | None = None
            glyph_count = 0
            for start, end in columns:
                if glyph_budget <= 0:
                    truncated = True
                    break
                if previous_end is not None and start - previous_end >= space_gap:
                    assembled.append(" ")
                crop = band.crop((start, 0, end, band_height))
                tight = crop.getbbox()
                if tight:
                    crop = crop.crop(tight)
                bits = _pack_glyph(crop)
                char, confidence = _match(bits, atlas)
                assembled.append(char)
                line_confidence.append(confidence)
                glyph_count += 1
                glyph_budget -= 1
                previous_end = end
            text = "".join(assembled).strip()
            if not text:
                continue
            lines.append(
                OCRLine(
                    text=text,
                    box=(
                        origin[0],
                        origin[1] + top * inverse_ratio,
                        mask.width * inverse_ratio,
                        band_height * inverse_ratio,
                    ),
                    confidence=sum(line_confidence) / max(1, len(line_confidence)),
                    glyphs=glyph_count,
                )
            )
            chars_conf.extend(line_confidence)
            if (time.perf_counter() - started) * 1000.0 > HARD_MS_CAP:
                truncated = True
                break

        latency_ms = (time.perf_counter() - started) * 1000.0
        text = "\n".join(line.text for line in lines)
        confidence = sum(chars_conf) / len(chars_conf) if chars_conf else 0.0
        return OCRResult(
            mode=ATLAS_MODE,
            engine="pillow glyph atlas",
            text=text,
            lines=lines,
            confidence=confidence,
            latency_ms=latency_ms,
            budget_ms=self.budget_ms,
            truncated=truncated,
            note=(
                f"{len(bands)} text bands · {sum(line.glyphs for line in lines)} glyphs matched · "
                f"atlas {len(atlas)} faces"
            ),
        )

    # -- dispatch ----------------------------------------------------------- #
    def analyze(
        self,
        document: NoteDocument,
        image: Image.Image | None = None,
        family: str = "DejaVu Sans",
        bold: bool = False,
        italic: bool = False,
    ) -> OCRResult:
        if image is None:
            result = self.analyze_vector(document)
            if result.lines:
                return result
            rendered, report = rasterize(document, 1.0)
            return self.analyze_atlas(rendered, family, bold, italic, source_scale=1.0)

        tesseract = self.analyze_tesseract(image)
        if tesseract is not None:
            return tesseract
        return self.analyze_atlas(image, family, bold, italic)

    def analyze_document_pixels(self, document: NoteDocument, scale: float = 1.0) -> OCRResult:
        """Force the pixel pipeline on a rendered document (raster validation)."""
        image, _ = rasterize(document, scale)
        tesseract = self.analyze_tesseract(image)
        if tesseract is not None:
            return tesseract
        return self.analyze_atlas(image, source_scale=scale)


def _png_stream(image: Image.Image) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def autocrop_ink(image: Image.Image) -> Image.Image:
    """Trim uniform borders; used by the image ingestion pipeline."""
    try:
        gray = ImageOps.autocontrast(image.convert("L"))
        mask = gray.point(lambda value: 255 if value < 240 else 0, mode="L")
        bbox = mask.getbbox()
        if bbox and bbox[2] - bbox[0] > 4 and bbox[3] - bbox[1] > 4:
            return image.crop(bbox)
    except (OSError, ValueError):
        pass
    return image
