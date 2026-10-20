from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.render.ass_overlay import (
    AssOverlayError,
    build_ass_overlay,
    caption_bbox,
    caption_max_width,
    escape_ass_text,
    headline_bbox,
    hex_to_ass,
    hex_to_ass_bgr,
    paginate_cue,
    resolve_caption_font,
    resolve_headline_font,
)
from cortex.render.captions import CaptionCue, TimelineWord
from cortex.render.safe_zones import SAFE_ZONES
from cortex.render.schemas import (
    RenderCanvasSettings,
    RenderCaptionSettings,
    RenderHeadlineAnimationSettings,
    RenderHeadlineSettings,
    RenderSettings,
    RenderSubtitleSettings,
)

FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
FRAMES_DIR = Path(
    "/tmp/claude-1000/-home-dx-Projects-CorteX/1632565b-04a6-4692-aa89-3a1f71313598/"
    "scratchpad/ass_frames"
)


def _word(start: float, end: float, text: str, segment: int = 0) -> TimelineWord:
    return TimelineWord(start=start, end=end, text=text, segment=segment)


def _settings(**overrides) -> RenderSettings:
    captions = RenderCaptionSettings(
        enabled=True, font_family="Montserrat", font_weight=900, font_size=48,
        words_per_cue=5, outline=True, shadow=True, karaoke=True,
    )
    headline = RenderHeadlineSettings(
        enabled=True, text="ALGO IMPORTANTE ACONTECEU AGORA", font_family="Montserrat",
        font_size=64, duration_seconds=1.0,
        animation=RenderHeadlineAnimationSettings(entrance="fade", exit="fade", duration_seconds=0.2),
    )
    defaults = {
        "encoder": "libx264", "canvas": RenderCanvasSettings(width=1080, height=1920, fps=30),
        "captions": captions, "headline": headline,
        "subtitles": RenderSubtitleSettings(sidecar_srt=False),
    }
    defaults.update(overrides)
    return RenderSettings(**defaults)


# ---------------------------------------------------------------------------
# Colour conversion
# ---------------------------------------------------------------------------

def test_hex_to_ass_opaque_white_matches_legacy_constant():
    assert hex_to_ass("#FFFFFF") == "&H00FFFFFF"


def test_hex_to_ass_inverts_alpha_channel():
    # AA=FF (opaque em CSS) -> alfa ASS 00; AA=00 (transparente) -> alfa ASS FF.
    assert hex_to_ass("#10171900") == "&HFF191710"
    assert hex_to_ass("#101719FF") == "&H00191710"


def test_hex_to_ass_bgr_has_no_alpha_component():
    assert hex_to_ass_bgr("#2CE4D7") == "&HD7E42C&"


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------

def test_escape_ass_text_escapes_braces_and_backslash():
    assert escape_ass_text("a{b}c\\d") == "a\\{b\\}c\\\\d"


def test_escape_ass_text_leaves_plain_text_untouched():
    assert escape_ass_text("ISSO AQUI") == "ISSO AQUI"


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_paginate_cue_keeps_every_word_and_never_exceeds_two_lines():
    words = [_word(i * 0.3, i * 0.3 + 0.25, f"PALAVRA{i}") for i in range(12)]
    cue = CaptionCue(start=words[0].start, end=words[-1].end, text="x", words=tuple(words))
    font = resolve_caption_font(FONTS_DIR, "Montserrat", 900)
    pages = paginate_cue(cue, max_width=300, font_size=48, gap=14, font_path=font.path, uppercase=True)
    assert pages
    total_words = sum(len(line) for page in pages for line in page.lines)
    assert total_words == len(words)
    assert all(len(page.lines) <= 2 for page in pages)


def test_paginate_cue_shrinks_font_for_a_very_long_word():
    words = [_word(0.0, 1.0, "SUPERCALIFRAGILISTICEXPIALIDOCIOUS")]
    cue = CaptionCue(start=0.0, end=1.0, text=words[0].text, words=tuple(words))
    font = resolve_caption_font(FONTS_DIR, "Montserrat", 900)
    pages = paginate_cue(cue, max_width=300, font_size=48, gap=14, font_path=font.path, uppercase=True)
    assert len(pages) == 1
    assert pages[0].font_size < 48
    assert pages[0].lines == ((words[0],),)


