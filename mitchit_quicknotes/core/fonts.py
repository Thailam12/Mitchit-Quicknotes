"""Font resolution bridge between Pillow (raster) and Qt (chrome).

The note buffer is rasterised by Pillow, so every text layer needs a real
``.ttf`` file path. Fontconfig is queried once, the result is indexed in memory,
and families are matched to their closest regular/bold/italic face. Nothing here
requires the Qt event loop.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from .constants import NOTE_FONT_FAMILIES

FONT_SEARCH_DIRS = (
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".local/share/fonts",
    Path.home() / ".fonts",
)

_FALLBACK_FILES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


@dataclass(frozen=True, slots=True)
class FontFace:
    family: str
    style: str
    path: str


def _parse_fc_list(output: str) -> dict[str, list[FontFace]]:
    index: dict[str, list[FontFace]] = {}
    for raw_line in output.splitlines():
        parts = raw_line.split("\t")
        if len(parts) < 3:
            continue
        file_path, families, style = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not file_path.lower().endswith((".ttf", ".otf", ".ttc")):
            continue
        for family in families.split(","):
            family = family.strip()
            if not family:
                continue
            index.setdefault(family.lower(), []).append(
                FontFace(family=family, style=style or "Regular", path=file_path)
            )
    return index


@lru_cache(maxsize=1)
def _font_index() -> dict[str, list[FontFace]]:
    try:
        completed = subprocess.run(
            ["fc-list", "--format", "%{file}\t%{family}\t%{style}\n"],
            capture_output=True,
            text=True,
            timeout=6.0,
            check=False,
        )
        index = _parse_fc_list(completed.stdout or "")
        if index:
            return index
    except (OSError, subprocess.SubprocessError):
        index = {}

    for root in FONT_SEARCH_DIRS:
        if not root.exists():
            continue
        try:
            for path in root.rglob("*.ttf"):
                family = path.stem.split("-")[0]
                style = path.stem.split("-")[-1]
                index.setdefault(family.lower(), []).append(
                    FontFace(family=family, style=style, path=str(path))
                )
        except OSError:
            continue
    return index


@lru_cache(maxsize=1)
def known_families() -> tuple[str, ...]:
    index = _font_index()
    by_lower = {family.lower(): faces[0].family for family, faces in index.items() if faces}
    ordered: list[str] = []
    for preferred in NOTE_FONT_FAMILIES:
        match = by_lower.get(preferred.lower())
        if match and match not in ordered:
            ordered.append(match)
    for family in sorted(by_lower.values()):
        if family not in ordered and not family.startswith("."):
            ordered.append(family)
    if not ordered:
        ordered.append("DejaVu Sans")
    return tuple(ordered)


def _score(face: FontFace, bold: bool, italic: bool) -> int:
    style = face.style.lower()
    score = 0
    has_bold = "bold" in style or "heavy" in style or "black" in style
    has_italic = "italic" in style or "oblique" in style
    if bold == has_bold:
        score += 4
    if italic == has_italic:
        score += 2
    if "regular" in style or "book" in style:
        score += 1
    if "condensed" in style or "narrow" in style:
        score -= 1
    return score


@lru_cache(maxsize=256)
def resolve_font_path(family: str, bold: bool = False, italic: bool = False) -> str:
    index = _font_index()
    faces = index.get(family.lower())
    if not faces:
        for candidate, candidates in index.items():
            if family.lower() in candidate or candidate in family.lower():
                faces = candidates
                break
    if faces:
        best = max(faces, key=lambda face: _score(face, bold, italic))
        try:
            if Path(best.path).exists():
                return best.path
        except OSError:
            pass
    for fallback in _FALLBACK_FILES:
        if Path(fallback).exists():
            return fallback
    return ""


@lru_cache(maxsize=192)
def load_font(family: str, size: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont:
    """Cached Pillow face. Falls back to the built-in bitmap font when needed."""
    size = max(6, int(size))
    path = resolve_font_path(family, bold, italic)
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except (OSError, ValueError):
            pass
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except (OSError, ValueError):
        return ImageFont.load_default(size=size)


def measure_text(family: str, size: int, text: str, bold: bool = False, italic: bool = False) -> float:
    font = load_font(family, size, bold, italic)
    try:
        return float(font.getlength(text))
    except (AttributeError, ValueError):
        return float(len(text) * size * 0.55)


def font_debug_label(family: str, bold: bool, italic: bool) -> str:
    path = resolve_font_path(family, bold, italic)
    return f"{family} · {'B' if bold else 'R'}{'I' if italic else ''} · {Path(path).name if path else 'builtin'}"
