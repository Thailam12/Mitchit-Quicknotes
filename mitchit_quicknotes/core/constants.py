"""Immutable constants: filesystem anchors, palette tokens, format registry.

Every value here is a hard constraint of the v1.0 RTM contract. Nothing in this
module touches the network, the Qt layer, or the disk at import time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .. import APP_NAME, APP_VERSION

# --------------------------------------------------------------------------- #
# Filesystem anchors (resolved once, relative to the package, never absolute
# paths baked into exported artifacts).
# --------------------------------------------------------------------------- #
CORE_DIR: Path = Path(__file__).resolve().parent
PACKAGE_DIR: Path = CORE_DIR.parent
PROJECT_ROOT: Path = PACKAGE_DIR.parent
NOTES_DIR: Path = PROJECT_ROOT / "notes"
EXPORT_DIR: Path = PROJECT_ROOT / "exports"
ENV_FILE: Path = PROJECT_ROOT / ".env"
GITIGNORE_FILE: Path = PROJECT_ROOT / ".gitignore"

# --------------------------------------------------------------------------- #
# Container policy
# --------------------------------------------------------------------------- #
# The document container is a universal PNG image: the layer tree rides inside
# its open text chunks, so a saved note displays in any viewer anywhere and is
# still fully editable here. No proprietary suffix is ever produced.
DOCUMENT_SUFFIX = ".png"
CONTAINER_SUFFIX = DOCUMENT_SUFFIX
CONTAINER_LABEL = "Universal PNG container"

# --------------------------------------------------------------------------- #
# Presentation hardlock
# --------------------------------------------------------------------------- #
VIEWPORT_W = 1280
VIEWPORT_H = 720
ASPECT_LABEL = "16:9"

HEADER_H = 52
FOOTER_H = 58
RAIL_W = 264
CAROUSEL_H = 130
RECENT_H = 136

DENSITY_MIN = 0.50
DENSITY_MAX = 2.00
DENSITY_STEPS = (0.50, 0.75, 1.00, 1.25, 1.50, 2.00)
DENSITY_DEFAULT = 1.00

ZOOM_MIN = 0.50
ZOOM_MAX = 2.00
ZOOM_STEP = 0.10

# Vector document buffer. Notes are authored at this resolution and exported
# with an integer/rational scale multiplier.
DOC_W = 960
DOC_H = 600

# Glassmorphism
GLASS_RADIUS = 16
GLASS_BLUR_RADIUS = 26

# --------------------------------------------------------------------------- #
# Colour palette (single source of truth for QSS + Pillow backgrounds)
# --------------------------------------------------------------------------- #
PALETTE = {
    "void": "#05080C",
    "shell": "#0A0F15",
    "panel": "#0E151D",
    "panel_alt": "#121C26",
    "panel_hi": "#16232F",
    "line": "#1E2C3A",
    "line_hi": "#2A3D4F",
    "text": "#E8F0F8",
    "text_dim": "#93A6B8",
    "text_faint": "#5F7488",
    "accent": "#4EA1FF",
    "accent_soft": "#1B3A5C",
    "mint": "#6FE7C1",
    "amber": "#FFB648",
    "danger": "#FF5F7E",
    "violet": "#A78BFA",
    "paper": "#F7F9FC",
    "ink": "#10161D",
}

LAYER_ACCENTS = ("#4EA1FF", "#6FE7C1", "#FFB648", "#A78BFA", "#FF5F7E")

# --------------------------------------------------------------------------- #
# Fonts used for the note buffer itself (Pillow side)
# --------------------------------------------------------------------------- #
NOTE_FONT_FAMILIES = (
    "DejaVu Sans",
    "DejaVu Serif",
    "DejaVu Sans Mono",
    "Liberation Sans",
    "Liberation Serif",
    "Noto Sans",
    "Ubuntu",
    "Ubuntu Mono",
)

NOTE_FONT_SIZES = (12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 48, 56, 64, 72, 96)

# --------------------------------------------------------------------------- #
# Image serialization registry (universal factory)
# --------------------------------------------------------------------------- #
RASTER_EXTENSIONS = (".png", ".jpg", ".jpeg", ".ico")
VECTOR_EXTENSIONS = (".svg",)
EXPORT_EXTENSIONS = RASTER_EXTENSIONS + VECTOR_EXTENSIONS


@dataclass(frozen=True, slots=True)
class ExportFormat:
    """One serialization target. ``writer`` is a method name on the factory."""

    key: str
    label: str
    extension: str
    writer: str
    mode: str
    supports_scale: bool
    hint: str

    @property
    def filter_string(self) -> str:
        return f"{self.label} (*{self.extension})"


EXPORT_FORMATS: tuple[ExportFormat, ...] = (
    ExportFormat(
        key="png",
        label="PNG · lossless raster",
        extension=".png",
        writer="write_png",
        mode="RGBA",
        supports_scale=True,
        hint="Lossless RGBA. Best for study notes, screenshots, print-ready sheets.",
    ),
    ExportFormat(
        key="jpg",
        label="JPG · compressed photo raster",
        extension=".jpg",
        writer="write_jpg",
        mode="RGB",
        supports_scale=True,
        hint="Flattened RGB at quality 93. Smallest payload for photo-heavy notes.",
    ),
    ExportFormat(
        key="ico",
        label="ICO · multi-size icon shell",
        extension=".ico",
        writer="write_ico",
        mode="RGBA",
        supports_scale=False,
        hint="16→256 px multi-resolution icon bundle for launchers and docks.",
    ),
    ExportFormat(
        key="svg",
        label="SVG · native vector attributes",
        extension=".svg",
        writer="write_svg",
        mode="VECTOR",
        supports_scale=False,
        hint="Raw geometric attributes, infinitely scalable, editable in Inkscape.",
    ),
)

EXPORT_FORMAT_BY_KEY = {fmt.key: fmt for fmt in EXPORT_FORMATS}

SCALE_CHOICES: tuple[tuple[str, float], ...] = (
    ("1.0× · native buffer", 1.0),
    ("1.5× · crisp", 1.5),
    ("2.0× · retina", 2.0),
    ("3.0× · print", 3.0),
)

# --------------------------------------------------------------------------- #
# Local auto-OCR contract
# --------------------------------------------------------------------------- #
OCR_BUDGET_MS = 1.0
OCR_MAX_SEGMENT_PIXELS = 720 * 720
OCR_MIN_CONFIDENCE = 0.35

# --------------------------------------------------------------------------- #
# Export bundle (ZIP compiler factory) layout
# --------------------------------------------------------------------------- #
BUNDLE_CORE_NODE = "core/index.html"
BUNDLE_STATE_NODE = "state/document.json"
BUNDLE_MANIFEST_NODE = "state/manifest.json"
BUNDLE_ASSET_DIR = "assets"
BUNDLE_LAYER_DIR = "assets/layers"

# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #
PUBLIC_SSID_MARKERS = (
    "free",
    "public",
    "guest",
    "cafe",
    "coffee",
    "airport",
    "hotel",
    "wifi",
    "wlan",
    "starbucks",
    "library",
)
VPN_INTERFACE_PREFIXES = ("tun", "tap", "wg", "proton", "warp", "tailscale", "zerotier")
VPN_PROCESS_MARKERS = ("protonvpn", "warp-svc", "wireguard", "openvpn", "tailscaled")

SLUG_RE = re.compile(r"[^a-z0-9]+")
VERSION_LABEL = f"v{APP_VERSION} RTM"
BRAND_LABEL = APP_NAME.upper()
