"""Predefined vector layouts for the top templates carousel.

Each builder is a pure function returning a fully populated ``NoteDocument`` at
the native 960x600 buffer resolution. Nothing is read from disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .constants import DOC_H, DOC_W, LAYER_ACCENTS, PALETTE
from .geometry import clamp
from .model import DocumentMeta, ImageLayer, Layer, NoteDocument, ShapeLayer, TextLayer, new_layer_id

INK = PALETTE["ink"]
MUTED = "#5A6B7C"
PAPER = PALETTE["paper"]
LINE_SOFT = "#DCE6F2"


def _text(
    name: str,
    text: str,
    x: float,
    y: float,
    w: float,
    size: int,
    fill: str = INK,
    bold: bool = False,
    align: str = "left",
    family: str = "DejaVu Sans",
    italic: bool = False,
    line_spacing: float = 1.3,
    opacity: float = 1.0,
) -> TextLayer:
    return TextLayer(
        id=new_layer_id("text"),
        name=name,
        x=x,
        y=y,
        w=w,
        text=text,
        family=family,
        size=size,
        fill=fill,
        bold=bold,
        italic=italic,
        align=align,
        line_spacing=line_spacing,
        opacity=opacity,
    )


def _shape(
    name: str,
    shape: str,
    x: float,
    y: float,
    w: float,
    h: float,
    stroke: str = "#00000000",
    fill: str = "#00000000",
    stroke_width: int = 0,
    radius: int = 0,
    opacity: float = 1.0,
) -> ShapeLayer:
    return ShapeLayer(
        id=new_layer_id("shape"),
        name=name,
        shape=shape,
        x=x,
        y=y,
        w=w,
        h=h,
        stroke=stroke,
        stroke_width=stroke_width,
        fill=fill,
        radius=radius,
        opacity=opacity,
    )


def _line(name: str, x: float, y: float, dx: float, dy: float, stroke: str, width: int = 3, opacity: float = 1.0) -> ShapeLayer:
    return _shape(name, "line", x, y, dx, dy, stroke=stroke, stroke_width=width, opacity=opacity)


def _rect(
    name: str,
    x: float,
    y: float,
    w: float,
    h: float,
    fill: str = "#00000000",
    stroke: str = "#00000000",
    width: int = 0,
    radius: int = 0,
    opacity: float = 1.0,
) -> ShapeLayer:
    return _shape(name, "rect", x, y, w, h, stroke=stroke, fill=fill, stroke_width=width, radius=radius, opacity=opacity)


def _ellipse(
    name: str,
    x: float,
    y: float,
    w: float,
    h: float,
    fill: str = "#00000000",
    stroke: str = "#00000000",
    width: int = 0,
    opacity: float = 1.0,
) -> ShapeLayer:
    return _shape(name, "ellipse", x, y, w, h, stroke=stroke, fill=fill, stroke_width=width, opacity=opacity)


def _document(title: str, background: str, grid: bool, accent: str, template_id: str, layers: list[Layer], footer: str = "") -> NoteDocument:
    return NoteDocument(
        title=title,
        width=DOC_W,
        height=DOC_H,
        background=background,
        layers=layers,
        meta=DocumentMeta(
            template_id=template_id,
            accent=accent,
            grid=grid,
            grid_spacing=32,
            grid_color="#E1E9F4",
            footer_note=footer,
        ),
    )


def _today() -> str:
    """Human date stamp for diary layouts, read from the local clock only."""
    return datetime.now().strftime("%A · %d %B %Y")


def _tint(color: str, weight: float) -> str:
    """Mix a colour toward white.

    The Pillow raster draws shapes opaque (layer opacity only steers image
    tiles), so a translucent look has to be baked into the hex value itself or
    the pale wash would punch a solid disc over the text sitting on top of it.
    """
    digits = color.lstrip("#")
    if len(digits) != 6:
        return color
    blend = clamp(weight, 0.0, 1.0)
    channels = []
    for index in (0, 2, 4):
        value = int(digits[index:index + 2], 16)
        channels.append(int(round(value + (255 - value) * blend)))
    return "#{:02X}{:02X}{:02X}".format(*channels)


def build_blank() -> NoteDocument:
    layers: list[Layer] = [
        _rect("Margin rule", 48, 48, DOC_W - 96, DOC_H - 96, stroke="#E6EDF6", width=2, radius=22),
        _text("Hint", "Insert a text node, a geometric shape or an image to begin.", 78, 78, 500, 15, MUTED, italic=True),
    ]
    return _document("Blank Canvas", PAPER, True, PALETTE["accent"], "blank", layers, "MITCHIT QUICKNOTES · BLANK")


def build_formula() -> NoteDocument:
    accent = PALETTE["accent"]
    layers: list[Layer] = [
        _rect("Header band", 0, 0, DOC_W, 112, fill="#0D141C", radius=0),
        _rect("Header accent", 44, 34, 6, 44, fill=accent, radius=3),
        _text("Title", "IMSO FORMULA SHEET", 64, 26, 620, 32, "#FFFFFF", bold=True),
        _text("Subtitle", "Mathematics · concise reference card", 66, 72, 520, 14, "#8FB3D9"),
        _text("Badge", "STAGE 2", 800, 40, 116, 14, accent, bold=True, align="center"),
        _rect("Badge frame", 790, 34, 130, 34, stroke="#24405E", width=2, radius=17),
    ]
    cards = (
        ("CARD 01 · CIRCLE", "A = πr²", "circumference = 2πr", accent),
        ("CARD 02 · SPHERE", "V = 4/3 πr³", "surface = 4πr²", PALETTE["mint"]),
        ("CARD 03 · ENERGY", "E = mc²", "mass–energy identity", PALETTE["amber"]),
    )
    for index, (label, formula, note, tone) in enumerate(cards):
        x = 44 + index * 300
        layers.append(_rect(f"Card {index + 1}", x, 148, 272, 196, fill="#FFFFFF", stroke=LINE_SOFT, width=2, radius=16))
        layers.append(_rect(f"Card {index + 1} accent", x + 22, 172, 34, 5, fill=tone, radius=3))
        layers.append(_text(f"Card {index + 1} label", label, x + 22, 188, 228, 12, MUTED, bold=True))
        layers.append(_text(f"Card {index + 1} formula", formula, x + 22, 232, 228, 30, INK, bold=True))
        layers.append(_text(f"Card {index + 1} note", note, x + 22, 296, 228, 13, MUTED))
    layers.append(_line("Divider", 44, 388, 872, 0, "#E1E9F4", 2))
    layers.append(
        _text(
            "Identity block",
            "a² + b² = c²        (a + b)² = a² + 2ab + b²\nsin²θ + cos²θ = 1        Σn = n(n + 1) / 2",
            44,
            414,
            700,
            19,
            INK,
            family="DejaVu Sans Mono",
            line_spacing=1.9,
        )
    )
    layers.append(_rect("Stamp", 690, 470, 226, 74, fill="#EAF3FF", radius=14))
    layers.append(_text("Stamp title", "MEMORISE DAILY", 712, 486, 190, 16, accent, bold=True))
    layers.append(_text("Stamp note", "15 minutes · 3 rounds", 712, 512, 190, 13, MUTED))
    return _document("IMSO Formula Sheet", PAPER, True, accent, "formula", layers, "THE MITCHIT TECHNOLOGIES · IMSO PREP")


def build_todo() -> NoteDocument:
    """To-do list: tick board, priority flags and a completion meter."""
    accent = PALETTE["mint"]
    tasks = (
        ("Complete 10 algebra problems", True, 3),
        ("Revise circle theorems", True, 2),
        ("Write the science lab conclusion", True, 1),
        ("Long division speed drill", False, 3),
        ("Read chapter 4: light and sound", False, 2),
        ("Pack the Olympiad notebook", False, 1),
    )
    layers: list[Layer] = [
        _rect("Header band", 0, 0, DOC_W, 104, fill="#0C1712", radius=0),
        _rect("Header accent", 40, 30, 6, 44, fill=accent, radius=3),
        _text("Title", "TO-DO LIST", 58, 26, 480, 30, "#FFFFFF", bold=True),
        _text("Subtitle", "Hôm nay · six missions, ticked one by one", 60, 70, 520, 13, "#79D6B4"),
        _text("Counter", "3 / 6 DONE", 742, 44, 178, 15, accent, bold=True, align="center"),
    ]
    for index, (label, done, priority) in enumerate(tasks):
        y = 132 + index * 62
        layers.append(_rect(f"Row {index + 1}", 40, y, 640, 52, fill="#FFFFFF", stroke=LINE_SOFT, width=2, radius=12))
        layers.append(
            _rect(
                f"Box {index + 1}",
                60,
                y + 16,
                22,
                22,
                fill=accent if done else "#FFFFFF",
                stroke=accent,
                width=2,
                radius=6,
            )
        )
        layers.append(_text(f"Task {index + 1}", label, 96, y + 16, 460, 15, MUTED if done else INK))
        if done:
            layers.append(_line(f"Strike {index + 1}", 96, y + 26, min(460.0, len(label) * 8.2), 0, "#BFCEDB", 2))
        layers.append(_rect(f"Priority {index + 1}", 634, y + 14, 32, 24, fill=LAYER_ACCENTS[priority - 1], radius=8))
        layers.append(
            _text(
                f"Priority label {index + 1}",
                f"P{priority}",
                634,
                y + 18,
                32,
                12,
                "#08111A",
                bold=True,
                align="center",
            )
        )
    layers.append(_rect("Meter card", 700, 132, 220, 156, fill="#0C1712", radius=16))
    layers.append(_text("Meter label", "COMPLETION", 720, 152, 180, 12, "#79D6B4", bold=True))
    layers.append(_text("Meter value", "50%", 718, 176, 180, 46, "#FFFFFF", bold=True))
    layers.append(_rect("Meter track", 720, 240, 180, 12, fill="#1C2C24", radius=6))
    layers.append(_rect("Meter fill", 720, 240, 90, 12, fill=accent, radius=6))
    layers.append(_rect("Reward card", 700, 304, 220, 118, fill="#EAF9F1", radius=16))
    layers.append(_text("Reward label", "REWARD", 720, 322, 180, 12, "#1F7A5C", bold=True))
    layers.append(_text("Reward body", "40 minutes of\nTetris, guilt-free", 720, 346, 180, 16, INK, line_spacing=1.35))
    layers.append(_rect("Sign strip", 700, 438, 220, 56, fill="#FFFFFF", stroke=LINE_SOFT, width=2, radius=12))
    layers.append(_text("Sign text", "Checked by: Lam", 720, 458, 180, 13, INK))
    layers.append(_text("Rule", "Rule: làm khó trước · the hardest task always goes first.", 40, 512, 640, 13, MUTED, italic=True))
    return _document("To-do List", PAPER, True, accent, "todo", layers, "MITCHIT QUICKNOTES · TO-DO")


def build_favorite() -> NoteDocument:
    """My favorite: a ranked podium of the things worth keeping in mind."""
    accent = PALETTE["violet"]
    favourites = (
        ("Mathematics", "the moment a hard problem suddenly opens", PALETTE["accent"]),
        ("Science", "experiments that actually prove the claim", PALETTE["mint"]),
        ("Reading", "one story before every single night", PALETTE["amber"]),
        ("Football", "Saturday morning with the whole class", PALETTE["danger"]),
    )
    layers: list[Layer] = [
        _text("Title", "MY FAVORITE", 48, 34, 520, 34, INK, bold=True),
        _text("Subtitle", "Điều tôi thích nhất · four things, ranked honestly", 50, 80, 560, 14, MUTED),
    ]
    for index, (name, reason, tone) in enumerate(favourites):
        y = 120 + index * 92
        layers.append(_rect(f"Card {index + 1}", 118, y, 512, 78, fill="#FFFFFF", stroke=LINE_SOFT, width=2, radius=16))
        layers.append(_ellipse(f"Medal {index + 1}", 52, y + 17, 44, 44, fill=_tint(tone, 0.84)))
        layers.append(_ellipse(f"Medal ring {index + 1}", 52, y + 17, 44, 44, stroke=tone, width=3))
        layers.append(_text(f"Rank {index + 1}", str(index + 1), 52, y + 26, 44, 24, tone, bold=True, align="center"))
        layers.append(_text(f"Name {index + 1}", name, 140, y + 16, 300, 20, INK, bold=True))
        layers.append(_text(f"Reason {index + 1}", reason, 140, y + 46, 460, 13, MUTED))
    layers.append(_rect("Palette card", 660, 120, 260, 264, fill="#0D141C", radius=18))
    layers.append(_text("Palette title", "FAVORITE COLOURS", 682, 142, 216, 13, "#9CB6D4", bold=True))
    swatch_names = ("SKY BLUE", "MINT", "AMBER", "VIOLET", "CORAL")
    for index, name in enumerate(swatch_names):
        y = 176 + index * 38
        layers.append(_rect(f"Swatch {name}", 682, y, 46, 22, fill=LAYER_ACCENTS[index], radius=7))
        layers.append(_text(f"Swatch label {name}", name, 740, y + 3, 160, 13, "#DCE7F2"))
    layers.append(_rect("Why card", 660, 400, 260, 118, fill="#F3EEFF", radius=16))
    layers.append(_text("Why title", "WHY I WRITE IT DOWN", 682, 418, 216, 12, accent, bold=True))
    layers.append(_text("Why body", "A favourite that is written\ndown stays favourite.", 682, 442, 216, 13, INK, line_spacing=1.4))
    layers.append(_line("Base rule", 48, 500, 582, 0, "#E1E9F4", 2))
    layers.append(_text("Base note", "Ranked by me, not by an algorithm · zero cloud, zero cost.", 48, 516, 580, 13, MUTED))
    return _document("My Favorite", PAPER, True, accent, "favorite", layers, "MITCHIT QUICKNOTES · FAVORITES")


def build_memo() -> NoteDocument:
    """A memo note: one pinned card, three hard facts, nothing decorative."""
    accent = PALETTE["amber"]
    points = (
        ("01", "Return the library book before 10:00."),
        ("02", "Bring the geometry set for the Stage 2 test."),
        ("03", "Call Grandpa — Sunday lunch is at noon."),
    )
    layers: list[Layer] = [
        _rect("Pin board", 0, 0, DOC_W, DOC_H, fill="#EDF1F6", radius=0),
        _rect("Memo card", 108, 62, 744, 456, fill="#FFFBEE", stroke="#EFDDAE", width=2, radius=18),
        _rect("Tape", 400, 42, 160, 40, fill="#E4EBF3", radius=4),
        _rect("Accent bar", 140, 108, 8, 44, fill=accent, radius=4),
        _text("Title", "A MEMO NOTE", 164, 104, 380, 30, INK, bold=True),
        _text("Subtitle", "Ghi nhớ nhanh · one page, three facts, no excuses", 166, 144, 520, 13, MUTED),
        _text("Date", _today(), 620, 116, 200, 13, MUTED, align="right"),
        _text("Recipient", "TO: me, tomorrow morning", 140, 186, 420, 15, INK, bold=True),
        _line("Header rule", 140, 216, 680, 0, "#E7D9B4", 2),
    ]
    for index, (marker, body) in enumerate(points):
        y = 242 + index * 66
        layers.append(_ellipse(f"Bullet {index + 1}", 140, y, 34, 34, stroke=accent, width=3))
        layers.append(_text(f"Marker {index + 1}", marker, 140, y + 9, 34, 13, INK, bold=True, align="center"))
        layers.append(_text(f"Point {index + 1}", body, 190, y + 8, 600, 17, INK))
        layers.append(_line(f"Underline {index + 1}", 190, y + 46, 600, 0, "#EFE3C6", 1))
    layers.append(_rect("Priority chip", 690, 434, 140, 44, fill="#FFF1D2", stroke=accent, width=2, radius=22))
    layers.append(_text("Priority label", "HIGH PRIORITY", 690, 448, 140, 12, "#8A5B12", bold=True, align="center"))
    layers.append(_text("Signature", "— written by Lam · The Mitchit Technologies", 140, 456, 480, 13, MUTED, italic=True))
    layers.append(_text("Board hint", "Read it once in the morning. Do not re-pin it after lunch.", 108, 536, 620, 12, "#7B8A99"))
    return _document("A Memo Note", "#EDF1F6", False, accent, "memo", layers, "MITCHIT QUICKNOTES · MEMO")


def build_diary() -> NoteDocument:
    """My diary for this day: date strip, mood chips, ruled entry, reflections."""
    accent = PALETTE["accent"]
    moods = ("HAPPY", "PROUD", "CURIOUS", "TIRED")
    chosen = 1
    gratitudes = (
        "the four-minute circle question",
        "Mum's note about my handwriting",
        "one goal in the Saturday match",
    )
    layers: list[Layer] = [
        _rect("Page frame", 32, 26, 896, 548, fill="#FFFFFF", stroke=LINE_SOFT, width=2, radius=18),
        _text("Kicker", "MY DIARY · THIS DAY", 62, 52, 420, 13, accent, bold=True),
        _text("Date", _today(), 62, 78, 520, 30, INK, bold=True),
        _text("Weather", "Nắng · sunny, 31°C, light wind", 62, 122, 420, 14, MUTED),
    ]
    for index, mood in enumerate(moods):
        x = 62 + index * 116
        active = index == chosen
        layers.append(
            _rect(
                f"Mood {mood}",
                x,
                156,
                104,
                32,
                fill=accent if active else "#F4F8FC",
                stroke=accent if active else LINE_SOFT,
                width=2,
                radius=16,
            )
        )
        layers.append(
            _text(
                f"Mood label {mood}",
                mood,
                x,
                164,
                104,
                12,
                "#FFFFFF" if active else MUTED,
                bold=True,
                align="center",
            )
        )
    for index in range(8):
        y = 250 + index * 34
        layers.append(_line(f"Rule {index + 1}", 62, y, 468, 0, "#EDF2F8", 1))
    layers.append(
        _text(
            "Entry",
            "Today the Stage 2 mock paper felt lighter than last week.\n"
            "I solved the circle question in four minutes and did not\n"
            "rush the algebra. Mum said my handwriting is getting\n"
            "better. The streak stays alive: fifteen minutes of formulas\n"
            "before bed, no phone first.",
            62,
            216,
            468,
            15,
            INK,
            family="DejaVu Serif",
            line_spacing=2.25,
        )
    )
    layers.append(_rect("Grateful card", 580, 206, 316, 168, fill="#F5F9FE", radius=16))
    layers.append(_text("Grateful title", "GRATEFUL FOR", 602, 226, 260, 12, accent, bold=True))
    for index, item in enumerate(gratitudes):
        y = 254 + index * 38
        layers.append(_ellipse(f"Grateful dot {index + 1}", 602, y + 5, 8, 8, fill=PALETTE["mint"]))
        layers.append(_text(f"Grateful {index + 1}", item, 622, y, 256, 13, INK))
    layers.append(_rect("Tomorrow card", 580, 390, 316, 116, fill="#0D141C", radius=16))
    layers.append(_text("Tomorrow title", "TOMORROW", 602, 410, 260, 12, "#8FB3D9", bold=True))
    layers.append(
        _text(
            "Tomorrow body",
            "One proof, one drill, one page.\nStreak target: 8 days.",
            602,
            434,
            260,
            15,
            "#FFFFFF",
            line_spacing=1.5,
        )
    )
    layers.append(_rect("Day track", 580, 524, 316, 10, fill="#EDF2F8", radius=5))
    layers.append(_rect("Day fill", 580, 524, 214, 10, fill=accent, radius=5))
    layers.append(_text("Privacy note", "Recorded locally · nothing leaves this machine.", 62, 500, 420, 11, "#9AA9B8", italic=True))
    return _document("My Diary · This Day", PAPER, False, accent, "diary", layers, "MITCHIT QUICKNOTES · DIARY")


def build_learning() -> NoteDocument:
    """Learning note: objective, three concept blocks, recap formulas, self quiz."""
    accent = PALETTE["mint"]
    concepts = (
        ("01 · RADIUS", "r reaches from the centre to any point on the rim.", PALETTE["accent"]),
        ("02 · UNROLL", "Cut the circle into wedges and rebuild a rough triangle.", PALETTE["amber"]),
        ("03 · DERIVE", "base = πr, height = r, so A = ½ · 2πr · r = πr².", accent),
    )
    layers: list[Layer] = [
        _rect("Header band", 0, 0, DOC_W, 120, fill="#0C1B18", radius=0),
        _rect("Subject tab", 40, 34, 96, 26, fill=accent, radius=13),
        _text("Subject", "MATH", 40, 40, 96, 13, "#062018", bold=True, align="center"),
        _text("Title", "LEARNING NOTE", 152, 28, 480, 28, "#FFFFFF", bold=True),
        _text("Topic", "Topic: circles · area, circumference and the why", 154, 68, 560, 14, "#7FD8BC"),
        _text("Duration", "25 MIN", 800, 40, 120, 13, accent, bold=True, align="center"),
        _text(
            "Objective",
            "OBJECTIVE: derive A = πr² from the unrolled circle and never memorise it blind.",
            40,
            140,
            880,
            15,
            INK,
            bold=True,
        ),
    ]
    for index, (label, body, tone) in enumerate(concepts):
        x = 40 + index * 300
        layers.append(_rect(f"Concept {index + 1}", x, 184, 272, 148, fill="#FFFFFF", stroke=LINE_SOFT, width=2, radius=16))
        layers.append(_rect(f"Concept tab {index + 1}", x + 20, 206, 30, 5, fill=tone, radius=3))
        layers.append(_text(f"Concept label {index + 1}", label, x + 20, 222, 232, 13, tone, bold=True))
        layers.append(_text(f"Concept body {index + 1}", body, x + 20, 248, 232, 14, INK, line_spacing=1.35))
    layers.append(_rect("Formula box", 40, 352, 560, 96, fill="#F2FBF7", stroke="#BFE7D5", width=2, radius=14))
    layers.append(_text("Formula title", "FORMULAS I USED", 62, 366, 300, 12, "#1F7A5C", bold=True))
    layers.append(_text("Formula body", "C = 2πr        A = πr²        π ≈ 22/7 ≈ 3.14159", 62, 392, 520, 19, INK, family="DejaVu Sans Mono"))
    layers.append(_rect("Quiz card", 620, 352, 300, 176, fill="#0C1B18", radius=16))
    layers.append(_text("Quiz title", "SELF QUIZ", 642, 372, 256, 12, accent, bold=True))
    layers.append(
        _text(
            "Quiz body",
            "Q1 · r = 7, find A.\nQ2 · C = 44, find r.\nQ3 · why is π not a fraction?",
            642,
            398,
            256,
            14,
            "#DCE7F2",
            line_spacing=1.7,
        )
    )
    layers.append(_rect("Confidence card", 40, 468, 560, 60, fill="#FFFFFF", stroke=LINE_SOFT, width=2, radius=14))
    layers.append(_text("Confidence label", "CONFIDENCE", 62, 480, 160, 12, MUTED, bold=True))
    layers.append(_rect("Confidence track", 62, 508, 400, 10, fill="#EAF0F7", radius=5))
    layers.append(_rect("Confidence fill", 62, 508, 312, 10, fill=accent, radius=5))
    layers.append(_text("Confidence value", "78%", 470, 484, 108, 18, INK, bold=True, align="right"))
    layers.append(_rect("Next card", 40, 540, 560, 40, fill="#EAF9F1", radius=12))
    layers.append(_text("Next body", "Re-check in 3 days · spaced repetition beats re-reading.", 58, 552, 500, 12, "#1F7A5C"))
    layers.append(_text("Side note", "Mistake log: never write A = 2πr² again.", 620, 540, 300, 12, MUTED))
    return _document("Learning Note", PAPER, True, accent, "learning", layers, "MITCHIT QUICKNOTES · LEARNING")


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    key: str
    label: str
    tagline: str
    accent: str
    builder: Callable[[], NoteDocument]

    def build(self) -> NoteDocument:
        return self.builder()


TEMPLATES: tuple[TemplateSpec, ...] = (
    TemplateSpec("formula", "IMSO Formula Sheet", "Reference card · 3 formula blocks", PALETTE["accent"], build_formula),
    TemplateSpec("todo", "To-do List", "Tick board · priority flags", PALETTE["mint"], build_todo),
    TemplateSpec("favorite", "My Favorite", "Ranked podium · colour palette", PALETTE["violet"], build_favorite),
    TemplateSpec("memo", "A Memo Note", "Pinned card · three hard facts", PALETTE["amber"], build_memo),
    TemplateSpec("diary", "My Diary · This Day", "Date strip · mood chips · entry", "#7C5CFF", build_diary),
    TemplateSpec("learning", "Learning Note", "Objective · concepts · self quiz", PALETTE["mint"], build_learning),
)

TEMPLATE_BY_KEY = {spec.key: spec for spec in TEMPLATES}


def build_template(key: str) -> NoteDocument:
    """Build a carousel layout by key; unknown keys fall back to blank paper."""
    spec = TEMPLATE_BY_KEY.get(key)
    if spec is None:
        return build_blank()
    return spec.build()


def template_choices() -> tuple[tuple[str, str], ...]:
    return tuple((spec.key, spec.label) for spec in TEMPLATES)


def image_layer_from_path(path: str, payload_b64: str, x: float, y: float, w: float, h: float, name: str) -> ImageLayer:
    return ImageLayer(
        id=new_layer_id("image"),
        name=name,
        x=x,
        y=y,
        w=w,
        h=h,
        payload=payload_b64,
        source_name=path,
        fit="contain",
        radius=12,
        border="#DCE6F2",
        border_width=2,
    )