def _sentence_pages(text: str, font_size: int, line_breaks: frozenset[int] = frozenset()) -> list[list[str]]:
    words = [TimelineWord(i * 0.3, i * 0.3 + 0.25, token, 0, i) for i, token in enumerate(text.split())]
    cue = CaptionCue(start=0.0, end=words[-1].end, text=text, words=tuple(words))
    font = resolve_caption_font(FONTS_DIR, "Montserrat", 900)
    max_width, _ = caption_max_width(width=1080, height=1920, font_size=font_size, position_y=0.64)
    pages = paginate_cue(cue, max_width=max_width, font_size=font_size, gap=14, font_path=font.path,
                         uppercase=True, line_breaks=line_breaks)
    return [[" ".join(word.text for word in line) for line in page.lines] for page in pages]


def test_paginate_cue_balances_the_two_lines_instead_of_leaving_one_word_alone():
    # Preenchendo a primeira linha até o limite, "TÁ" ficava sozinho na segunda.
    assert _sentence_pages("tem muita coisa que tá", 56) == [["tem muita", "coisa que tá"]]


def test_paginate_cue_keeps_a_short_cue_on_one_line():
    assert _sentence_pages("eu acho", 56) == [["eu acho"]]


def test_paginate_cue_prefers_breaking_after_punctuation():
    assert _sentence_pages("a gente não sabe, mas isso vai mudar tudo", 56) == [
        ["a gente não sabe,"], ["mas isso vai", "mudar tudo"],
    ]


def test_paginate_cue_spreads_words_across_pages_without_an_orphan_page():
    pages = _sentence_pages("estamos no meio de várias revoluções", 64)
    assert pages == [["estamos no meio"], ["de várias", "revoluções"]]


def test_paginate_cue_starts_a_line_at_a_manual_break():
    assert _sentence_pages("tem muita coisa que tá", 56, frozenset({3})) == [["tem muita coisa", "que tá"]]
    # A quebra na primeira palavra da legenda não muda nada.
    assert _sentence_pages("tem muita coisa que tá", 56, frozenset({0})) == [["tem muita", "coisa que tá"]]


def test_manual_line_break_reaches_the_ass_document():
    words = [TimelineWord(i * 0.3, i * 0.3 + 0.25, token, 0, i)
             for i, token in enumerate("tem muita coisa que tá".split())]
    cue = CaptionCue(start=0.0, end=1.5, text="tem muita coisa que tá", words=tuple(words))
    base = _settings()
    captions = base.captions.model_copy(update={"karaoke": False, "line_break_word_indices": [3]})
    result = build_ass_overlay(base.model_copy(update={"captions": captions}), [cue],
                               fonts_dir=FONTS_DIR, headline_text=None)
    assert "TEM MUITA COISA\\NQUE TÁ" in result.ass_text


# ---------------------------------------------------------------------------
# Karaoke breakpoints
# ---------------------------------------------------------------------------

def test_karaoke_page_is_a_single_event_with_timed_highlight():
    # Um evento por palavra fazia a legenda inteira piscar a cada troca.
    words = [_word(0.0, 0.5, "UM"), _word(0.5, 1.0, "DOIS"), _word(1.2, 1.6, "TRES")]
    cue = CaptionCue(start=0.0, end=1.6, text="um dois tres", words=tuple(words))
    settings = _settings(headline=RenderHeadlineSettings(enabled=False, text="", font_family="Montserrat", font_size=64, duration_seconds=1.0))
    result = build_ass_overlay(settings, [cue], fonts_dir=FONTS_DIR, headline_text=None)
    caption_events = [line for line in result.ass_text.splitlines() if line.startswith("Dialogue: 0,") and ",Caption," in line]
    assert len(caption_events) == 1
    assert "0:00:00.00,0:00:01.60,Caption" in caption_events[0]
    # Destaque de cada palavra acende e apaga por \\t relativo ao início da página.
    for on, off in ((0, 500), (500, 1000), (1200, 1600)):
        assert f"\\t({on},{on + 1}," in caption_events[0]
        assert f"\\t({off},{off + 1}," in caption_events[0]


