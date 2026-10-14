from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image

from asgi_client import ASGITestClient as TestClient
from cortex.api import create_app
from cortex.config import load_config
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.edit.schemas import (
    EditPlanDiagnostics,
    EditPlanDocument,
    EditQualityReport,
    EditSegment,
    EditTransition,
)
from cortex.render.ass_overlay import (
    _layout_page,
    allowed_font_files,
    build_ass_overlay,
    caption_bbox,
    caption_max_width,
    caption_pages,
    resolve_caption_font,
)
from cortex.render.captions import (
    CaptionCue,
    TimelineWord,
    build_timeline_words,
    piece_junctions,
)
from cortex.render.safe_zones import SAFE_ZONES_VERSION, occlusion_issues
from cortex.render.schemas import (
    RenderAnimationSettings,
    RenderCanvasSettings,
    RenderCaptionSettings,
    RenderHeadlineSettings,
    RenderSettings,
    RenderSubtitleSettings,
    RenderTransitionStyleSettings,
)
from cortex.render.service import RenderService
from cortex.transcribe.schemas import (
    EngineInfo,
    TranscriptDocument,
    TranscriptSegment,
    TranscriptWord,
)

ROOT = Path(__file__).resolve().parents[1]
FONTS_DIR = ROOT / "assets" / "fonts"


def _caption(**overrides) -> RenderCaptionSettings:
    defaults = dict(
        enabled=True, font_family="Montserrat", font_weight=900, font_size=56,
        words_per_cue=6, outline=False, shadow=False, karaoke=True, position_y=0.62,
    )
    defaults.update(overrides)
    return RenderCaptionSettings(**defaults)


def _settings(captions: RenderCaptionSettings | None = None, **overrides) -> RenderSettings:
    defaults = dict(
        encoder="libx264", canvas=RenderCanvasSettings(width=1080, height=1920, fps=30),
        captions=captions or _caption(),
        headline=RenderHeadlineSettings(
            enabled=False, text="", font_family="Montserrat", font_size=64, duration_seconds=2.0,
        ),
        subtitles=RenderSubtitleSettings(sidecar_srt=False),
    )
    defaults.update(overrides)
    return RenderSettings(**defaults)


def _cue(texts: list[str], step: float = 0.4) -> CaptionCue:
    words = tuple(
        TimelineWord(
            start=round(i * step, 3), end=round((i + 1) * step, 3), text=text, segment=0, index=i,
        )
        for i, text in enumerate(texts)
    )
    return CaptionCue(0.0, round(len(texts) * step, 3), " ".join(texts), words)


TEXTS = ["Isso", "nunca", "funcionaria", "sem", "planejamento", "extremo"]


def _ass(captions: RenderCaptionSettings | None = None, **kwargs) -> str:
    result = build_ass_overlay(
        _settings(captions), [_cue(TEXTS)], fonts_dir=FONTS_DIR, headline_text=None, **kwargs,
    )
    return result.ass_text


