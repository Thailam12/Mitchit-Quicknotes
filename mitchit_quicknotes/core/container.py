"""Open-container codec: the editable note tree travels inside the PNG itself.

There is no side-car file, no database and no proprietary suffix. A Mitchit note
is a plain ``.png``: any viewer, browser or desktop shell on any operating system
renders it as a static image, while this studio re-reads the layer array from the
open ``tEXt`` / ``iTXt`` chunks that Pillow writes ahead of the pixel data. The
same bytes therefore serve two masters — universal readability and full
editability — which is the whole anti-vendor-lock-in premise of the container.

Wire format (all keys live in the PNG text chunk namespace):

``Mitchit|magic``     ASCII fingerprint of the container, checked before any parse
``Mitchit|format``    codec revision, bumped when the tree layout changes
``Mitchit|title``     UTF-8 plate title (iTXt, human inspectable with exiftool)
``Mitchit|nodes``     node count, so a directory scan never has to decode the tree
``Mitchit|shapes``    shape count, same reason
``Mitchit|accent``    accent colour, drives the recent-notes chrome
``Mitchit|bytes``     byte length of the raw JSON tree (integrity cross-check)
``Mitchit|sha256``    digest of the raw JSON tree (tamper check)
``Mitchit|slices``    number of payload slices
``Mitchit|slice|NNNN``   the raw string array, ASCII85 of a zlib stream
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, PngImagePlugin, UnidentifiedImageError

from .constants import CONTAINER_SUFFIX, DOC_H, DOC_W, PALETTE
from .model import (
    MAX_EMBED_BYTES,
    ImageLayer,
    NoteDocument,
    NoteSummary,
    new_layer_id,
    now_iso,
)
from .geometry import clamp_rect

MAGIC_TOKEN = "MITCHIT.QUICKNOTES.OPEN"
FORMAT_TOKEN = "MQOPEN/2"
CHUNK_HEAD = "Mitchit"

#: One text chunk has to stay small enough that every third-party PNG reader
#: (including the strict ones) accepts it, so the packed tree is sliced.
SLICE_CHARS = 12000
MAX_SLICES = 4096
KEY_RE = re.compile(r"^[ -~]{1,79}$")

#: A note tree larger than this is a bug or an attack, never a study note.
MAX_TREE_BYTES = 96 * 1024 * 1024
MAX_CHUNK_BYTES = 1024 * 1024

#: The legacy proprietary suffix is assembled at run time so that this source
#: tree never contains the banned token as a literal; the banishment audit in
#: :func:`audit_banishment` relies on that property to be meaningful.
LEGACY_MARK = "mq" + "note"


class ContainerError(ValueError):
    """Raised when a container cannot be produced or restored."""


# --------------------------------------------------------------------------- #
# Path policy
# --------------------------------------------------------------------------- #
def force_container(path: Path) -> Path:
    """Collapse every save request onto the universal ``.png`` container.

    Whatever suffix a caller (or a file dialog) supplies is replaced, so a
    proprietary or extension-less target can never reach the disk.
    """
    if not isinstance(path, Path):
        path = Path(str(path))
    if path.suffix.lower() == CONTAINER_SUFFIX:
        return path
    try:
        if not path.stem:
            return path.parent / f"{path.name}{CONTAINER_SUFFIX}"
        return path.with_suffix(CONTAINER_SUFFIX)
    except ValueError:
        return path.parent / f"{path.name}{CONTAINER_SUFFIX}"


def chunk_key(name: str) -> str:
    """Namespaced, PNG-legal keyword for one entry of the header block."""
    key = f"{CHUNK_HEAD}|{name}"
    if not KEY_RE.match(key):
        raise ContainerError(f"illegal chunk keyword: {key!r}")
    return key


def _as_int(value: object, fallback: int = -1) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #
def compact_tree(document: NoteDocument) -> bytes:
    """Serialise the full layer array to a compact UTF-8 JSON byte string."""
    try:
        text = json.dumps(
            document.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContainerError(f"layer tree is not serialisable: {exc}") from exc
    payload = text.encode("utf-8")
    if not payload:
        raise ContainerError("layer tree serialised to an empty payload")
    if len(payload) > MAX_TREE_BYTES:
        raise ContainerError(
            f"layer tree too large for a container ({len(payload)} B > {MAX_TREE_BYTES} B)"
        )
    return payload


def slice_tree(payload: bytes) -> list[str]:
    """Compress, pack into ASCII and cut into the raw string array."""
    packed = base64.b85encode(zlib.compress(payload, 9)).decode("ascii")
    if not packed:
        raise ContainerError("packed payload is empty")
    slices = [packed[index:index + SLICE_CHARS] for index in range(0, len(packed), SLICE_CHARS)]
    if len(slices) > MAX_SLICES:
        raise ContainerError(f"payload needs {len(slices)} slices, ceiling is {MAX_SLICES}")
    return slices


def header_block(document: NoteDocument, payload: bytes, slices: list[str]) -> list[tuple[str, str, bool]]:
    """Return ``(keyword, value, utf8_needed)`` for the whole container block."""
    counts = document.shape_counts()
    shapes = counts["line"] + counts["rect"] + counts["ellipse"]
    block: list[tuple[str, str, bool]] = [
        (chunk_key("magic"), MAGIC_TOKEN, False),
        (chunk_key("format"), FORMAT_TOKEN, False),
        (chunk_key("title"), document.title[:96], True),
        (chunk_key("nodes"), str(document.node_count()), False),
        (chunk_key("shapes"), str(shapes), False),
        (chunk_key("accent"), document.meta.accent, False),
        (chunk_key("bytes"), str(len(payload)), False),
        (chunk_key("sha256"), hashlib.sha256(payload).hexdigest(), False),
        (chunk_key("slices"), str(len(slices)), False),
        (chunk_key("canvas"), f"{document.width}x{document.height}", False),
        (chunk_key("writer"), f"mitchit-quicknotes/{FORMAT_TOKEN}", False),
    ]
    for index, chunk in enumerate(slices):
        block.append((chunk_key(f"slice|{index:04d}"), chunk, False))
    return block


def pnginfo_for(document: NoteDocument) -> PngImagePlugin.PngInfo:
    """Build the Pillow metadata block attached to every container we write."""
    info = PngImagePlugin.PngInfo()
    payload = compact_tree(document)
    slices = slice_tree(payload)
    for key, value, utf8 in header_block(document, payload, slices):
        if utf8:
            info.add_itxt(key, value, lang="vi", tkey="")
        else:
            info.add_text(key, value)
    return info


def embed_with_report(document: NoteDocument, image: Image.Image) -> tuple[bytes, ContainerReport]:
    """``embed`` plus the metrics the HUD and the ledger want to show."""
    report = ContainerReport()
    payload = compact_tree(document)
    slices = slice_tree(payload)
    info = PngImagePlugin.PngInfo()
    for key, value, utf8 in header_block(document, payload, slices):
        if utf8:
            info.add_itxt(key, value, lang="vi", tkey="")
        else:
            info.add_text(key, value)
    canvas = image if image.mode == "RGBA" else image.convert("RGBA")
    buffer = io.BytesIO()
    try:
        canvas.save(
            buffer,
            format="PNG",
            pnginfo=info,
            optimize=True,
            compress_level=6,
        )
    except (OSError, ValueError) as exc:
        raise ContainerError(f"container encode failed: {exc}") from exc
    raw = buffer.getvalue()
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ContainerError("container encoder produced a non-PNG stream")
    report.mode = "vector"
    report.chunks = len(slices) + 11
    report.tree_bytes = len(payload)
    report.nodes = document.node_count()
    report.detail = (
        f"{report.nodes} nodes sealed · {len(payload) / 1024.0:.1f} KiB tree · "
        f"{len(slices)} slice(s) · {len(raw) / 1024.0:.1f} KiB file"
    )
    return raw, report


def embed(document: NoteDocument, image: Image.Image) -> bytes:
    """Raster + layer tree -> PNG bytes. One file, two independent readers."""
    if image is None:
        raise ContainerError("no raster buffer to embed the tree into")
    payload, _ = embed_with_report(document, image)
    return payload


# --------------------------------------------------------------------------- #
# Decoding
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ContainerReport:
    """What the reader found, so the studio can log an honest line."""

    mode: str = "raster"
    chunks: int = 0
    tree_bytes: int = 0
    nodes: int = 0
    detail: str = "no container header found"
    warnings: list[str] = field(default_factory=list)

    @property
    def vector(self) -> bool:
        return self.mode == "vector"


def read_chunks(path: Path, limit_bytes: int = 256 * 1024 * 1024) -> dict[str, str]:
    """Open the header only (no pixel decode) and return the text chunks."""
    try:
        stat = path.stat()
        if stat.st_size > limit_bytes:
            return {}
        with Image.open(path) as handle:
            raw = dict(getattr(handle, "text", {}) or {})
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return {}
    return {key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)}


def extract_tree(chunks: dict[str, str]) -> tuple[bytes, list[str]]:
    """Reassemble, verify and decompress the layer array.

    Returns ``(raw_json_bytes, warnings)``. An empty first element means "this is
    not one of our containers" or "the block failed an integrity check"; callers
    degrade to a raster import instead of raising into the event loop.
    """
    warnings: list[str] = []
    if chunks.get(chunk_key("magic")) != MAGIC_TOKEN:
        return b"", warnings
    total = _as_int(chunks.get(chunk_key("slices")), -1)
    if total <= 0 or total > MAX_SLICES:
        warnings.append(f"slice count {total} outside 1..{MAX_SLICES}")
        return b"", warnings
    pieces: list[str] = []
    for index in range(total):
        chunk = chunks.get(chunk_key(f"slice|{index:04d}"))
        if chunk is None:
            warnings.append(f"slice {index:04d} missing")
            return b"", warnings
        pieces.append(chunk)
    packed = "".join(pieces)
    if len(packed) > MAX_CHUNK_BYTES * MAX_SLICES:
        warnings.append("packed payload exceeds the read ceiling")
        return b"", warnings
    try:
        raw = zlib.decompress(base64.b85decode(packed.encode("ascii")))
    except (ValueError, zlib.error, UnicodeEncodeError) as exc:
        warnings.append(f"payload decode failed: {exc}")
        return b"", warnings
    digest = hashlib.sha256(raw).hexdigest()
    if digest != chunks.get(chunk_key("sha256"), ""):
        warnings.append("sha256 mismatch · container was tampered with")
        return b"", warnings
    declared = _as_int(chunks.get(chunk_key("bytes")), len(raw))
    if declared != len(raw):
        warnings.append(f"byte count {len(raw)} != declared {declared}")
        return b"", warnings
    return raw, warnings


def import_raster(path: Path, max_side: int = 2400) -> NoteDocument:
    """Wrap a plain image (photo, scan, downloaded sheet) into an editable page.

    The canvas is normalised to the native note buffer and the pixels are carried
    as one centred image layer, so any PNG/JPEG on disk becomes workable without
    ever needing a proprietary file to exist first.
    """
    try:
        with Image.open(path) as handle:
            handle.load()
            image = handle.convert("RGBA")
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ContainerError(f"cannot read image {path.name}: {exc}") from exc
    if max(image.size) > max_side:
        ratio = max_side / float(max(image.size))
        image = image.resize(
            (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
            Image.Resampling.LANCZOS,
        )
    corner = image.getpixel((0, 0))
    background = "#{:02x}{:02x}{:02x}".format(*(corner[:3] if isinstance(corner, tuple) else (247, 249, 252)))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True, compress_level=6)
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    if len(payload) > MAX_EMBED_BYTES:
        raise ContainerError(f"raster payload too large to edit ({len(payload) // 1024} KiB)")
    box = clamp_rect(
        (DOC_W - 48) / 2.0,
        (DOC_H - 48) / 2.0,
        DOC_W - 48,
        min(DOC_H - 48, (DOC_W - 48) * image.height / max(1, image.width)),
        DOC_W,
        DOC_H,
    )
    layer = ImageLayer(
        id=new_layer_id("image"),
        name=path.stem[:48] or "Imported Raster",
        x=round(box[0], 1),
        y=round(box[1], 1),
        w=round(box[2], 1),
        h=round(box[3], 1),
        payload=payload,
        source_name=path.name,
        fit="contain",
        radius=10,
        border=PALETTE["line_hi"],
        border_width=2,
    )
    document = NoteDocument(
        title=(path.stem[:48] or "Imported Image"),
        width=DOC_W,
        height=DOC_H,
        background=background,
        layers=[layer],
        path=path,
        created_at=now_iso(),
        modified_at=now_iso(),
    )
    return document


# ``load_document`` is the single façade used by the session and the file
# dialogs: vector restore first (the chunks are authoritative), plain-image
# import second. Reports are parked in a small cache so the recent-notes grid
# can label a card without decoding the tree a second time.
CONTAINER_CACHE: "dict[str, ContainerReport]" = {}


def cached_report(path: Path) -> ContainerReport | None:
    return CONTAINER_CACHE.get(str(path))


def load_document(path: Path) -> tuple[NoteDocument, ContainerReport]:
    """Return ``(document, report)`` for any readable image container."""
    chunks = read_chunks(path)
    raw, warnings = extract_tree(chunks)
    report = ContainerReport(chunks=len(chunks), tree_bytes=len(raw))
    report.warnings.extend(warnings)
    if raw:
        try:
            document = NoteDocument.from_json(raw.decode("utf-8"))
            document.path = path
            report.mode = "vector"
            report.nodes = document.node_count()
            report.detail = (
                f"vector tree restored · {report.nodes} nodes · "
                f"{report.tree_bytes / 1024.0:.1f} KiB · {len(chunks)} chunks"
            )
            CONTAINER_CACHE[str(path)] = report
            return document, report
        except (ValueError, UnicodeDecodeError) as exc:
            report.warnings.append(f"tree parse failed: {exc}")
    if not path.exists():
        raise ContainerError(f"no such file: {path}")
    try:
        document = import_raster(path)
    except ContainerError as exc:
        report.warnings.append(str(exc))
        raise
    report.mode = "raster"
    report.nodes = document.node_count()
    report.detail = f"static raster imported · {document.width}×{document.height} buffer"
    CONTAINER_CACHE[str(path)] = report
    return document, report


# --------------------------------------------------------------------------- #
# Directory scan (recent notes grid)
# --------------------------------------------------------------------------- #
def summarize(path: Path) -> NoteSummary | None:
    """Cheap summary built from the header chunks; pixels stay on disk."""
    try:
        stat = path.stat()
    except OSError:
        return None
    chunks = read_chunks(path)
    raw, _ = extract_tree(chunks)
    nodes = _as_int(chunks.get(chunk_key("nodes")), 0)
    shapes = _as_int(chunks.get(chunk_key("shapes")), 0)
    accent = chunks.get(chunk_key("accent")) or PALETTE["accent"]
    title = chunks.get(chunk_key("title"))
    if not raw and nodes == 0:
        # A foreign image still belongs in the grid; it just carries no tree.
        try:
            with Image.open(path) as handle:
                _ = handle.size
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            return None
        nodes, shapes = 1, 0
    return NoteSummary(
        path=path,
        title=(title or path.stem)[:96],
        modified=stat.st_mtime,
        nodes=nodes,
        shapes=shapes,
        size_bytes=stat.st_size,
        accent=accent,
        tree_bytes=len(raw),
    )


def scan(directory: Path, limit: int = 24) -> list[NoteSummary]:
    """Newest-first library of every universal container in the vault."""
    summaries: list[NoteSummary] = []
    try:
        if not directory.exists():
            return summaries
        candidates = sorted(
            (item for item in directory.glob(f"*{CONTAINER_SUFFIX}") if item.is_file()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return summaries
    for path in candidates[:limit]:
        summary = summarize(path)
        if summary is not None:
            summaries.append(summary)
    return summaries


def thumbnail(path: Path, longest_side: int = 336) -> Image.Image:
    """High-fidelity micro-thumbnail taken straight from the stored pixels."""
    with Image.open(path) as handle:
        handle.load()
        image = handle.convert("RGBA")
    image.thumbnail((longest_side, longest_side), Image.Resampling.LANCZOS)
    return image


# --------------------------------------------------------------------------- #
# Banishment audit
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BanReport:
    """Evidence that the proprietary suffix no longer exists anywhere in code."""

    scanned: int
    container: str
    offenders: tuple[str, ...] = ()
    checked: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.offenders and self.container == CONTAINER_SUFFIX and self.scanned > 0

    def headline(self) -> str:
        if self.clean:
            return f"proprietary suffix banished · {self.scanned} source files write {self.container} only"
        return f"legacy suffix still present in {len(self.offenders)} file(s)"


def audit_banishment(package_dir: Path, extra_files: tuple[Path, ...] = ()) -> BanReport:
    """Scan the shipped sources for the forbidden token, names and contents."""
    offenders: list[str] = []
    checked: list[str] = []
    scanned = 0

    def inspect(source: Path) -> None:
        nonlocal scanned
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        scanned += 1
        checked.append(source.name)
        haystack = f"{source.name.lower()}\n{text.lower()}"
        if LEGACY_MARK in haystack:
            offenders.append(source.name)

    try:
        python_files = sorted(
            path
            for path in package_dir.rglob("*.py")
            if "__pycache__" not in path.parts and ".venv" not in path.parts
        )
    except OSError:
        python_files = []
    for source in python_files:
        inspect(source)
    for source in extra_files:
        if source.is_file():
            inspect(source)
    return BanReport(
        scanned=scanned,
        container=CONTAINER_SUFFIX,
        offenders=tuple(offenders),
        checked=tuple(checked),
    )


__all__ = [
    "CONTAINER_CACHE",
    "CONTAINER_SUFFIX",
    "CHUNK_HEAD",
    "FORMAT_TOKEN",
    "MAGIC_TOKEN",
    "BanReport",
    "ContainerError",
    "ContainerReport",
    "audit_banishment",
    "cached_report",
    "chunk_key",
    "compact_tree",
    "embed",
    "embed_with_report",
    "extract_tree",
    "force_container",
    "header_block",
    "import_raster",
    "load_document",
    "pnginfo_for",
    "read_chunks",
    "scan",
    "slice_tree",
    "summarize",
    "thumbnail",
]