def test_caption_fade_is_entrance_only():
    words = [_word(0.0, 0.5, "UM")]
    cue = CaptionCue(start=0.0, end=0.5, text="um", words=tuple(words))
    settings = _settings(headline=RenderHeadlineSettings(enabled=False, text="", font_family="Montserrat", font_size=64, duration_seconds=1.0))
    settings = settings.model_copy(update={"captions": settings.captions.model_copy(update={
        "animation": settings.captions.animation.model_copy(update={"style": "fade", "duration_seconds": 0.2}),
    })})
    result = build_ass_overlay(settings, [cue], fonts_dir=FONTS_DIR, headline_text=None)
    assert "\\fad(200,0)" in result.ass_text


# ---------------------------------------------------------------------------
# Headline events bounded by duration
# ---------------------------------------------------------------------------

def test_headline_events_are_limited_to_the_duration_window():
    settings = _settings()
    settings = settings.model_copy(update={
        "headline": settings.headline.model_copy(update={"duration_seconds": 1.5}),
        "captions": settings.captions.model_copy(update={"enabled": False}),
    })
    result = build_ass_overlay(settings, [], fonts_dir=FONTS_DIR, headline_text="MANCHETE CURTA")
    headline_events = [
        line for line in result.ass_text.splitlines()
        if line.startswith("Dialogue:") and (",Headline," in line or ",Badge," in line or ",BadgeGlyph," in line)
    ]
    assert headline_events
    for line in headline_events:
        assert line.split(",")[1] == "0:00:00.00"
        assert line.split(",")[2] == "0:00:01.50"


# ---------------------------------------------------------------------------
# Safe-zone geometry
# ---------------------------------------------------------------------------

def test_safe_zone_geometry_flags_caption_too_close_to_the_bottom_edge():
    safe_zone = SAFE_ZONES["9:16"]
    bbox_ok = caption_bbox(width=1080, height=1920, font_size=48, position_y=0.78)
    bbox_bad = caption_bbox(width=1080, height=1920, font_size=48, position_y=0.98)

    def flagged(bbox):
        _, y1, _, y2 = bbox
        bottom_limit = (1.0 - safe_zone.bottom) * 1920
        top_px = safe_zone.top * 1920
        return y2 > bottom_limit or y1 < top_px

    assert flagged(bbox_ok) is False
    assert flagged(bbox_bad) is True


def test_headline_bbox_grows_with_line_count():
    one_line = headline_bbox(width=1080, height=1920, font_size=64, line_count=1)
    two_lines = headline_bbox(width=1080, height=1920, font_size=64, line_count=2)
    assert two_lines[3] > one_line[3]


# ---------------------------------------------------------------------------
# Unknown font family
# ---------------------------------------------------------------------------

def test_resolve_caption_font_rejects_unknown_family():
    with pytest.raises(AssOverlayError):
        resolve_caption_font(FONTS_DIR, "Comic Sans", 900)


def test_resolve_caption_font_rejects_unknown_weight():
    with pytest.raises(AssOverlayError):
        resolve_caption_font(FONTS_DIR, "Montserrat", 700)


def test_resolve_headline_font_rejects_unknown_family():
    with pytest.raises(AssOverlayError):
        resolve_headline_font(FONTS_DIR, "Comic Sans")


def test_build_ass_overlay_raises_precondition_style_error_for_unknown_family():
    settings = _settings(captions=RenderCaptionSettings(
        enabled=True, font_family="Comic Sans", font_weight=900, font_size=48, words_per_cue=5,
        outline=True,
    ))
    words = [_word(0.0, 0.5, "OI")]
    cue = CaptionCue(start=0.0, end=0.5, text="oi", words=tuple(words))
    with pytest.raises(AssOverlayError):
        build_ass_overlay(settings, [cue], fonts_dir=FONTS_DIR, headline_text=None)


