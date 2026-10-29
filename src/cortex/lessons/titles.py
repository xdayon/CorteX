"""On-screen titles as one libass `.ass` file (same engine as the cut captions), burned in the final encode.

Brand styles are declared once below, in fractions of the frame height, so 9:16 promos and 16:9 lessons
share them. Text stays inside the Reels/TikTok safe area (y between 10 % and 78 %, x centred).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cortex.config import CortexConfig
from cortex.lessons.common import LessonError
from cortex.lessons.sound import Anchors
from cortex.lessons.spec import LessonSpec, Title
from cortex.lessons.timeline import Timings

BEIGE, IVORY, GOLD, INK = "C0CFD8", "D0DFE8", "5CA4C9", "0B0B0B"  # ASS colours are BGR


@dataclass(frozen=True)
class Style:
    font: str
    size: float  # fraction of the frame height
    spacing: float  # letter spacing, fraction of the frame height
    colour: str
    y: float  # default centre height, fraction of the frame height
    alpha: str = "00"
    box: str | None = None  # opaque box colour behind the text (BorderStyle 3)


STYLES: dict[str, Style] = {
    "coords": Style("IBM Plex Mono Light", 0.019, 0.005, BEIGE, 0.115, alpha="50"),
    "kicker": Style("IBM Plex Mono", 0.021, 0.008, BEIGE, 0.115, alpha="20"),
    "word": Style("IBM Plex Mono Medium", 0.040, 0.014, GOLD, 0.70),
    "line": Style("IBM Plex Mono", 0.026, 0.008, IVORY, 0.70),
    "hero": Style("IBM Plex Mono Medium", 0.058, 0.020, IVORY, 0.48),
    "cta_small": Style("IBM Plex Mono", 0.024, 0.010, BEIGE, 0.600),
    "cta_big": Style("IBM Plex Mono SemiBold", 0.066, 0.020, GOLD, 0.652),
    "cta_note": Style("IBM Plex Mono Light", 0.020, 0.003, BEIGE, 0.703, alpha="18"),
    "coupon": Style("IBM Plex Mono SemiBold", 0.024, 0.006, INK, 0.752, box=GOLD),
}
FADE_MS, TYPE_SECONDS = 300, 0.045
ADVANCE = 0.6  # IBM Plex Mono: every glyph is 0.6 em wide, so a line's width is known before rendering
MAX_WIDTH = 0.80  # of the frame width (Reels side margins and the right-hand buttons)
TRACK_FROM = 0.004  # "track" opens the letters by this much (fraction of the height): trailer-style


def _clock(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _clean(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\\", "/").replace("\n", "\\N")


def _style_line(name: str, s: Style, height: int) -> str:
    size, spacing = round(s.size * height), round(s.spacing * height, 1)
    border, outline, back = (3, round(s.size * height * 0.42), f"&H00{s.box}") if s.box else (1, 0, "&H00000000")
    outline_colour = f"&H00{s.box}" if s.box else "&H00000000"
    return (f"Style: {name},{s.font},{size},&H{s.alpha}{s.colour},&H{s.alpha}{s.colour},{outline_colour},{back},"
            f"0,0,0,0,100,100,{spacing},0,{border},{outline},0,5,0,0,0,1")


def fit_scale(text: str, s: Style, width: int, height: int) -> float:
    """1, or the factor that brings the longest line within MAX_WIDTH of the frame."""
    longest = max(len(line) for line in text.split("\n"))
    natural = longest * (ADVANCE * s.size + s.spacing) * height
    return min(1.0, MAX_WIDTH * width / natural) if natural else 1.0


def _events(title: Title, s: Style, start: float, end: float, x: int, y: int, size: tuple[int, int]) -> list[str]:
    width, height = size
    text = _clean(title.text)
    dur_ms = int((end - start) * 1000)
    fade = min(FADE_MS, dur_ms // 3)
    k = fit_scale(title.text, s, width, height)
    spacing = round(s.spacing * height * k, 1)
    tags = f"\\pos({x},{y})" + (f"\\fs{int(s.size * height * k)}\\fsp{spacing}" if k < 1 else "")  # floor: never wider
    if title.effect == "track":  # letters open up slowly while it fades in; the widest moment is the last
        narrow = round(max(s.spacing - TRACK_FROM, 0) * height * k, 1)
        tags += f"\\fsp{narrow}\\t(0,{dur_ms},\\fsp{spacing})\\fad({fade},{fade})"
    elif title.effect == "fade":
        tags += f"\\blur6\\t(0,{fade},\\blur0)\\fad({fade},{fade})"
    if title.effect != "type":
        return [f"Dialogue: 0,{_clock(start)},{_clock(end)},{title.style},,0,0,0,,{{{tags}}}{text}"]
    # Typewriter: the untyped rest stays laid out (transparent), so centred text never shifts.
    lines, chars = [], list(title.text)
    for i in range(1, len(chars) + 1):
        t0 = start + (i - 1) * TYPE_SECONDS
        t1 = start + i * TYPE_SECONDS if i < len(chars) else end
        if t0 >= end:
            break
        rest = _clean("".join(chars[i:]))
        tail = f"\\fad(0,{fade})" if i == len(chars) else ""
        lines.append(f"Dialogue: 0,{_clock(t0)},{_clock(min(t1, end))},{title.style},,0,0,0,,"
                     f"{{{tags}{tail}}}{_clean(''.join(chars[:i]))}{{\\alpha&HFF&}}{rest}")
    return lines


def build_ass(cfg: CortexConfig, directory: Path, spec: LessonSpec, timings: Timings, size: tuple[int, int]) -> str:
    width, height = size
    anchors = Anchors(cfg, directory, spec, timings)
    used = sorted({t.style for t in spec.titles})
    unknown = [s for s in used if s not in STYLES]
    if unknown:
        raise LessonError(f"unknown title style(s) {unknown}; known: {sorted(STYLES)}")
    events = []
    for title in spec.titles:
        start = anchors.time(title.at, title.offset)
        end = anchors.time(title.until, title.until_offset) if title.until else start + title.duration
        end = min(end, timings.total)
        if end <= start:
            raise LessonError(f"title {title.text!r}: ends before it starts ({start:.2f}s → {end:.2f}s)")
        s = STYLES[title.style]
        events += _events(title, s, start, end, width // 2, round((title.y or s.y) * height), size)
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        *(_style_line(name, STYLES[name], height) for name in used), "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text", *events, ""])


def filter_path(path: Path) -> str:
    """Escape a path for a filtergraph argument (same rules as render.service)."""
    value = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    for character in ",;[]":
        value = value.replace(character, f"\\{character}")
    return value