def _dialogues(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


# ---------------------------------------------------------------------------
# Timeline word index / junctions
# ---------------------------------------------------------------------------

def _transcript(words_per_segment: list[list[tuple[float, float, str]]]) -> TranscriptDocument:
    segments = []
    for number, words in enumerate(words_per_segment):
        segments.append(TranscriptSegment(
            id=number, start=words[0][0], end=words[-1][1], text=" ".join(w[2] for w in words),
            avg_logprob=-0.1, no_speech_prob=0.0,
            words=[TranscriptWord(start=s, end=e, word=w, probability=0.9) for s, e, w in words],
        ))
    return TranscriptDocument(
        language="pt", duration_seconds=60.0,
        engine=EngineInfo(
            model="fixture", requested_device="cpu", effective_device="cpu",
            requested_compute_type="int8", effective_compute_type="int8",
            batch_size=1, vad=True,
        ),
        segments=segments,
    )


def _plan(segments: list[EditSegment], duration: float, project_id="p", source_id="s",
          transcript_id="t") -> EditPlanDocument:
    return EditPlanDocument(
        project_id=project_id, source_asset_id=source_id, transcript_artifact_id=transcript_id,
        analysis_artifact_id="a", input_hash="h", clip_start=0.0,
        clip_end=max(segment.end for segment in segments), profile="balanced", segments=segments,
        timeline_duration_seconds=duration,
        diagnostics=EditPlanDiagnostics(
            profile="balanced", waveform_used=True, candidate_pauses=0, cuts=0,
            saved_seconds=0.0, crossfade=0.0, vad_used=True,
        ),
        quality=EditQualityReport(passed=True, issues=[], profile="balanced", degraded=False),
    )


def test_timeline_words_carry_global_transcript_index() -> None:
    transcript = _transcript([
        [(0.0, 0.4, "um"), (0.4, 0.8, "dois")],
        [(1.0, 1.4, "tres"), (1.4, 1.8, "quatro")],
    ])
    plan = _plan([
        EditSegment(start=0.4, end=1.8, timeline_order=0),
    ], 1.4)
    words = build_timeline_words(plan, transcript)
    assert [(w.text, w.index) for w in words] == [
        ("dois", 1), ("tres", 2), ("quatro", 3),
    ]


def test_piece_junctions_detect_only_known_piece_changes() -> None:
    segments = [
        EditSegment(start=0.0, end=2.0, timeline_order=0, piece_index=0),
        EditSegment(start=3.0, end=5.0, timeline_order=1, piece_index=0),
        EditSegment(start=10.0, end=12.0, timeline_order=2, piece_index=1),
        EditSegment(start=13.0, end=14.0, timeline_order=3, piece_index=None),
        EditSegment(start=20.0, end=21.0, timeline_order=4, piece_index=2),
    ]
    plan = _plan(segments, 7.0)
    assert piece_junctions(plan) == [(2, 4.0)]


def test_piece_junction_time_subtracts_incoming_transition() -> None:
    transition = EditTransition(video="fade", audio="fade", duration=0.5)
    segments = [
        EditSegment(start=0.0, end=2.0, timeline_order=0, piece_index=0, transition=transition),
        EditSegment(start=10.0, end=12.0, timeline_order=1, piece_index=1),
    ]
    plan = _plan(segments, 3.5)
    assert piece_junctions(plan) == [(1, 1.5)]


# ---------------------------------------------------------------------------
# Caption styles
# ---------------------------------------------------------------------------

def test_legacy_animation_pop_selects_pop_style_unless_style_is_explicit() -> None:
    legacy = _caption(animation=RenderAnimationSettings(style="pop", duration_seconds=0.2))
    assert legacy.style == "pop"
    explicit = _caption(
        style="classic", animation=RenderAnimationSettings(style="pop", duration_seconds=0.2),
    )
    assert explicit.style == "classic"
    assert _caption().style == "classic"


def test_classic_style_has_color_swap_but_no_scale_or_alpha_tags() -> None:
    text = "\n".join(_dialogues(_ass(_caption(style="classic"))))
    assert "\\t(" in text and "\\c&HD7E42C&" in text
    assert "\\fscx" not in text
    assert "\\alpha&HFF&" not in text
    assert "Style: Box" not in _ass(_caption(style="classic"))


def test_pop_style_scales_active_word_116_then_108_then_back() -> None:
    text = "\n".join(_dialogues(_ass(_caption(style="pop"))))
    assert "\\fscx116\\fscy116" in text
    assert "\\fscx108\\fscy108" in text
    first = re.search(r"\\t\((\d+),(\d+),\\fscx116", text)
    assert first and int(first.group(2)) - int(first.group(1)) == 70
    second = re.search(r"\\t\((\d+),(\d+),\\fscx108", text)
    assert second and int(second.group(2)) - int(second.group(1)) == 70


def test_word_reveal_hides_unspoken_words_and_reveals_in_60ms() -> None:
    text = "\n".join(_dialogues(_ass(_caption(style="word_reveal"))))
    assert "\\alpha&HFF&" in text
    reveal = re.search(r"\\t\((\d+),(\d+),\\alpha&H00&\)", text)
    assert reveal and int(reveal.group(2)) - int(reveal.group(1)) == 60
    # words never hide again before the page ends
    assert "\\alpha&HFF&)" not in text


def test_box_style_draws_vector_rectangles_below_the_text_layer() -> None:
    ass = _ass(_caption(style="box"))
    assert "Style: Box," in ass
    lines = _dialogues(ass)
    boxes = [line for line in lines if ",Box," in line]
    captions = [line for line in lines if ",Caption," in line]
    assert len(boxes) == len(TEXTS)
    assert all(line.startswith("Dialogue: 1,") and "\\p1" in line for line in boxes)
    assert all(line.startswith("Dialogue: 2,") for line in captions)
    assert "\\c&H121007&" in captions[0]


def test_emphasis_indices_map_by_word_index_and_are_normalized() -> None:
    captions = _caption(emphasis_word_indices=[3, 1, 3])
    assert captions.emphasis_word_indices == [1, 3]
    text = "\n".join(_dialogues(_ass(captions)))
    emphasised = re.findall(r"\{([^}]*)\}(\w+)", text)
    by_word = {word: tags for tags, word in emphasised}
    assert "\\c&H00D4FF&" in by_word["NUNCA"] and "\\fscx112" in by_word["NUNCA"]
    assert "\\c&H00D4FF&" in by_word["SEM"] and "\\fscx112" in by_word["SEM"]
    assert "\\fscx112" not in by_word["ISSO"]
    assert "\\c&HFFFFFF&" in by_word["ISSO"]


def test_emphasis_in_pop_style_peaks_at_122() -> None:
    text = "\n".join(_dialogues(_ass(_caption(style="pop", emphasis_word_indices=[1]))))
    assert "\\fscx122\\fscy122" in text


def test_emphasis_color_stays_when_word_is_active() -> None:
    text = "\n".join(_dialogues(_ass(_caption(emphasis_word_indices=[1]))))
    nunca = re.search(r"\{([^}]*)\}NUNCA", text).group(1)
    assert "\\c&HD7E42C&" not in nunca


def test_box_color_follows_emphasis_and_text_switches_to_box_text_color() -> None:
    ass = _ass(_caption(style="box", emphasis_word_indices=[1], box_text_color="#112233"))
    boxes = [line for line in _dialogues(ass) if ",Box," in line]
    assert "\\1c&H00D4FF&" in boxes[1]
    assert "\\1c&HD7E42C&" in boxes[0]
    assert "\\c&H332211&" in "\n".join(_dialogues(ass))


def test_caption_settings_reject_bad_emphasis_and_colors() -> None:
    with pytest.raises(ValueError):
        _caption(emphasis_word_indices=[-1])
    with pytest.raises(ValueError):
        _caption(emphasis_color="red")
    with pytest.raises(ValueError):
        _caption(emphasis_word_indices=list(range(2001)))


# ---------------------------------------------------------------------------
# Headline persistence
# ---------------------------------------------------------------------------

def _headline_ass(**headline) -> str:
    base = dict(
        enabled=True, text="TESTE", font_family="Montserrat", font_size=64, duration_seconds=2.0,
    )
    base.update(headline)
    settings = _settings(headline=RenderHeadlineSettings(**base))
    return build_ass_overlay(
        settings, [], fonts_dir=FONTS_DIR, headline_text="TESTE",
    ).ass_text


def test_timed_headline_fades_out_but_persistent_one_does_not() -> None:
    timed = _headline_ass()
    assert "\\fad(240,240)" in timed
    persistent = _headline_ass(persistent=True, duration_seconds=42.0)
    assert "\\fad(240,0)" in persistent
    assert "\\fad(240,240)" not in persistent
    assert "0:00:42.00" in persistent


def test_timed_headline_over_15_seconds_is_rejected_but_persistent_is_allowed() -> None:
    with pytest.raises(ValueError):
        RenderHeadlineSettings(
            enabled=True, font_family="Montserrat", font_size=64, duration_seconds=20.0,
        )
    ok = RenderHeadlineSettings(
        enabled=True, font_family="Montserrat", font_size=64, duration_seconds=20.0,
        persistent=True,
    )
    assert ok.persistent


# ---------------------------------------------------------------------------
# Junction flash
# ---------------------------------------------------------------------------

def test_flash_events_are_top_layer_rectangles_fading_out() -> None:
    settings = _settings(
        _caption(enabled=False),
        transitions=RenderTransitionStyleSettings(piece_junction="flash", flash_opacity=0.55),
    )
    result = build_ass_overlay(
        settings, [], fonts_dir=FONTS_DIR, headline_text=None, junctions=(1.0, 2.5),
    )
    flashes = [line for line in _dialogues(result.ass_text) if ",Flash," in line]
    assert len(flashes) == 2 and result.flash_count == 2
    assert flashes[0].startswith("Dialogue: 9,0:00:01.00,0:00:01.12,Flash")
    assert "\\fad(0,120)" in flashes[0] and "\\alpha&H73&" in flashes[0]
    assert "m 0 0 l 1080 0 l 1080 1920 l 0 1920" in flashes[0]
    assert "Style: Flash," in result.ass_text


def test_cut_and_zoom_junctions_draw_no_flash() -> None:
    for style in ("cut", "zoom"):
        settings = _settings(
            _caption(enabled=False), transitions=RenderTransitionStyleSettings(piece_junction=style),
        )
        result = build_ass_overlay(
            settings, [], fonts_dir=FONTS_DIR, headline_text=None, junctions=(1.0,),
        )
        assert result.flash_count == 0
        assert ",Flash," not in result.ass_text


# ---------------------------------------------------------------------------
# Safe zones v2
# ---------------------------------------------------------------------------

def test_safe_zone_version_is_two() -> None:
    assert SAFE_ZONES_VERSION == 2


def test_caption_narrows_only_when_its_span_touches_the_action_column() -> None:
    narrow, narrowed = caption_max_width(width=1080, height=1920, font_size=56, position_y=0.62)
    assert narrowed and narrow == pytest.approx(2 * 0.35 * 1080 - 16)
    wide, narrowed = caption_max_width(width=1080, height=1920, font_size=56, position_y=0.20)
    assert not narrowed and wide == pytest.approx(1080 * 0.85 - 100)
    _, narrowed = caption_max_width(width=1920, height=1080, font_size=56, position_y=0.62)
    assert not narrowed


def test_caption_bbox_reports_the_width_used_by_the_layout() -> None:
    left, _top, right, _bottom = caption_bbox(
        width=1080, height=1920, font_size=56, position_y=0.62,
    )
    max_width, _ = caption_max_width(width=1080, height=1920, font_size=56, position_y=0.62)
    assert right - left == pytest.approx(max_width)
    settings = _settings(_caption(font_size=56, words_per_cue=6, position_y=0.62))
    font = resolve_caption_font(FONTS_DIR, "Montserrat", 900)
    pages = caption_pages(settings.captions, [_cue(TEXTS)], 1080, 1920, font)
    from cortex.render.ass_overlay import _advance
    for page in pages:
        for line in page.lines:
            width = sum(_advance(font.path, w.text.upper(), page.font_size) for w in line)
            assert width <= max_width + 1
    # default layout (outside the action column) keeps today's box
    left, _t, right, _b = caption_bbox(width=1080, height=1920, font_size=56, position_y=0.2)
    assert (left, right) == pytest.approx((1080 * 0.075, 1080 * 0.925))


def test_occlusion_issue_codes_name_platform_and_rect() -> None:
    issues = occlusion_issues(
        canvas="9:16", width=1080, height=1920, element="caption",
        bbox=(100, 1000, 1000, 1100),
    )
    assert "caption_occluded:instagram_reels:right_actions" in issues
    assert "caption_occluded:tiktok:right_actions" in issues
    low = occlusion_issues(
        canvas="9:16", width=1080, height=1920, element="caption", bbox=(100, 1560, 900, 1700),
    )
    assert low == [
        "caption_occluded:instagram_reels:bottom_meta", "caption_occluded:tiktok:bottom_meta",
    ]
    top = occlusion_issues(
        canvas="9:16", width=1080, height=1920, element="headline", bbox=(80, 100, 900, 300),
    )
    assert top == [
        "headline_occluded:instagram_reels:top_bar", "headline_occluded:tiktok:top_tabs",
    ]
    assert occlusion_issues(
        canvas="9:16", width=1080, height=1920, element="caption", bbox=(100, 1000, 900, 1100),
    ) == []
    assert occlusion_issues(
        canvas="16:9", width=1920, height=1080, element="caption", bbox=(0, 0, 1920, 1080),
    ) == []


def test_libass_safe_zone_qa_flags_platform_occlusion() -> None:
    service = RenderService(load_config(), None)  # type: ignore[arg-type]
    from cortex.render.safe_zones import SAFE_ZONES
    bbox_ok = caption_bbox(width=1080, height=1920, font_size=56, position_y=0.62)
    assert service._safe_zone_issues_libass(
        caption_bbox=bbox_ok, headline_bbox=None, width=1080, height=1920,
        safe_zone=SAFE_ZONES["9:16"],
    ) == []
    bbox_low = caption_bbox(width=1080, height=1920, font_size=56, position_y=0.78)
    issues = service._safe_zone_issues_libass(
        caption_bbox=bbox_low, headline_bbox=None, width=1080, height=1920,
        safe_zone=SAFE_ZONES["9:16"],
    )
    assert "caption_occluded:tiktok:bottom_meta" in issues


# ---------------------------------------------------------------------------
# Overlay preview API
# ---------------------------------------------------------------------------

def _api_config(tmp_path: Path):
    base = load_config()
    return base.model_copy(update={
        "paths": base.paths.model_copy(update={
            "data_dir": tmp_path,
            "projects_dir": tmp_path / "projects",
            "cache_dir": tmp_path / "cache",
            "output_dir": tmp_path / "output",
            "database": tmp_path / "cortex.sqlite3",
        }),
    })


def _api_fixture(tmp_path: Path, word_count: int = 12, step: float = 0.4):
    config = _api_config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Overlay preview fixture")
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="m.mp4",
        stored_path="m.mp4", sha256="0" * 64, size_bytes=1,
    ))
    words = [
        (round(i * step, 3), round((i + 1) * step, 3), f"palavra{i}") for i in range(word_count)
    ]
    transcript = _transcript([words])
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(transcript.model_dump_json(), encoding="utf-8")
    transcript_artifact = domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project.id, source_asset_id=source.id, path=str(transcript_path),
        audio_sha256="a", engine="fixture", model="fixture", device="cpu",
        compute_type="int8", language="pt", vad=True, batch_size=1,
        duration_seconds=word_count * step,
    ))
    half = word_count // 2
    plan = _plan(
        [
            EditSegment(start=0.0, end=half * step, timeline_order=0, piece_index=0),
            EditSegment(start=half * step, end=word_count * step, timeline_order=1, piece_index=1),
        ],
        word_count * step, project.id, source.id, transcript_artifact.id,
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    plan_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="edit_plan", path=str(plan_path), input_hash="h",
    ))
    return config, project, plan_artifact


