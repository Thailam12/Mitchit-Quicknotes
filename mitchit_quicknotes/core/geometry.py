"""Geometry containment mathematics.

The 1280x720 viewport is frozen. Every floating surface, overlay card, carousel
scroll anchor and layer drag offset is funnelled through the clamp loops in this
module so that no widget can ever escape the presentation boundary, at any
layout density between 50% and 200%.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .constants import (
    DENSITY_MAX,
    DENSITY_MIN,
    VIEWPORT_H,
    VIEWPORT_W,
    ZOOM_MAX,
    ZOOM_MIN,
)


def clamp(value: float, low: float, high: float) -> float:
    """Clamp a scalar; guarantees ``low <= result <= high`` for ``low <= high``."""
    if high < low:
        low, high = high, low
    return max(low, min(value, high))


def clamp_int(value: float, low: int, high: int) -> int:
    if high < low:
        low, high = high, low
    return int(max(low, min(int(round(value)), high)))


def clamp_axis(current_x: float, widget_width: float, viewport: float = VIEWPORT_W) -> int:
    """Containment clamp for one axis of a floating surface.

    Mirrors the contractual loop:
        x = max(0, min(current_x, 1280 - widget_width))
    """
    current_x = int(round(current_x))
    widget_width = int(round(widget_width))
    x = max(0, min(current_x, viewport - widget_width))
    return x


def clamp_position(
    x: float,
    y: float,
    widget_w: float,
    widget_h: float,
    viewport_w: int = VIEWPORT_W,
    viewport_h: int = VIEWPORT_H,
) -> tuple[int, int]:
    """Contain a floating rectangle (x, y, w, h) inside the frozen viewport."""
    widget_w = clamp_int(widget_w, 1, viewport_w)
    widget_h = clamp_int(widget_h, 1, viewport_h)
    x = max(0, min(int(round(x)), viewport_w - widget_w))
    y = max(0, min(int(round(y)), viewport_h - widget_h))
    return x, y


def clamp_rect(
    x: float,
    y: float,
    w: float,
    h: float,
    bounds_w: float,
    bounds_h: float,
) -> tuple[int, int, int, int]:
    """Contain a rectangle inside an arbitrary bounds box (document space).

    The rectangle keeps at least ``MIN_VISIBLE`` pixels inside the bounds so a
    dragged layer can never be lost outside the canvas buffer.
    """
    bounds_w = max(1, int(round(bounds_w)))
    bounds_h = max(1, int(round(bounds_h)))
    w_i = clamp_int(w, 1, bounds_w)
    h_i = clamp_int(h, 1, bounds_h)
    min_visible_x = min(24, w_i)
    min_visible_y = min(24, h_i)
    x_i = clamp_int(x, -w_i + min_visible_x, bounds_w - min_visible_x)
    y_i = clamp_int(y, -h_i + min_visible_y, bounds_h - min_visible_y)
    return x_i, y_i, w_i, h_i


def clamp_scale(value: float, low: float = DENSITY_MIN, high: float = DENSITY_MAX) -> float:
    """Quantise a layout density to the sanctioned 50%–200% ladder."""
    return clamp(round(value, 4), low, high)


def clamp_zoom(value: float) -> float:
    return clamp(round(value, 4), ZOOM_MIN, ZOOM_MAX)


def zoom_percent(value: float) -> int:
    return int(round(clamp_zoom(value) * 100))


def fit_scale(
    doc_w: float,
    doc_h: float,
    view_w: float,
    view_h: float,
    padding: float = 0.0,
) -> float:
    """Largest uniform scale that fits the document inside the viewport."""
    avail_w = max(1.0, view_w - padding * 2)
    avail_h = max(1.0, view_h - padding * 2)
    if doc_w <= 0 or doc_h <= 0:
        return 1.0
    return min(avail_w / doc_w, avail_h / doc_h)


def contains(rect: Sequence[float], px: float, py: float) -> bool:
    x, y, w, h = rect[0], rect[1], rect[2], rect[3]
    return x <= px <= x + w and y <= py <= y + h


def expand(rect: Sequence[float], pad: float) -> tuple[float, float, float, float]:
    x, y, w, h = rect[0], rect[1], rect[2], rect[3]
    return x - pad, y - pad, w + pad * 2, h + pad * 2


def rects_intersect(a: Sequence[float], b: Sequence[float]) -> bool:
    return not (
        a[0] + a[2] < b[0]
        or b[0] + b[2] < a[0]
        or a[1] + a[3] < b[1]
        or b[1] + b[3] < a[1]
    )


def union(rects: Iterable[Sequence[float]]) -> tuple[float, float, float, float] | None:
    items = [r for r in rects if r and r[2] > 0 and r[3] > 0]
    if not items:
        return None
    x0 = min(r[0] for r in items)
    y0 = min(r[1] for r in items)
    x1 = max(r[0] + r[2] for r in items)
    y1 = max(r[1] + r[3] for r in items)
    return x0, y0, x1 - x0, y1 - y0


@dataclass(frozen=True, slots=True)
class LayoutMetrics:
    """Density-scaled spacing tokens. Recomputed on every density change."""

    density: float

    @property
    def pad(self) -> int:
        return clamp_int(16 * self.density, 8, 34)

    @property
    def pad_small(self) -> int:
        return clamp_int(10 * self.density, 6, 22)

    @property
    def gap(self) -> int:
        return clamp_int(8 * self.density, 4, 18)

    @property
    def radius(self) -> int:
        return clamp_int(12 * self.density, 8, 20)

    @property
    def carousel_h(self) -> int:
        return clamp_int(130 * self.density, 108, 210)

    @property
    def recent_h(self) -> int:
        return clamp_int(136 * self.density, 112, 214)

    @property
    def header_h(self) -> int:
        return clamp_int(48 * self.density, 44, 70)

    @property
    def footer_h(self) -> int:
        return clamp_int(54 * self.density, 50, 80)

    @property
    def rail_w(self) -> int:
        return clamp_int(264 * self.density, 236, 320)

    @property
    def font_body(self) -> int:
        return clamp_int(12 * self.density, 10, 17)

    @property
    def font_small(self) -> int:
        return clamp_int(10 * self.density, 8, 14)

    @property
    def font_title(self) -> int:
        return clamp_int(15 * self.density, 12, 21)

    @property
    def card_w(self) -> int:
        return clamp_int(168 * self.density, 148, 244)

    @property
    def card_h(self) -> int:
        return clamp_int(92 * self.density, 78, 132)


def verify_viewport(width: int, height: int) -> bool:
    """Hardlock audit: the client area must be exactly 1280x720."""
    return width == VIEWPORT_W and height == VIEWPORT_H
