"""Localized application clipboard array for the muscle-key interceptor.

``Ctrl+C`` snapshots the binary properties, font metadata and pixel-buffer
coordinates of the targeted canvas nodes; ``Ctrl+V`` reads that array back,
rebuilds matching clones with fresh identifiers and applies a cascading
geometric offset; ``Ctrl+X`` reuses the same capture before the nodes are
purged from the shape tree.

The buffer is strictly in-process: nothing is written to the system clipboard,
nothing is serialised to disk and no socket is opened. Every payload is
validated twice — once when it is captured and once when it is materialised —
so a corrupted or oversized node can never reach the render stack.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .geometry import clamp
from .model import ImageLayer, Layer, ShapeLayer, TextLayer, layer_from_dict, new_layer_id

#: How many nodes the array can hold at once. A mass selection of the whole
#: canvas is bounded by ``MAX_LAYERS`` in the model, so this is the smaller,
#: deliberately defensive ceiling for the in-process buffer.
CLIPBOARD_CAPACITY = 64

#: Per-node and whole-buffer byte budgets. Base64 image payloads are the only
#: realistically large entry, so both limits are enforced on capture.
CLIPBOARD_ENTRY_BUDGET = 9 * 1024 * 1024
CLIPBOARD_TOTAL_BUDGET = 44 * 1024 * 1024

#: "Slight geometric pixel offset" of the contract, widened on every repeated
#: paste so a dozen pastes of one node do not stack into an invisible pile.
PASTE_OFFSET = 18.0
PASTE_CASCADE = 7.0
PASTE_OFFSET_MAX = 186.0

ALLOWED_KINDS = frozenset({"text", "shape", "image"})

MIN_COORDINATE = -4000.0
MAX_COORDINATE = 4000.0


def _coerce_coordinate(value: object, fallback: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    if number != number or number in (float("inf"), float("-inf")):
        return fallback
    return clamp(number, MIN_COORDINATE, MAX_COORDINATE)


def estimate_payload_bytes(payload: dict[str, Any]) -> int:
    """Deterministic accounting for one serialised node.

    ``sys.getsizeof`` would under-report the base64 image buffer, so the cost is
    derived from the actual field widths: string bodies by UTF-8 length, fixed
    scalars by their storage width.
    """
    total = 0
    for key, value in payload.items():
        total += len(str(key).encode("utf-8")) + 8
        if isinstance(value, str):
            total += len(value.encode("utf-8"))
        elif isinstance(value, (bytes, bytearray)):
            total += len(value)
        elif isinstance(value, bool):
            total += 1
        elif isinstance(value, (int, float)):
            total += 8
        elif isinstance(value, dict):
            total += estimate_payload_bytes(value)
    return total


def capture_coordinates(layer: Layer) -> tuple[float, float, float, float]:
    """Pixel-buffer coordinates of the node inside the document buffer."""
    x = _coerce_coordinate(getattr(layer, "x", 0.0))
    y = _coerce_coordinate(getattr(layer, "y", 0.0))
    w = _coerce_coordinate(getattr(layer, "w", 0.0))
    h = _coerce_coordinate(getattr(layer, "h", w))
    return x, y, w, h


def capture_font_metadata(layer: Layer) -> dict[str, Any]:
    """Typography attributes for text nodes, geometry matrix for the others."""
    if isinstance(layer, TextLayer):
        return {
            "family": str(layer.family)[:64],
            "size": int(clamp(float(layer.size), 6, 240)),
            "fill": layer.fill,
            "bold": bool(layer.bold),
            "italic": bool(layer.italic),
            "align": layer.align,
            "line_spacing": float(layer.line_spacing),
        }
    if isinstance(layer, ShapeLayer):
        return {
            "shape": layer.shape,
            "stroke": layer.stroke,
            "stroke_width": int(layer.stroke_width),
            "fill": layer.fill,
            "radius": int(layer.radius),
        }
    if isinstance(layer, ImageLayer):
        return {
            "source_name": str(layer.source_name)[:120],
            "fit": layer.fit,
            "border": layer.border,
            "border_width": int(layer.border_width),
            "radius": int(layer.radius),
        }
    return {}


@dataclass(slots=True)
class ClipboardEntry:
    """One validated snapshot of a canvas node, ready to be cloned."""

    payload: dict[str, Any]
    kind: str
    name: str
    source_id: str
    coordinates: tuple[float, float, float, float]
    metadata: dict[str, Any]
    size_bytes: int
    captured_at: float = field(default_factory=time.monotonic)

    @property
    def trace(self) -> str:
        x, y, w, h = self.coordinates
        return f"{self.kind} · {x:.0f},{y:.0f} · {w:.0f}×{h:.0f}"

    def materialise(self, dx: float, dy: float) -> Layer | None:
        """Rebuild a matching clone with a fresh id and the pasted offset.

        The payload is re-validated through the model factories, which re-clamp
        every colour, size and enumeration; a payload that no longer round-trips
        is dropped instead of being pushed into the shape tree.
        """
        candidate = dict(self.payload)
        candidate["id"] = new_layer_id(self.kind)
        candidate["name"] = self.name[:64] or self.kind
        candidate["x"] = _coerce_coordinate(candidate.get("x", self.coordinates[0])) + dx
        candidate["y"] = _coerce_coordinate(candidate.get("y", self.coordinates[1])) + dy
        layer = layer_from_dict(candidate)
        if layer is None or layer.kind != self.kind:
            return None
        layer.visible = bool(self.payload.get("visible", True))
        layer.locked = False
        return layer

    def describe(self) -> str:
        if self.kind == "text":
            family = str(self.metadata.get("family", "DejaVu Sans"))
            size = self.metadata.get("size", 24)
            return f"{self.name} · {family} {size}px"
        if self.kind == "shape":
            return f"{self.name} · {self.metadata.get('shape', 'rect')}"
        return f"{self.name} · {self.metadata.get('fit', 'contain')}"


@dataclass(slots=True)
class ClipboardBuffer:
    """The localized application clipboard array driven by the interceptor."""

    entries: list[ClipboardEntry] = field(default_factory=list)
    bytes_used: int = 0
    captures: int = 0
    pastes: int = 0
    rejected: int = 0

    # -- inspection --------------------------------------------------------- #
    def depth(self) -> int:
        return len(self.entries)

    def is_empty(self) -> bool:
        return not self.entries

    def kinds(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for entry in self.entries:
            tally[entry.kind] = tally.get(entry.kind, 0) + 1
        return tally

    def offset(self) -> float:
        """Current geometric paste offset, grown by the cascade generation."""
        return clamp(
            PASTE_OFFSET + PASTE_CASCADE * max(0, self.pastes - 1),
            PASTE_OFFSET,
            PASTE_OFFSET_MAX,
        )

    def metrics(self) -> dict[str, object]:
        return {
            "depth": self.depth(),
            "bytes": self.bytes_used,
            "captures": self.captures,
            "pastes": self.pastes,
            "rejected": self.rejected,
            "offset": self.offset(),
            "kinds": self.kinds(),
        }

    def label(self) -> str:
        if not self.entries:
            return "buffer empty"
        kinds = self.kinds()
        mixed = " · ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
        return f"{mixed} · {self.bytes_used // 1024} KiB"

    # -- capture ------------------------------------------------------------ #
    def clear(self) -> int:
        discarded = self.depth()
        self.entries = []
        self.bytes_used = 0
        self.pastes = 0
        return discarded

    def capture(self, layers: Iterable[Layer] | Sequence[Layer]) -> int:
        """Replace the array with validated snapshots of ``layers``.

        Returns the number of nodes accepted. Oversized, unknown-kind or
        non-round-tripping nodes are counted as rejected and skipped, so a
        partially invalid mass selection still copies the healthy remainder.
        """
        accepted: list[ClipboardEntry] = []
        total = 0
        for layer in layers or ():
            if layer is None or len(accepted) >= CLIPBOARD_CAPACITY:
                continue
            if getattr(layer, "kind", None) not in ALLOWED_KINDS:
                self.rejected += 1
                continue
            try:
                payload = layer.to_dict()
            except (AttributeError, TypeError, ValueError):
                self.rejected += 1
                continue
            if not isinstance(payload, dict):
                self.rejected += 1
                continue
            size = estimate_payload_bytes(payload)
            if size > CLIPBOARD_ENTRY_BUDGET or total + size > CLIPBOARD_TOTAL_BUDGET:
                self.rejected += 1
                continue
            if layer_from_dict(dict(payload)) is None:
                self.rejected += 1
                continue
            accepted.append(
                ClipboardEntry(
                    payload=payload,
                    kind=str(layer.kind),
                    name=str(layer.name)[:64],
                    source_id=str(layer.id)[:40],
                    coordinates=capture_coordinates(layer),
                    metadata=capture_font_metadata(layer),
                    size_bytes=size,
                )
            )
            total += size
        self.entries = accepted
        self.bytes_used = total
        self.pastes = 0
        if accepted:
            self.captures += 1
        return len(accepted)

    # -- harvest ------------------------------------------------------------ #
    def harvest(self) -> list[Layer]:
        """Clone every buffered node with the active geometric offset."""
        if not self.entries:
            return []
        shift = self.offset()
        self.pastes += 1
        cloned: list[Layer] = []
        for entry in self.entries:
            try:
                layer = entry.materialise(shift, shift)
            except (AttributeError, TypeError, ValueError):
                self.rejected += 1
                continue
            if layer is None:
                self.rejected += 1
                continue
            cloned.append(layer)
        if not cloned:
            self.pastes = max(0, self.pastes - 1)
        return cloned