def _settings_json(**caption) -> dict:
    return _settings(_caption(**caption)).model_dump(mode="json")


def test_overlay_preview_returns_pages_with_global_indices_and_whitelisted_fonts(tmp_path) -> None:
    config, project, plan_artifact = _api_fixture(tmp_path)
    client = TestClient(create_app(config))
    response = client.post(
        f"/api/v1/projects/{project.id}/overlay-preview",
        json={
            "edit_plan_artifact_id": plan_artifact.id,
            "settings": _settings_json(emphasis_word_indices=[2], style="box"),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["generator_version"] == 5
    assert body["canvas"] == {"width": 1080, "height": 1920}
    assert body["timeline_duration_seconds"] == pytest.approx(4.8)
    assert body["junctions"] == [2.4]
    indices = [w["index"] for p in body["pages"] for line in p["lines"] for w in line]
    assert indices == sorted(indices) and indices[0] == 0 and indices[-1] == 11
    flagged = [w["index"] for p in body["pages"] for line in p["lines"] for w in line if w["emphasis"]]
    assert flagged == [2]
    assert body["pages"][0]["lines"][0][0]["text"] == "PALAVRA0"
    assert "Style: Box," in body["ass_text"]
    assert body["fonts"] and all(
        font["url"] == f"/api/v1/overlay-fonts/{Path(font['url']).name}"
        and Path(font["url"]).name in allowed_font_files()
        for font in body["fonts"]
    )
    font = client.get(body["fonts"][0]["url"])
    assert font.status_code == 200 and len(font.content) > 1000


def test_overlay_preview_matches_the_ass_the_renderer_would_burn(tmp_path) -> None:
    from cortex.render.service import build_plan_overlay
    config, project, plan_artifact = _api_fixture(tmp_path)
    client = TestClient(create_app(config))
    payload = _settings_json(style="pop", emphasis_word_indices=[1, 4])
    body = client.post(
        f"/api/v1/projects/{project.id}/overlay-preview",
        json={"edit_plan_artifact_id": plan_artifact.id, "settings": payload},
    ).json()
    plan = EditPlanDocument.model_validate_json(Path(plan_artifact.path).read_text())
    transcript = TranscriptDocument.model_validate_json((tmp_path / "transcript.json").read_text())
    settings = RenderSettings.model_validate(payload)
    settings = settings.model_copy(update={"headline": settings.headline.model_copy(
        update={"duration_seconds": min(2.0, plan.timeline_duration_seconds)})})
    overlay = build_plan_overlay(
        plan=plan, transcript=transcript, settings=settings, fonts_dir=FONTS_DIR,
        headline_text=None, needs_cues=True, build_ass=True,
    )
    assert overlay.ass is not None
    assert overlay.ass.ass_text == body["ass_text"]
    assert overlay.ass.content_hash == body["content_hash"]


def test_overlay_preview_is_fast_for_a_two_minute_clip(tmp_path) -> None:
    config, project, plan_artifact = _api_fixture(tmp_path, word_count=300, step=0.4)
    client = TestClient(create_app(config))
    request = {"edit_plan_artifact_id": plan_artifact.id, "settings": _settings_json(style="box")}
    url = f"/api/v1/projects/{project.id}/overlay-preview"
    assert client.post(url, json=request).status_code == 200
    started = time.perf_counter()
    response = client.post(url, json=request)
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert elapsed < 0.3, f"overlay preview took {elapsed:.3f}s"


def test_overlay_preview_errors_are_portuguese(tmp_path) -> None:
    config, project, plan_artifact = _api_fixture(tmp_path)
    client = TestClient(create_app(config))
    url = f"/api/v1/projects/{project.id}/overlay-preview"
    missing_project = client.post(
        "/api/v1/projects/nope/overlay-preview",
        json={"edit_plan_artifact_id": plan_artifact.id, "settings": _settings_json()},
    )
    assert missing_project.status_code == 404 and "Projeto" in missing_project.json()["detail"]
    missing_plan = client.post(
        url, json={"edit_plan_artifact_id": "nope", "settings": _settings_json()},
    )
    assert missing_plan.status_code == 404 and "Plano" in missing_plan.json()["detail"]
    bad = _settings_json()
    bad["captions"]["style"] = "neon"
    invalid = client.post(url, json={"edit_plan_artifact_id": plan_artifact.id, "settings": bad})
    assert invalid.status_code == 422
    assert invalid.json()["detail"].startswith("Configurações de render inválidas")


def test_overlay_font_route_serves_only_bundled_whitelisted_files(tmp_path) -> None:
    client = TestClient(create_app(_api_config(tmp_path)))
    ok = client.get("/api/v1/overlay-fonts/Montserrat-Black.otf")
    assert ok.status_code == 200
    # present in assets/fonts but not used by the overlay generator
    assert (FONTS_DIR / "Montserrat-Bold.otf").is_file()
    for name in ("Montserrat-Bold.otf", "OFL.txt", "..%2F..%2Fpyproject.toml", "nope.otf"):
        assert client.get(f"/api/v1/overlay-fonts/{name}").status_code == 404


# ---------------------------------------------------------------------------
# Real FFmpeg frames
# ---------------------------------------------------------------------------

requires_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg required",
)


def _burn_frame(ass_text: str, tmp_path: Path, name: str, at: float, background="#808080") -> Image.Image:
    ass = tmp_path / f"{name}.ass"
    ass.write_text(ass_text, encoding="utf-8")
    png = tmp_path / f"{name}.png"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", f"color=c={background}:s=1080x1920:r=30:d=4",
        "-vf", f"subtitles=filename={ass}:fontsdir={FONTS_DIR}",
        "-ss", str(at), "-frames:v", "1", str(png),
    ], check=True, timeout=60)
    return Image.open(png).convert("RGB")


