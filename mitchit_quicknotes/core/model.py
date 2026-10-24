"""Document model: vector layers, validation and open-container serialization.

All numeric input is sanitised through clamped coercion helpers, colours are
regex-validated, embedded raster payloads are size-capped and unknown keys are
discarded. Malformed trees therefore degrade to a safe document instead of
raising into the Qt event loop. The tree is handed to :mod:`core.container`,
which packs it into the text chunks of a universal PNG file.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .constants import DOC_H, DOC_W, DOCUMENT_SUFFIX, PALETTE, SLUG_RE
from .geometry import clamp, clamp_int, clamp_rect

COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
MAX_LAYERS = 220
MAX_TEXT_CHARS = 4000
MAX_EMBED_BYTES = 8 * 1024 * 1024
DOC_FORMAT = "mqopen/2"

TEXT_ALIGNMENTS = ("left", "center", "right")
SHAPE_KINDS = ("line", "rect", "ellipse")
FIT_MODES = ("contain", "cover", "stretch")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def local_stamp() -> str:
    return datetime.now().strftime("%d %b %Y · %H:%M")


def slugify(text: str, fallback: str = "quicknote") -> str:
    cleaned = SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    return (cleaned or fallback)[:48]


def new_layer_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def normalize_color(value: object, fallback: str) -> str:
    if isinstance(value, str) and COLOR_RE.match(value.strip()):
        text = value.strip()
        if len(text) == 4:
            text = "#" + "".join(ch * 2 for ch in text[1:])
        return text.lower()
    return fallback


def safe_number(value: object, fallback: float, low: float, high: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    if number != number or number in (float("inf"), float("-inf")):
        return fallback
    return clamp(number, low, high)


def safe_bool(value: object, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return fallback


def safe_choice(value: object, choices: Iterable[str], fallback: str) -> str:
    if isinstance(value, str) and value in choices:
        return value
    return fallback


@dataclass
class Layer:
    """Common vector layer contract."""

    id: str
    name: str
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0
    kind: str = "layer"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "visible": self.visible,
            "locked": self.locked,
            "opacity": round(self.opacity, 3),
        }

    @staticmethod
    def _base_from_dict(data: dict[str, Any], prefix: str) -> dict[str, Any]:
        return {
            "id": str(data.get("id") or new_layer_id(prefix))[:40],
            "name": str(data.get("name") or prefix.title())[:64],
            "visible": safe_bool(data.get("visible"), True),
            "locked": safe_bool(data.get("locked"), False),
            "opacity": safe_number(data.get("opacity"), 1.0, 0.0, 1.0),
        }


@dataclass
class TextLayer(Layer):
    x: float = 40.0
    y: float = 40.0
    w: float = 420.0
    text: str = ""
    family: str = "DejaVu Sans"
    size: int = 24
    fill: str = PALETTE["ink"]
    bold: bool = False
    italic: bool = False
    align: str = "left"
    line_spacing: float = 1.28
    kind: str = "text"

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload.update(
            {
                "x": round(self.x, 2),
                "y": round(self.y, 2),
                "w": round(self.w, 2),
                "text": self.text,
                "family": self.family,
                "size": self.size,
                "fill": self.fill,
                "bold": self.bold,
                "italic": self.italic,
                "align": self.align,
                "line_spacing": round(self.line_spacing, 3),
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TextLayer":
        base = Layer._base_from_dict(data, "text")
        return cls(
            **base,
            x=safe_number(data.get("x"), 40.0, -4000, 4000),
            y=safe_number(data.get("y"), 40.0, -4000, 4000),
            w=safe_number(data.get("w"), 420.0, 24, 4000),
            text=str(data.get("text") or "")[:MAX_TEXT_CHARS],
            family=str(data.get("family") or "DejaVu Sans")[:64],
            size=clamp_int(safe_number(data.get("size"), 24, 6, 240), 6, 240),
            fill=normalize_color(data.get("fill"), PALETTE["ink"]),
            bold=safe_bool(data.get("bold"), False),
            italic=safe_bool(data.get("italic"), False),
            align=safe_choice(data.get("align"), TEXT_ALIGNMENTS, "left"),
            line_spacing=safe_number(data.get("line_spacing"), 1.28, 0.8, 3.0),
        )


@dataclass
class ShapeLayer(Layer):
    shape: str = "rect"
    x: float = 120.0
    y: float = 120.0
    w: float = 220.0
    h: float = 140.0
    stroke: str = PALETTE["accent"]
    stroke_width: int = 3
    fill: str = "#ffffff00"
    radius: int = 18
    kind: str = "shape"

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload.update(
            {
                "shape": self.shape,
                "x": round(self.x, 2),
                "y": round(self.y, 2),
                "w": round(self.w, 2),
                "h": round(self.h, 2),
                "stroke": self.stroke,
                "stroke_width": self.stroke_width,
                "fill": self.fill,
                "radius": self.radius,
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShapeLayer":
        base = Layer._base_from_dict(data, "shape")
        return cls(
            **base,
            shape=safe_choice(data.get("shape"), SHAPE_KINDS, "rect"),
            x=safe_number(data.get("x"), 120.0, -4000, 4000),
            y=safe_number(data.get("y"), 120.0, -4000, 4000),
            w=safe_number(data.get("w"), 220.0, 0, 4000),
            h=safe_number(data.get("h"), 140.0, 0, 4000),
            stroke=normalize_color(data.get("stroke"), PALETTE["accent"]),
            stroke_width=clamp_int(safe_number(data.get("stroke_width"), 3, 0, 64), 0, 64),
            fill=normalize_color(data.get("fill"), "#ffffff00"),
            radius=clamp_int(safe_number(data.get("radius"), 18, 0, 200), 0, 200),
        )

    @property
    def is_line(self) -> bool:
        return self.shape == "line"


@dataclass
class ImageLayer(Layer):
    x: float = 120.0
    y: float = 120.0
    w: float = 260.0
    h: float = 180.0
    payload: str = ""
    source_name: str = "embedded.png"
    fit: str = "contain"
    radius: int = 14
    border: str = "#00000000"
    border_width: int = 0
    kind: str = "image"

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload.update(
            {
                "x": round(self.x, 2),
                "y": round(self.y, 2),
                "w": round(self.w, 2),
                "h": round(self.h, 2),
                "payload": self.payload,
                "source_name": self.source_name,
                "fit": self.fit,
                "radius": self.radius,
                "border": self.border,
                "border_width": self.border_width,
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ImageLayer":
        base = Layer._base_from_dict(data, "image")
        raw_payload = data.get("payload")
        payload = raw_payload if isinstance(raw_payload, str) else ""
        if len(payload) > MAX_EMBED_BYTES:
            payload = ""
        return cls(
            **base,
            x=safe_number(data.get("x"), 120.0, -4000, 4000),
            y=safe_number(data.get("y"), 120.0, -4000, 4000),
            w=safe_number(data.get("w"), 260.0, 8, 4000),
            h=safe_number(data.get("h"), 180.0, 8, 4000),
            payload=payload,
            source_name=str(data.get("source_name") or "embedded.png")[:120],
            fit=safe_choice(data.get("fit"), FIT_MODES, "contain"),
            radius=clamp_int(safe_number(data.get("radius"), 14, 0, 200), 0, 200),
            border=normalize_color(data.get("border"), "#00000000"),
            border_width=clamp_int(safe_number(data.get("border_width"), 0, 0, 32), 0, 32),
        )

    def decoded_bytes(self) -> bytes:
        if not self.payload:
            return b""
        try:
            return base64.b64decode(self.payload, validate=False)
        except (ValueError, TypeError):
            return b""


@dataclass(slots=True)
class DocumentMeta:
    template_id: str = "blank"
    accent: str = PALETTE["accent"]
    grid: bool = True
    grid_spacing: int = 32
    grid_color: str = "#d8e2ee"
    padding: int = 0
    footer_note: str = ""


@dataclass
class NoteDocument:
    title: str = "Untitled Note"
    width: int = DOC_W
    height: int = DOC_H
    background: str = PALETTE["paper"]
    layers: list[Layer] = field(default_factory=list)
    meta: DocumentMeta = field(default_factory=DocumentMeta)
    created_at: str = field(default_factory=now_iso)
    modified_at: str = field(default_factory=now_iso)
    path: Path | None = None

    # -- layer operations -------------------------------------------------- #
    def add_layer(self, layer: Layer) -> Layer:
        if len(self.layers) >= MAX_LAYERS:
            raise ValueError(f"layer ceiling reached ({MAX_LAYERS})")
        self.layers.append(layer)
        self.touch()
        return layer

    def remove_layer(self, layer_id: str) -> bool:
        for index, layer in enumerate(self.layers):
            if layer.id == layer_id:
                del self.layers[index]
                self.touch()
                return True
        return False

    def find(self, layer_id: str | None) -> Layer | None:
        if not layer_id:
            return None
        for layer in self.layers:
            if layer.id == layer_id:
                return layer
        return None

    def index_of(self, layer_id: str | None) -> int:
        if not layer_id:
            return -1
        for index, layer in enumerate(self.layers):
            if layer.id == layer_id:
                return index
        return -1

    def raise_layer(self, layer_id: str) -> bool:
        index = self.index_of(layer_id)
        if index < 0 or index == len(self.layers) - 1:
            return False
        self.layers[index], self.layers[index + 1] = self.layers[index + 1], self.layers[index]
        self.touch()
        return True

    def lower_layer(self, layer_id: str) -> bool:
        index = self.index_of(layer_id)
        if index <= 0:
            return False
        self.layers[index], self.layers[index - 1] = self.layers[index - 1], self.layers[index]
        self.touch()
        return True

    def duplicate_layer(self, layer_id: str) -> Layer | None:
        source = self.find(layer_id)
        if source is None:
            return None
        clone = type(source).from_dict(source.to_dict())
        clone.id = new_layer_id(clone.kind)
        clone.name = f"{source.name} copy"
        setattr(clone, "x", getattr(source, "x", 0) + 18)
        setattr(clone, "y", getattr(source, "y", 0) + 18)
        clone.opacity = source.opacity
        self.add_layer(clone)
        return clone

    def clamp_layer(self, layer: Layer) -> None:
        if isinstance(layer, TextLayer):
            layer.x = clamp(layer.x, -layer.w + 24, self.width - 24)
            layer.y = clamp(layer.y, -20, self.height - 20)
            layer.w = clamp(layer.w, 48, self.width)
            return
        x, y, w, h = clamp_rect(
            getattr(layer, "x", 0),
            getattr(layer, "y", 0),
            getattr(layer, "w", 1),
            getattr(layer, "h", 1),
            self.width,
            self.height,
        )
        setattr(layer, "x", x)
        setattr(layer, "y", y)
        setattr(layer, "w", w)
        setattr(layer, "h", h)

    def touch(self) -> None:
        self.modified_at = now_iso()

    # -- counters ---------------------------------------------------------- #
    def shape_counts(self) -> dict[str, int]:
        counts = {"text": 0, "line": 0, "rect": 0, "ellipse": 0, "image": 0}
        for layer in self.layers:
            if isinstance(layer, TextLayer):
                counts["text"] += 1
            elif isinstance(layer, ShapeLayer):
                counts[layer.shape] = counts.get(layer.shape, 0) + 1
            elif isinstance(layer, ImageLayer):
                counts["image"] += 1
        return counts

    def node_count(self) -> int:
        return len(self.layers)

    def text_layers(self) -> list[TextLayer]:
        return [layer for layer in self.layers if isinstance(layer, TextLayer)]

    def visible_layers(self) -> list[Layer]:
        return [layer for layer in self.layers if layer.visible]

    # -- serialization ----------------------------------------------------- #
    def to_dict(self) -> dict[str, Any]:
        return {
            "format": DOC_FORMAT,
            "title": self.title,
            "canvas": {
                "width": self.width,
                "height": self.height,
                "background": self.background,
            },
            "meta": {
                "template_id": self.meta.template_id,
                "accent": self.meta.accent,
                "grid": self.meta.grid,
                "grid_spacing": self.meta.grid_spacing,
                "grid_color": self.meta.grid_color,
                "footer_note": self.meta.footer_note,
            },
            "layers": [layer.to_dict() for layer in self.layers],
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NoteDocument":
        if not isinstance(data, dict):
            raise ValueError("document payload is not an object")
        canvas = data.get("canvas") if isinstance(data.get("canvas"), dict) else {}
        meta_raw = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        width = clamp_int(safe_number(canvas.get("width"), DOC_W, 240, 4096), 240, 4096)
        height = clamp_int(safe_number(canvas.get("height"), DOC_H, 240, 4096), 240, 4096)
        meta = DocumentMeta(
            template_id=str(meta_raw.get("template_id") or "blank")[:48],
            accent=normalize_color(meta_raw.get("accent"), PALETTE["accent"]),
            grid=safe_bool(meta_raw.get("grid"), True),
            grid_spacing=clamp_int(safe_number(meta_raw.get("grid_spacing"), 32, 8, 200), 8, 200),
            grid_color=normalize_color(meta_raw.get("grid_color"), "#d8e2ee"),
            footer_note=str(meta_raw.get("footer_note") or "")[:160],
        )
        document = cls(
            title=str(data.get("title") or "Untitled Note")[:96],
            width=width,
            height=height,
            background=normalize_color(canvas.get("background"), PALETTE["paper"]),
            layers=[],
            meta=meta,
            created_at=str(data.get("created_at") or now_iso()),
            modified_at=str(data.get("modified_at") or now_iso()),
        )
        raw_layers = data.get("layers")
        if isinstance(raw_layers, list):
            for entry in raw_layers[:MAX_LAYERS]:
                layer = layer_from_dict(entry)
                if layer is not None:
                    document.layers.append(layer)
        return document

    @classmethod
    def from_json(cls, text: str) -> "NoteDocument":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid note payload: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid note payload: root is not an object")
        return cls.from_dict(payload)

    def blank_copy(self, keep_title: bool = False) -> "NoteDocument":
        return NoteDocument(
            title=self.title if keep_title else "Untitled Note",
            width=self.width,
            height=self.height,
            background=self.background,
            meta=DocumentMeta(
                template_id=self.meta.template_id,
                accent=self.meta.accent,
                grid=self.meta.grid,
                grid_spacing=self.meta.grid_spacing,
                grid_color=self.meta.grid_color,
                footer_note=self.meta.footer_note,
            ),
        )

    @property
    def display_name(self) -> str:
        if self.path is not None:
            return self.path.name
        return f"{slugify(self.title)}{DOCUMENT_SUFFIX}"


def layer_from_dict(data: object) -> Layer | None:
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    try:
        if kind == "text":
            return TextLayer.from_dict(data)
        if kind == "shape":
            return ShapeLayer.from_dict(data)
        if kind == "image":
            return ImageLayer.from_dict(data)
    except (TypeError, ValueError, KeyError):
        return None
    return None


@dataclass(frozen=True, slots=True)
class NoteSummary:
    path: Path
    title: str
    modified: float
    nodes: int
    shapes: int
    size_bytes: int
    accent: str
    tree_bytes: int = 0

    @property
    def stamp(self) -> str:
        return datetime.fromtimestamp(self.modified).strftime("%d %b · %H:%M")

    @property
    def size_label(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        if self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024.0:.0f} KiB"
        return f"{self.size_bytes / (1024 * 1024):.1f} MiB"

    @property
    def editable(self) -> bool:
        """True when the container carries the layer tree, not only pixels."""
        return self.tree_bytes > 0

    @property
    def tree_label(self) -> str:
        if self.tree_bytes <= 0:
            return "flat raster"
        if self.tree_bytes < 1024:
            return f"{self.tree_bytes} B tree"
        return f"{self.tree_bytes / 1024.0:.1f} KiB tree"
