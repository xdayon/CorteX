"""Gera overlays de legenda e headline como um arquivo .ass (libass), queimado pelo
FFmpeg na mesma passagem de encode. Substitui o overlay alpha renderizado pelo
Remotion (Chrome headless), preservando a mesma aparência: legendas em Montserrat
bold uppercase com contorno/sombra/karaokê, e headline em tarjas brancas com
selo de aspas. A geometria replica apps/remotion/src/CaptionLayer.tsx,
captionLayout.ts e HeadlineLayer.tsx para que trocar o renderer não mude o layout.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
import struct
from pathlib import Path

from PIL import ImageFont

from cortex.render.captions import CaptionCue, TimelineWord
from cortex.render.schemas import RenderCaptionSettings, RenderHeadlineSettings, RenderSettings

ASS_GENERATOR_VERSION = 4

# B2: mapeamento explícito família+peso -> arquivo de fonte. Nunca substituímos
# silenciosamente uma família desconhecida por outra.
_CAPTION_FONT_FILES: dict[tuple[str, int], str] = {
    ("montserrat", 900): "Montserrat-Black.otf",
    ("montserrat", 400): "Montserrat-Regular.otf",
}
_HEADLINE_FONT_FILES: dict[str, str] = {
    "montserrat": "Montserrat-ExtraBold.otf",
}
# Nome de família alternativo que o fontconfig/libass reconhece para cada
# arquivo estático (confirmado via `fc-scan`), permitindo referenciar o peso
# exato sem depender da flag Bold do estilo ASS.
_ASS_FONT_NAMES: dict[str, str] = {
    "Montserrat-Black.otf": "Montserrat Black",
    "Montserrat-Regular.otf": "Montserrat",
    "Montserrat-ExtraBold.otf": "Montserrat ExtraBold",
}


class AssOverlayError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ResolvedFont:
    filename: str
    path: Path
    ass_name: str
    sha256: str


def _load_font(fonts_dir: Path, filename: str) -> ResolvedFont:
    path = fonts_dir / filename
    if not path.is_file():
        raise AssOverlayError(f"arquivo de fonte ausente: {path}")
    return ResolvedFont(
        filename=filename, path=path,
        ass_name=_ASS_FONT_NAMES.get(filename, filename), sha256=_sha256_file(path),
    )


def resolve_caption_font(fonts_dir: Path, family: str, weight: int) -> ResolvedFont:
    key = (family.strip().casefold(), weight)
    filename = _CAPTION_FONT_FILES.get(key)
    if filename is None:
        raise AssOverlayError(
            f"família de fonte não suportada para legendas: {family!r} peso {weight}"
        )
    return _load_font(fonts_dir, filename)


def resolve_headline_font(fonts_dir: Path, family: str) -> ResolvedFont:
    filename = _HEADLINE_FONT_FILES.get(family.strip().casefold())
    if filename is None:
        raise AssOverlayError(f"família de fonte não suportada para headline: {family!r}")
    return _load_font(fonts_dir, filename)


# ---------------------------------------------------------------------------
# Medição de glifos (Pillow, mesmo arquivo de fonte usado pelo libass)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4096)
def _measure_cached(font_path: str, text: str, size: int) -> float:
    font = ImageFont.truetype(font_path, size)
    bbox = font.getbbox(text)
    return float(bbox[2] - bbox[0]) if bbox else 0.0


def _measure(font_path: Path, text: str, size: float) -> float:
    return _measure_cached(str(font_path), text, max(1, round(size)))


@dataclass(frozen=True)
class FontMetrics:
    """Métricas verticais em em. O libass dimensiona a fonte pela altura
    win (usWinAscent+usWinDescent); o navegador da prévia usa hhea. Na
    Montserrat win≈1,562em e hhea≈1,219em, então um Fontsize igual ao
    tamanho CSS desenhava as letras com ~64% do tamanho da prévia."""
    win_ascent: float
    win_descent: float
    ascent: float
    descent: float

    @property
    def ass_scale(self) -> float:
        return self.win_ascent + self.win_descent


@lru_cache(maxsize=16)
def font_metrics(font_path: Path) -> FontMetrics:
    data = Path(font_path).read_bytes()
    tables: dict[bytes, int] = {}
    for index in range(struct.unpack(">H", data[4:6])[0]):
        tag, _checksum, offset, _length = struct.unpack(">4sIII", data[12 + 16 * index:28 + 16 * index])
        tables[tag] = offset
    try:
        head, os2, hhea = tables[b"head"], tables[b"OS/2"], tables[b"hhea"]
    except KeyError as exc:
        raise AssOverlayError(f"fonte sem tabela de métricas: {Path(font_path).name}") from exc
    units = struct.unpack(">H", data[head + 18:head + 20])[0]
    win_ascent, win_descent = struct.unpack(">HH", data[os2 + 74:os2 + 78])
    ascent, descent = struct.unpack(">hh", data[hhea + 4:hhea + 8])
    return FontMetrics(win_ascent / units, win_descent / units, ascent / units, -descent / units)


def ass_font_size(css_px: float, font_path: Path) -> int:
    """Fontsize do .ass que desenha glifos com o mesmo em da prévia CSS."""
    return max(1, round(css_px * font_metrics(font_path).ass_scale))


def _display_text(text: str, uppercase: bool) -> str:
    return text.upper() if uppercase else text


def escape_ass_text(text: str) -> str:
    """Escapa os únicos caracteres com significado especial no texto de um
    Dialogue ASS: barra invertida (início de override) e chaves (bloco de tags)."""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


# ---------------------------------------------------------------------------
# Cores: #RRGGBB[AA] -> &HAABBGGRR (alfa ASS é invertido: 00 = opaco)
# ---------------------------------------------------------------------------

def hex_to_ass(color: str) -> str:
    value = color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    a = int(value[6:8], 16) if len(value) == 8 else 255
    alpha = 255 - a
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def hex_to_ass_bgr(color: str) -> str:
    """Formato usado por overrides inline (`\\c&Hbbggrr&`), sem canal alfa."""
    value = color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def _ass_time(seconds: float) -> str:
    total_cs = max(0, round(seconds * 100))
    hours, remainder = divmod(total_cs, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centis = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _dialogue(style: str, start: float, end: float, text: str, layer: int = 0) -> str:
    return f"Dialogue: {layer},{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}"


# ---------------------------------------------------------------------------
# Paginação de legendas — porta 1:1 de apps/remotion/src/captionLayout.ts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CaptionPage:
    start: float
    end: float
    lines: tuple[tuple[TimelineWord, ...], ...]
    font_size: float


def paginate_cue(
    cue: CaptionCue, *, max_width: float, font_size: float, gap: float,
    font_path: Path, uppercase: bool,
) -> list[CaptionPage]:
    words = cue.words
    if not words:
        return []

    def word_width(word: TimelineWord, size: float) -> float:
        return _measure(font_path, _display_text(word.text, uppercase), size)

    longest = max(word_width(word, font_size) for word in words)
    effective = min(font_size, font_size * max_width / max(1.0, longest))
    groups: list[list[list[TimelineWord]]] = []
    lines: list[list[TimelineWord]] = [[]]
    line_width = 0.0
    for word in words:
        width = word_width(word, effective)
        line = lines[-1]
        if line and line_width + gap + width > max_width:
            if len(lines) == 2:
                groups.append(lines)
                lines = [[]]
            else:
                lines.append([])
            line = lines[-1]
            line_width = 0.0
        line_width += (gap if line else 0.0) + width
        line.append(word)
    if lines[0]:
        groups.append(lines)
    pages: list[CaptionPage] = []
    for index, group in enumerate(groups):
        start = cue.start if index == 0 else group[0][0].start
        end = groups[index + 1][0][0].start if index + 1 < len(groups) else cue.end
        pages.append(CaptionPage(
            start=start, end=end, lines=tuple(tuple(line) for line in group), font_size=effective,
        ))
    return pages


# Uma página = um único Dialogue. A palavra falada muda de cor (e, no "pop",
# cresce) por \\t dentro do próprio evento, com tempos relativos ao início da
# página. Emitir um evento por palavra fazia o libass recriar a legenda a cada
# troca, e o \\fad de cada evento a fazia piscar.
_POP_MS = 80


def _caption_text(
    page: CaptionPage, caption: RenderCaptionSettings, *, karaoke: bool, pop: bool,
) -> str:
    highlight = hex_to_ass_bgr(caption.karaoke_color)
    base = hex_to_ass_bgr(caption.text_color)
    line_strings = []
    for line in page.lines:
        parts = []
        for word in line:
            display = escape_ass_text(_display_text(word.text, caption.uppercase))
            start = max(page.start, word.start)
            end = min(page.end, word.end)
            if not karaoke or end <= start:
                parts.append(display)
                continue
            on = max(0, round((start - page.start) * 1000))
            off = max(on + 1, round((end - page.start) * 1000))
            tags = f"\\c{base}\\t({on},{on + 1},\\c{highlight})\\t({off},{off + 1},\\c{base})"
            if pop:
                grow = min(on + _POP_MS, off)
                tags += (
                    f"\\t({on},{grow},\\fscx108\\fscy108)"
                    f"\\t({off},{off + 1},\\fscx100\\fscy100)"
                )
            parts.append(f"{{{tags}}}{display}{{\\r}}")
        line_strings.append(" ".join(parts))
    return "\\N".join(line_strings)


def caption_bbox(*, width: int, height: int, font_size: int, position_y: float) -> tuple[float, float, float, float]:
    """Bounding box do bloco de legenda, nas mesmas coordenadas usadas para
    posicionar o Dialogue no .ass (usado pelo QA de safe zone, B6)."""
    scale = min(width / 1080, height / 1920)
    box_height = (font_size * 2 * 1.14 + 8 + 20) * scale
    center_y = height * position_y
    return (width * 0.075, center_y - box_height / 2, width * 0.925, center_y + box_height / 2)


def _build_caption_events(
    caption: RenderCaptionSettings, cues: list[CaptionCue], width: int, height: int, font: ResolvedFont,
) -> tuple[list[str], tuple[float, float, float, float]]:
    scale = min(width / 1080, height / 1920)
    max_width = width * 0.85 - 100 * scale
    base_font_size = caption.font_size * scale
    gap = 14 * scale
    center_x = width / 2
    center_y = height * caption.position_y
    fade_ms = round(caption.animation.duration_seconds * 1000) if caption.animation.style == "fade" else 0
    pop = caption.animation.style == "pop"
    events: list[str] = []
    for cue in cues:
        pages = paginate_cue(
            cue, max_width=max_width, font_size=base_font_size, gap=gap,
            font_path=font.path, uppercase=caption.uppercase,
        )
        for page in pages:
            tags = (
                f"{{\\q2\\an5\\pos({center_x:.2f},{center_y:.2f})"
                f"\\fs{ass_font_size(page.font_size, font.path)}}}"
            )
            if fade_ms:
                # Como no Remotion: só entrada. Um fade de saída faria a legenda
                # apagar e acender entre páginas consecutivas.
                tags += f"{{\\fad({fade_ms},0)}}"
            text = _caption_text(page, caption, karaoke=caption.karaoke, pop=pop)
            events.append(_dialogue("Caption", page.start, page.end, tags + text))
    bbox = caption_bbox(
        width=width, height=height, font_size=caption.font_size, position_y=caption.position_y,
    )
    return events, bbox


# ---------------------------------------------------------------------------
# Headline — porta de apps/remotion/src/HeadlineLayer.tsx
# ---------------------------------------------------------------------------

def _wrap_headline(paragraph: str, font_path: Path, size: float, max_width: float) -> list[str]:
    words = paragraph.split()
    if not words:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and _measure(font_path, candidate, size) > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def headline_bbox(
    *, width: int, height: int, font_size: int, line_count: int,
) -> tuple[float, float, float, float]:
    scale = min(width / 1080, height / 1920)
    line_height = font_size * scale * 1.65
    badge_height = 50 * scale
    top = height * 0.105
    bottom = top + badge_height + 14 * scale + max(1, line_count) * line_height
    return (width * 0.075, top, width * 0.925, bottom)


def _build_headline_events(
    headline: RenderHeadlineSettings, text: str, width: int, height: int, font: ResolvedFont,
) -> tuple[list[str], tuple[float, float, float, float]]:
    scale = min(width / 1080, height / 1920)
    max_width = width * 0.85
    font_size = headline.font_size * scale
    line_height = font_size * 1.65
    box_left = width * 0.075
    badge_width, badge_height = 70 * scale, 50 * scale
    badge_top = height * 0.105
    text_top = badge_top + badge_height + 14 * scale

    lines: list[str] = []
    for paragraph in text.split("\n"):
        lines.extend(_wrap_headline(paragraph, font.path, font_size, max_width))

    duration_ms = round(headline.duration_seconds * 1000)
    animation = headline.animation
    anim_ms = min(round(animation.duration_seconds * 1000), duration_ms)
    fad_in = anim_ms if animation.entrance == "fade" else 0
    fad_out = anim_ms if animation.exit == "fade" else 0
    slide_offset = 28 * scale

    # Porta do CSS da prévia: line-height 1.65em, faixa = área de conteúdo
    # (hhea) + padding 0.10em vertical e 0.22em horizontal. As faixas são
    # retângulos vetoriais; o BorderStyle 3 do libass usaria a altura win e
    # sobreporia as linhas.
    metrics = font_metrics(font.path)
    pad_x, pad_y = 0.22 * font_size, 0.10 * font_size
    half_leading = (1.65 - metrics.ascent - metrics.descent) * font_size / 2

    def animated(x: float, y: float) -> str:
        tags = f"\\pos({x:.2f},{y:.2f})"
        if animation.entrance == "slide":
            tags = f"\\move({x:.2f},{y + slide_offset:.2f},{x:.2f},{y:.2f},0,{anim_ms})"
        elif animation.exit == "slide":
            tags = (
                f"\\move({x:.2f},{y:.2f},{x:.2f},{y + slide_offset:.2f},"
                f"{max(0, duration_ms - anim_ms)},{duration_ms})"
            )
        if fad_in or fad_out:
            tags += f"\\fad({fad_in},{fad_out})"
        return tags

    events: list[str] = []
    for index, line_text in enumerate(lines):
        if not line_text:
            continue
        baseline = text_top + index * line_height + half_leading + metrics.ascent * font_size
        strip_top = baseline - metrics.ascent * font_size - pad_y
        strip_w = _measure(font.path, line_text, font_size) + 2 * pad_x
        strip_h = (metrics.ascent + metrics.descent) * font_size + 2 * pad_y
        events.append(_dialogue(
            "Strip", 0, headline.duration_seconds,
            f"{{\\an7{animated(box_left, strip_top)}\\p1}}"
            f"m 0 0 l {strip_w:.0f} 0 l {strip_w:.0f} {strip_h:.0f} l 0 {strip_h:.0f}{{\\p0}}",
        ))
        # \\an1 ancora a base da caixa win do libass: baseline + winDescent.
        text_y = baseline + metrics.win_descent * font_size
        events.append(_dialogue(
            "Headline", 0, headline.duration_seconds,
            f"{{\\q2\\an1{animated(box_left + pad_x, text_y)}}}" + escape_ass_text(line_text),
            layer=1,
        ))

    badge_tags = (
        f"{{\\an7\\pos({box_left:.2f},{badge_top:.2f})\\p1}}"
        f"m 0 0 l {badge_width:.0f} 0 l {badge_width:.0f} {badge_height:.0f} l 0 {badge_height:.0f}{{\\p0}}"
    )
    events.append(_dialogue("Badge", 0, headline.duration_seconds, badge_tags))
    glyph_x, glyph_y = box_left + badge_width / 2, badge_top + badge_height / 2
    events.append(_dialogue(
        "BadgeGlyph", 0, headline.duration_seconds, f"{{\\an5\\pos({glyph_x:.2f},{glyph_y + 14 * scale:.2f})}}“",
    ))

    non_empty_lines = max(1, len([line for line in lines if line]))
    bbox = headline_bbox(width=width, height=height, font_size=headline.font_size, line_count=non_empty_lines)
    return events, bbox


# ---------------------------------------------------------------------------
# Documento .ass completo
# ---------------------------------------------------------------------------

def _style_line(
    name: str, font_name: str, size: int, *, primary: str, secondary: str, outline: str, back: str,
    border_style: int, outline_px: float, shadow_px: float, alignment: int = 5,
) -> str:
    return (
        f"Style: {name},{font_name},{size},{primary},{secondary},{outline},{back},"
        f"0,0,0,0,100,100,0,0,{border_style},{outline_px},{shadow_px},{alignment},10,10,10,1"
    )


def _render_document(
    width: int, height: int, settings: RenderSettings,
    caption_font: ResolvedFont | None, headline_font: ResolvedFont | None,
    caption_events: list[str], headline_events: list[str],
) -> str:
    scale = min(width / 1080, height / 1920)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes", "WrapStyle: 2", "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
    ]
    caption = settings.captions
    if caption_font is not None:
        lines.append(_style_line(
            "Caption", caption_font.ass_name, ass_font_size(caption.font_size * scale, caption_font.path),
            primary=hex_to_ass(caption.text_color), secondary=hex_to_ass(caption.text_color),
            outline=hex_to_ass(caption.outline_color), back=hex_to_ass(caption.shadow_color),
            border_style=1, outline_px=(3 if caption.outline else 0), shadow_px=(4 if caption.shadow else 0),
        ))
    headline = settings.headline
    if headline_font is not None:
        lines.append(_style_line(
            "Headline", headline_font.ass_name, ass_font_size(headline.font_size * scale, headline_font.path),
            primary=hex_to_ass(headline.text_color), secondary=hex_to_ass(headline.text_color),
            outline=hex_to_ass(headline.text_color), back=hex_to_ass(headline.text_color),
            border_style=1, outline_px=0, shadow_px=0, alignment=1,
        ))
        lines.append(_style_line(
            "Strip", headline_font.ass_name, 1,
            primary=hex_to_ass(headline.strip_color), secondary=hex_to_ass(headline.strip_color),
            outline=hex_to_ass(headline.strip_color), back=hex_to_ass(headline.strip_color),
            border_style=1, outline_px=0, shadow_px=0, alignment=7,
        ))
        lines.append(_style_line(
            "Badge", headline_font.ass_name, 1,
            primary=hex_to_ass(headline.burst_color), secondary=hex_to_ass(headline.burst_color),
            outline=hex_to_ass(headline.burst_color), back=hex_to_ass(headline.burst_color),
            border_style=1, outline_px=0, shadow_px=0, alignment=7,
        ))
        lines.append(_style_line(
            "BadgeGlyph", headline_font.ass_name, max(1, round(64 * scale)),
            primary="&H00FFFFFF", secondary="&H00FFFFFF", outline="&H00FFFFFF", back="&H00FFFFFF",
            border_style=1, outline_px=0, shadow_px=0, alignment=5,
        ))
    lines.extend([
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])
    lines.extend(caption_events)
    lines.extend(headline_events)
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class AssOverlayResult:
    ass_text: str
    content_hash: str
    generator_version: int
    caption_font: ResolvedFont | None
    headline_font: ResolvedFont | None
    caption_bbox: tuple[float, float, float, float] | None
    headline_bbox: tuple[float, float, float, float] | None


def build_ass_overlay(
    settings: RenderSettings, cues: list[CaptionCue], *, fonts_dir: Path, headline_text: str | None,
) -> AssOverlayResult:
    width, height = settings.canvas.width, settings.canvas.height
    caption_font: ResolvedFont | None = None
    headline_font: ResolvedFont | None = None
    caption_events: list[str] = []
    headline_events: list[str] = []
    caption_bbox_value: tuple[float, float, float, float] | None = None
    headline_bbox_value: tuple[float, float, float, float] | None = None

    if settings.captions.enabled and cues:
        caption_font = resolve_caption_font(
            fonts_dir, settings.captions.font_family, settings.captions.font_weight
        )
        caption_events, caption_bbox_value = _build_caption_events(
            settings.captions, cues, width, height, caption_font
        )
    if settings.headline.enabled and headline_text:
        headline_font = resolve_headline_font(fonts_dir, settings.headline.font_family)
        headline_events, headline_bbox_value = _build_headline_events(
            settings.headline, headline_text, width, height, headline_font
        )

    ass_text = _render_document(
        width, height, settings, caption_font, headline_font, caption_events, headline_events,
    )
    payload = {
        "generator_version": ASS_GENERATOR_VERSION,
        "settings": settings.model_dump(mode="json"),
        "headline_text": headline_text,
        "cues": [
            {
                "start": cue.start, "end": cue.end, "text": cue.text,
                "words": [{"start": w.start, "end": w.end, "text": w.text} for w in cue.words],
            }
            for cue in cues
        ],
        "caption_font_sha256": caption_font.sha256 if caption_font else None,
        "headline_font_sha256": headline_font.sha256 if headline_font else None,
    }
    content_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return AssOverlayResult(
        ass_text=ass_text, content_hash=content_hash, generator_version=ASS_GENERATOR_VERSION,
        caption_font=caption_font, headline_font=headline_font,
        caption_bbox=caption_bbox_value, headline_bbox=headline_bbox_value,
    )