def _color_bbox(image: Image.Image, color: tuple[int, int, int], tol=14):
    pixels = image.load()
    width, height = image.size
    hits = [
        (x, y) for y in range(height) for x in range(width)
        if all(abs(pixels[x, y][i] - color[i]) <= tol for i in range(3))
    ]
    if not hits:
        return None
    xs, ys = zip(*hits)
    return min(xs), min(ys), max(xs), max(ys)


@requires_ffmpeg
@pytest.mark.parametrize("emphasized", [False, True])
def test_box_style_rectangle_aligns_with_the_burned_word(tmp_path, emphasized) -> None:
    captions = _caption(style="box", emphasis_word_indices=[1] if emphasized else [])
    settings = _settings(captions)
    result = build_ass_overlay(
        settings, [_cue(TEXTS)], fonts_dir=FONTS_DIR, headline_text=None,
    )
    image = _burn_frame(result.ass_text, tmp_path, "box", at=0.6)
    color = (0xFF, 0xD4, 0x00) if emphasized else (0x2C, 0xE4, 0xD7)
    box = _color_bbox(image, color)
    assert box is not None
    page = result.pages[0]
    slot = _layout_page(
        page, captions, result.caption_font, 540, 1920 * 0.62, frozenset(captions.emphasis_word_indices),
    )[1]
    pixels = image.load()
    dark = [
        (x, y) for y in range(box[1], box[3] + 1) for x in range(box[0], box[2] + 1)
        if max(pixels[x, y]) < 60
    ]
    xs, ys = zip(*dark)
    # the dark (box_text_color) glyphs match the Pillow-measured word extents
    assert abs(min(xs) - slot.x0) <= 6 and abs(max(xs) - slot.x1) <= 6
    # and sit centred inside the rectangle on both axes
    assert abs((min(xs) - box[0]) - (box[2] - max(xs))) <= 8
    assert abs((min(ys) - box[1]) - (box[3] - max(ys))) <= 6
    assert abs(max(ys) - slot.baseline) <= 3


@requires_ffmpeg
def test_flash_frame_is_brighter_at_the_junction_than_after_it(tmp_path) -> None:
    settings = _settings(
        _caption(enabled=False),
        transitions=RenderTransitionStyleSettings(piece_junction="flash", flash_opacity=0.8),
    )
    result = build_ass_overlay(
        settings, [], fonts_dir=FONTS_DIR, headline_text=None, junctions=(1.0,),
    )
    during = _burn_frame(result.ass_text, tmp_path, "flash_on", at=1.01, background="#202020")
    after = _burn_frame(result.ass_text, tmp_path, "flash_off", at=1.5, background="#202020")
    assert during.getpixel((540, 960))[0] > 140
    assert after.getpixel((540, 960))[0] < 50