# ---------------------------------------------------------------------------
# Real FFmpeg burn: decode + duration + pixel diff, plus PNG frames for review
# ---------------------------------------------------------------------------

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg/ffprobe required")
def test_real_ffmpeg_burns_ass_overlay_and_changes_caption_region_pixels(tmp_path: Path):
    words = [
        _word(0.2, 0.6, "ESSA"), _word(0.7, 1.1, "FRASE"), _word(1.2, 1.8, "AGORA"),
    ]
    cue = CaptionCue(start=0.2, end=1.8, text="essa frase agora", words=tuple(words))
    settings = _settings()
    result = build_ass_overlay(settings, [cue], fonts_dir=FONTS_DIR, headline_text=settings.headline.text)
    ass_path = tmp_path / "overlay.ass"
    ass_path.write_text(result.ass_text, encoding="utf-8")

    def render(path: Path, with_subtitles: bool) -> None:
        vf = (
            f"subtitles=filename='{ass_path.as_posix()}':fontsdir='{FONTS_DIR.as_posix()}'"
            if with_subtitles else "null"
        )
        subprocess.run([
            FFMPEG, "-y", "-v", "error", "-f", "lavfi",
            "-i", "color=c=0x1a1a1a:s=1080x1920:d=3:r=30",
            "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(path),
        ], check=True, timeout=120)

    with_captions = tmp_path / "with.mp4"
    without_captions = tmp_path / "without.mp4"
    render(with_captions, True)
    render(without_captions, False)

    for path in (with_captions, without_captions):
        probe = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        assert abs(float(probe.stdout.strip()) - 3.0) < 0.2

    def extract_frame(path: Path, out: Path, frame_index: int) -> None:
        subprocess.run([
            FFMPEG, "-y", "-v", "error", "-i", str(path),
            "-vf", f"select='eq(n\\,{frame_index})'", "-vframes", "1", str(out),
        ], check=True, timeout=60)

    from PIL import Image

    with_png = tmp_path / "with.png"
    without_png = tmp_path / "without.png"
    extract_frame(with_captions, with_png, 15)
    extract_frame(without_captions, without_png, 15)
    bbox = caption_bbox(width=1080, height=1920, font_size=settings.captions.font_size, position_y=settings.captions.position_y)
    x1, y1, x2, y2 = (max(0, int(v)) for v in bbox)
    region_with = Image.open(with_png).convert("RGB").crop((x1, y1, x2, y2))
    region_without = Image.open(without_png).convert("RGB").crop((x1, y1, x2, y2))
    assert list(region_with.getdata()) != list(region_without.getdata())

    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    extract_frame(with_captions, FRAMES_DIR / "caption_karaoke_active.png", 42)  # t=1.4s -> "AGORA" ativo
    extract_frame(with_captions, FRAMES_DIR / "caption_and_headline.png", 15)  # t=0.5s -> ambos visiveis

    headline_only_settings = settings.model_copy(update={
        "captions": settings.captions.model_copy(update={"enabled": False}),
        "headline": settings.headline.model_copy(update={"duration_seconds": 3.0}),
    })
    headline_only_result = build_ass_overlay(
        headline_only_settings, [], fonts_dir=FONTS_DIR, headline_text=headline_only_settings.headline.text,
    )
    headline_only_ass = tmp_path / "headline_only.ass"
    headline_only_ass.write_text(headline_only_result.ass_text, encoding="utf-8")
    headline_only_video = tmp_path / "headline_only.mp4"
    subprocess.run([
        FFMPEG, "-y", "-v", "error", "-f", "lavfi",
        "-i", "color=c=0x1a1a1a:s=1080x1920:d=3:r=30",
        "-vf", f"subtitles=filename='{headline_only_ass.as_posix()}':fontsdir='{FONTS_DIR.as_posix()}'",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(headline_only_video),
    ], check=True, timeout=120)
    extract_frame(headline_only_video, FRAMES_DIR / "headline_only.png", 15)

    print("PNG frames written to:", FRAMES_DIR)
