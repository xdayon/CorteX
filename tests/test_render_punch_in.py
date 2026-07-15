from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from cortex.domain.models import SourceAsset, SourceKind, StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.edit.camera_plan_schemas import CameraEditPlanDocument
from cortex.edit.schemas import (
    EditPlanDiagnostics,
    EditPlanDocument,
    EditQualityReport,
    EditSegment,
)
from cortex.ingest.ffprobe import probe_media
from cortex.render.schemas import (
    RenderCanvasSettings,
    RenderCaptionSettings,
    RenderDocument,
    RenderFramingSettings,
    RenderHeadlineSettings,
    RenderPunchInSettings,
    RenderSettings,
    RenderSubtitleSettings,
)
from cortex.render.service import (
    RenderPreconditionError,
    RenderService,
    _filtergraph,
    _PunchInResolution,
    _resolve_punch_ins,
    _resolve_segment_punch_in,
)
from cortex.transcribe.schemas import EngineInfo, TranscriptDocument

from test_render import _config


# ---------------------------------------------------------------------------
# Schema-level validation: anchor="face" gating.
# ---------------------------------------------------------------------------


def test_punch_in_anchor_face_requires_face_static_crop_mode() -> None:
    with pytest.raises(ValidationError, match="punch_in.anchor 'face'"):
        RenderFramingSettings(
            mode="vertical_crop",
            punch_in=RenderPunchInSettings(enabled=True, anchor="face"),
        )
    # Same anchor is fine once the mode matches.
    settings = RenderFramingSettings(
        mode="face_static_crop", punch_in=RenderPunchInSettings(enabled=True, anchor="face"),
    )
    assert settings.punch_in.anchor == "face"


def test_punch_in_scale_bounds_enforced_by_schema() -> None:
    with pytest.raises(ValidationError):
        RenderPunchInSettings(scale=1.6)
    with pytest.raises(ValidationError):
        RenderPunchInSettings(scale=0.9)


# ---------------------------------------------------------------------------
# _filtergraph: byte-identical when disabled, constant crops when applied.
# ---------------------------------------------------------------------------


def _one_segment_plan() -> EditPlanDocument:
    return EditPlanDocument(
        project_id="p", source_asset_id="s", transcript_artifact_id="t",
        analysis_artifact_id="a", input_hash="h", clip_start=0.0, clip_end=1.0,
        profile="balanced", segments=[EditSegment(start=0.0, end=1.0, timeline_order=0)],
        timeline_duration_seconds=1.0,
        diagnostics=EditPlanDiagnostics(profile="balanced", waveform_used=True, candidate_pauses=0,
                                        cuts=0, saved_seconds=0.0, crossfade=0.0, vad_used=True),
        quality=EditQualityReport(passed=True, issues=[], profile="balanced", degraded=False),
    )


def test_filtergraph_punch_in_disabled_is_byte_identical_to_baseline() -> None:
    plan = _one_segment_plan()
    baseline, _v, _a = _filtergraph(plan, 320, 320, 30, -14.0, -1.0)
    disabled_resolution = _PunchInResolution(0, False, 1.15, 1.0, 0, 0, 320, 320, "punch_in_disabled")
    with_disabled_punch_ins, _v2, _a2 = _filtergraph(
        plan, 320, 320, 30, -14.0, -1.0, punch_ins={0: disabled_resolution},
    )
    assert baseline == with_disabled_punch_ins


def test_filtergraph_punch_in_vertical_crop_uses_constant_crop_and_no_temporal_expressions() -> None:
    plan = _one_segment_plan()
    resolution = _resolve_segment_punch_in(
        0, RenderPunchInSettings(enabled=True, scale=1.2),
        framing_mode="vertical_crop", canvas_width=320, canvas_height=320,
        static_face_crop_map=None,
    )
    assert resolution.applied is True
    assert resolution.crop_width == 267 and resolution.crop_height == 267
    assert resolution.anchor_x == 26 and resolution.anchor_y == 26

    filtergraph, _video_label, _audio_label = _filtergraph(
        plan, 320, 320, 30, -14.0, -1.0, punch_ins={0: resolution},
    )
    assert (
        f"crop={resolution.crop_width}:{resolution.crop_height}:"
        f"{resolution.anchor_x}:{resolution.anchor_y}"
    ) in filtergraph
    assert "zoompan" not in filtergraph
    assert "t*" not in filtergraph
    assert "between(t" not in filtergraph  # no headline in this graph either


def test_filtergraph_blurred_background_zooms_fitted_foreground_without_invalid_crop() -> None:
    plan = _one_segment_plan()
    resolution = _resolve_segment_punch_in(
        0, RenderPunchInSettings(enabled=True, scale=1.15, alternate_on_jump_cuts=False),
        framing_mode="blurred_background", canvas_width=1080, canvas_height=1920,
        static_face_crop_map=None,
    )

    filtergraph, _video_label, _audio_label = _filtergraph(
        plan, 1080, 1920, 30, -14.0, -1.0,
        framing_mode="blurred_background", punch_ins={0: resolution},
    )

    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in filtergraph
    assert "scale=trunc(iw*1.15/2)*2:trunc(ih*1.15/2)*2" in filtergraph
    assert "crop=1080:1920" not in filtergraph


def test_filtergraph_trims_loudnorm_tail_to_video_duration() -> None:
    filtergraph, _video_label, audio_label = _filtergraph(
        _one_segment_plan(), 320, 568, 30, -14.0, -1.0,
    )

    assert "loudnorm=I=-14.00:TP=-1.50:LRA=11:print_format=none" in filtergraph
    assert "atrim=duration=1.000000,asetpts=PTS-STARTPTS[anorm]" in filtergraph
    assert audio_label == "[anorm]"


def test_filtergraph_punch_in_face_static_crop_tightens_the_resolved_crop() -> None:
    plan = _one_segment_plan()
    static_face_crop_map = {0: {"crop_x": 10, "crop_y": 5, "crop_width": 200, "crop_height": 300}}
    resolution = _resolve_segment_punch_in(
        0, RenderPunchInSettings(enabled=True, scale=1.5, anchor="face"),
        framing_mode="face_static_crop", canvas_width=320, canvas_height=568,
        static_face_crop_map=static_face_crop_map,
    )
    assert resolution.applied is True
    # Shrunk, centered within the original resolved crop rectangle.
    assert resolution.crop_width == round(200 / 1.5)
    assert resolution.crop_height == round(300 / 1.5)
    assert resolution.anchor_x >= 10 and resolution.anchor_x + resolution.crop_width <= 210
    assert resolution.anchor_y >= 5 and resolution.anchor_y + resolution.crop_height <= 305

    filtergraph, _v, _a = _filtergraph(
        plan, 320, 568, 30, -14.0, -1.0, framing_mode="face_static_crop",
        static_face_crops=static_face_crop_map, punch_ins={0: resolution},
    )
    assert (
        f"crop={resolution.crop_width}:{resolution.crop_height}:"
        f"{resolution.anchor_x}:{resolution.anchor_y}"
    ) in filtergraph
    # The original, un-tightened crop must not remain in the graph.
    assert "crop=200:300:10:5" not in filtergraph


# ---------------------------------------------------------------------------
# Fail-closed geometry / resolution floor.
# ---------------------------------------------------------------------------


def test_resolve_segment_punch_in_rejects_crop_below_safe_resolution_floor() -> None:
    static_face_crop_map = {0: {"crop_x": 0, "crop_y": 0, "crop_width": 56, "crop_height": 100}}
    with pytest.raises(RenderPreconditionError, match="resolução efetiva"):
        _resolve_segment_punch_in(
            0, RenderPunchInSettings(enabled=True, scale=1.5, anchor="center"),
            framing_mode="face_static_crop", canvas_width=320, canvas_height=568,
            static_face_crop_map=static_face_crop_map,
        )


def test_resolve_punch_ins_raises_before_any_encode_when_face_crop_missing() -> None:
    plan = _one_segment_plan()
    with pytest.raises(RenderPreconditionError, match="crop facial estático ausente"):
        _resolve_punch_ins(
            plan, None, RenderPunchInSettings(enabled=True, scale=1.2),
            framing_mode="face_static_crop", canvas_width=320, canvas_height=568,
            static_face_crop_map=None,
        )


# ---------------------------------------------------------------------------
# Deterministic alternation.
# ---------------------------------------------------------------------------


def _two_segment_plan() -> EditPlanDocument:
    return EditPlanDocument(
        project_id="p", source_asset_id="s", transcript_artifact_id="t",
        analysis_artifact_id="a", input_hash="h", clip_start=0.0, clip_end=2.0,
        profile="balanced", segments=[
            EditSegment(start=0.0, end=1.0, timeline_order=0),
            EditSegment(start=1.0, end=2.0, timeline_order=1),
        ],
        timeline_duration_seconds=2.0,
        diagnostics=EditPlanDiagnostics(profile="balanced", waveform_used=True, candidate_pauses=0,
                                        cuts=0, saved_seconds=0.0, crossfade=0.0, vad_used=True),
        quality=EditQualityReport(passed=True, issues=[], profile="balanced", degraded=False),
    )


def test_alternation_without_camera_plan_uses_global_timeline_order_parity() -> None:
    plan = _two_segment_plan()
    punch_in = RenderPunchInSettings(enabled=True, scale=1.2, alternate_on_jump_cuts=True)
    run_one = _resolve_punch_ins(
        plan, None, punch_in, framing_mode="vertical_crop",
        canvas_width=320, canvas_height=320, static_face_crop_map=None,
    )
    run_two = _resolve_punch_ins(
        plan, None, punch_in, framing_mode="vertical_crop",
        canvas_width=320, canvas_height=320, static_face_crop_map=None,
    )
    assert run_one == run_two  # deterministic across repeated calls
    assert run_one[0].applied is True and run_one[0].reason is None
    assert run_one[1].applied is False and run_one[1].reason == "alternation_skipped"


def test_alternation_with_camera_plan_uses_scene_index_parity() -> None:
    plan = _two_segment_plan()
    camera_plan = CameraEditPlanDocument.model_validate({
        "project_id": "p", "source_asset_id": "s", "edit_plan_artifact_id": "edit",
        "edit_plan_input_hash": "h", "camera_timeline_artifact_id": "camera",
        "camera_timeline_input_hash": "camera-hash", "identity_index_artifact_id": "identity",
        "identity_index_input_hash": "identity-hash", "visual_quality_artifact_id": "quality",
        "visual_quality_input_hash": "quality-hash", "input_hash": "camera-plan",
        "shots": [
            {"edit_segment_order": 0, "video_source_asset_id": "s", "audio_source_asset_id": "s",
             "source_start_us": 0, "source_end_us": 1_000_000, "audio_source_start_us": 0,
             "audio_source_end_us": 1_000_000, "scene_index": 0, "layout_id": "primary",
             "camera_role": "speaker_close", "intent": "speaker", "confirmed_identity_ids": [],
             "visual_quality_usable": True, "evidence": []},
            {"edit_segment_order": 1, "video_source_asset_id": "s", "audio_source_asset_id": "s",
             "source_start_us": 1_000_000, "source_end_us": 2_000_000, "audio_source_start_us": 1_000_000,
             "audio_source_end_us": 2_000_000, "scene_index": 0, "layout_id": "primary",
             "camera_role": "speaker_close", "intent": "speaker", "confirmed_identity_ids": [],
             "visual_quality_usable": True, "evidence": []},
        ],
        "diagnostics": {"shot_count": 2, "speaker_shot_count": 2, "context_shot_count": 0,
                        "fallback_shot_count": 0, "unusable_scene_count": 0,
                        "reaction_shots_blocked_by": []},
        "engine": {"algorithm": "fixture", "algorithm_version": "2"},
    })
    punch_in = RenderPunchInSettings(enabled=True, scale=1.2, alternate_on_jump_cuts=True)
    resolutions = _resolve_punch_ins(
        plan, camera_plan, punch_in, framing_mode="vertical_crop",
        canvas_width=320, canvas_height=320, static_face_crop_map=None,
    )
    # Both segments share scene_index=0 -> alternate within that one scene.
    assert resolutions[0].applied is True
    assert resolutions[1].applied is False


def test_alternate_on_jump_cuts_false_applies_to_every_segment() -> None:
    plan = _two_segment_plan()
    punch_in = RenderPunchInSettings(enabled=True, scale=1.2, alternate_on_jump_cuts=False)
    resolutions = _resolve_punch_ins(
        plan, None, punch_in, framing_mode="vertical_crop",
        canvas_width=320, canvas_height=320, static_face_crop_map=None,
    )
    assert all(resolution.applied for resolution in resolutions)


# ---------------------------------------------------------------------------
# Real synthetic render: dimensions, pixel proof of zoom, temporal stability,
# alternation across two rendered segments, and manifest evidence.
# ---------------------------------------------------------------------------


def _stripe_source(config, domain: DomainStore, tmp_path: Path, project_id: str, *, duration: float = 2.0) -> SourceAsset:
    """320x320, solid blue with a 16px-wide (5% of width) solid red stripe
    pinned to the left edge, static for the whole duration. A 1.2x punch-in
    on a 320-wide canvas crops in to x=[26, 293) — well past the stripe — so
    the stripe is provably cropped out entirely once punch-in applies,
    without needing any face/scene detection."""
    source_dir = tmp_path / "projects" / project_id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / "stripe.mp4"
    subprocess.run([
        str(config.render.ffmpeg), "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=blue:size=320x320:rate=30:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
        "-filter_complex", "[0:v]drawbox=x=0:y=0:w=16:h=320:color=red:t=fill[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path),
    ], check=True, timeout=60)
    return domain.create_source_asset(SourceAsset(
        project_id=project_id, kind=SourceKind.UPLOAD, original_filename="stripe.mp4",
        stored_path=str(path.relative_to(tmp_path)),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size_bytes=path.stat().st_size, probe=probe_media(config.render.ffprobe, path),
    ))


def _pixel_at(ffmpeg: Path, media_path: Path, timestamp: float, x: int, y: int) -> tuple[int, int, int]:
    result = subprocess.run(
        [
            str(ffmpeg), "-v", "error", "-ss", str(timestamp), "-i", str(media_path),
            "-frames:v", "1", "-vf", f"crop=1:1:{x}:{y},format=rgb24",
            "-f", "rawvideo", "-",
        ],
        capture_output=True, check=True, timeout=30,
    )
    return tuple(result.stdout[:3])  # type: ignore[return-value]


def _make_transcript(domain: DomainStore, tmp_path: Path, project_id: str, source_id: str, duration: float) -> TranscriptArtifact:
    transcript_path = tmp_path / f"transcript-{source_id}.json"
    transcript_path.write_text(TranscriptDocument(
        language="pt", duration_seconds=duration,
        engine=EngineInfo(
            model="fixture", requested_device="cpu", effective_device="cpu",
            requested_compute_type="int8", effective_compute_type="int8",
            batch_size=1, vad=True,
        ), segments=[],
    ).model_dump_json(), encoding="utf-8")
    return domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project_id, source_asset_id=source_id, path=str(transcript_path),
        audio_sha256="audio", engine="fixture", model="fixture", device="cpu",
        compute_type="int8", language="pt", vad=True, batch_size=1, duration_seconds=duration,
    ))


def _make_plan_artifact(domain: DomainStore, tmp_path: Path, project_id: str, source_id: str,
                        transcript_id: str, segments: list[EditSegment], duration: float) -> StageArtifact:
    plan = EditPlanDocument(
        project_id=project_id, source_asset_id=source_id, transcript_artifact_id=transcript_id,
        analysis_artifact_id="analysis", input_hash=f"edit-{duration}-{len(segments)}",
        clip_start=0.0, clip_end=duration, profile="balanced", segments=segments,
        timeline_duration_seconds=duration,
        diagnostics=EditPlanDiagnostics(profile="balanced", waveform_used=True, candidate_pauses=0,
                                        cuts=0, saved_seconds=0.0, crossfade=0.0, vad_used=True),
        quality=EditQualityReport(passed=True, issues=[], profile="balanced", degraded=False),
    )
    plan_path = tmp_path / f"plan-{plan.input_hash}.json"
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    return domain.create_stage_artifact(StageArtifact(
        project_id=project_id, stage="edit_plan", path=str(plan_path), input_hash=plan.input_hash,
    ))


def _settings(*, scale: float, alternate: bool) -> RenderSettings:
    return RenderSettings(
        encoder="libx264", canvas=RenderCanvasSettings(width=320, height=320, fps=30),
        framing=RenderFramingSettings(
            mode="vertical_crop",
            punch_in=RenderPunchInSettings(enabled=True, scale=scale, alternate_on_jump_cuts=alternate),
        ),
        captions=RenderCaptionSettings(enabled=False, font_family="Montserrat", font_size=28,
                                       words_per_cue=3, outline=False),
        headline=RenderHeadlineSettings(enabled=False, font_family="Montserrat", font_size=36,
                                        duration_seconds=1.0),
        subtitles=RenderSubtitleSettings(sidecar_srt=False),
    )


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg and ffprobe required")
def test_punch_in_render_proves_zoom_via_pixel_and_keeps_canvas_dimensions(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Punch-in zoom fixture")
    source = _stripe_source(config, domain, tmp_path, project.id, duration=1.0)
    transcript = _make_transcript(domain, tmp_path, project.id, source.id, duration=1.0)
    plan_artifact = _make_plan_artifact(
        domain, tmp_path, project.id, source.id, transcript.id,
        segments=[EditSegment(start=0.0, end=1.0, timeline_order=0)], duration=1.0,
    )

    result = RenderService(config, domain).run(
        edit_plan_artifact=plan_artifact, encoder=None, headline=None,
        progress_cb=lambda *_a: None, should_cancel=lambda: False,
        render_settings=_settings(scale=1.2, alternate=False),
    )
    render_artifact = domain.get_stage_artifact(result["render_artifact_id"])
    document = RenderDocument.model_validate_json(Path(render_artifact.path).read_text())

    probe = probe_media(config.render.ffprobe, Path(document.output_path))
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert int(video_stream["width"]) == 320
    assert int(video_stream["height"]) == 320

    # The stripe (x in [0,16)) is cropped out entirely once punch-in is applied.
    pixel = _pixel_at(config.render.ffmpeg, Path(document.output_path), 0.5, 8, 160)
    assert pixel[2] > pixel[0], f"expected blue (cropped-out stripe) at (8,160), got {pixel}"

    # Temporal stability: the crop is resolved once per segment, never per frame.
    for timestamp in (0.15, 0.5, 0.85):
        stable_pixel = _pixel_at(config.render.ffmpeg, Path(document.output_path), timestamp, 8, 160)
        assert stable_pixel == pixel

    assert len(document.punch_ins) == 1
    entry = document.punch_ins[0]
    assert entry.applied is True
    assert entry.requested_scale == pytest.approx(1.2)
    assert entry.effective_scale == pytest.approx(1.2)
    assert entry.anchor_x == 26 and entry.anchor_y == 26
    assert document.publication is not None
    assert document.publication.punch_in["enabled"] is True
    assert document.publication.punch_in["segments"][0]["applied"] is True


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg and ffprobe required")
def test_punch_in_disabled_render_shows_the_stripe_unchanged(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Punch-in disabled fixture")
    source = _stripe_source(config, domain, tmp_path, project.id, duration=1.0)
    transcript = _make_transcript(domain, tmp_path, project.id, source.id, duration=1.0)
    plan_artifact = _make_plan_artifact(
        domain, tmp_path, project.id, source.id, transcript.id,
        segments=[EditSegment(start=0.0, end=1.0, timeline_order=0)], duration=1.0,
    )
    settings = RenderSettings(
        encoder="libx264", canvas=RenderCanvasSettings(width=320, height=320, fps=30),
        framing=RenderFramingSettings(mode="vertical_crop"),
        captions=RenderCaptionSettings(enabled=False, font_family="Montserrat", font_size=28,
                                       words_per_cue=3, outline=False),
        headline=RenderHeadlineSettings(enabled=False, font_family="Montserrat", font_size=36,
                                        duration_seconds=1.0),
        subtitles=RenderSubtitleSettings(sidecar_srt=False),
    )

    result = RenderService(config, domain).run(
        edit_plan_artifact=plan_artifact, encoder=None, headline=None,
        progress_cb=lambda *_a: None, should_cancel=lambda: False,
        render_settings=settings,
    )
    document = RenderDocument.model_validate_json(
        Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text()
    )
    pixel = _pixel_at(config.render.ffmpeg, Path(document.output_path), 0.5, 8, 160)
    assert pixel[0] > pixel[2], f"expected red (stripe visible, no punch-in) at (8,160), got {pixel}"
    assert document.punch_ins == [] or all(not entry.applied for entry in document.punch_ins)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg and ffprobe required")
def test_punch_in_alternates_across_two_consecutive_segments(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Punch-in alternation fixture")
    source = _stripe_source(config, domain, tmp_path, project.id, duration=2.0)
    transcript = _make_transcript(domain, tmp_path, project.id, source.id, duration=2.0)
    plan_artifact = _make_plan_artifact(
        domain, tmp_path, project.id, source.id, transcript.id,
        segments=[
            EditSegment(start=0.0, end=1.0, timeline_order=0),
            EditSegment(start=1.0, end=2.0, timeline_order=1),
        ],
        duration=2.0,
    )

    result = RenderService(config, domain).run(
        edit_plan_artifact=plan_artifact, encoder=None, headline=None,
        progress_cb=lambda *_a: None, should_cancel=lambda: False,
        render_settings=_settings(scale=1.2, alternate=True),
    )
    document = RenderDocument.model_validate_json(
        Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text()
    )
    assert [entry.applied for entry in sorted(document.punch_ins, key=lambda e: e.segment_order)] == [True, False]

    first_segment_pixel = _pixel_at(config.render.ffmpeg, Path(document.output_path), 0.5, 8, 160)
    second_segment_pixel = _pixel_at(config.render.ffmpeg, Path(document.output_path), 1.5, 8, 160)
    assert first_segment_pixel[2] > first_segment_pixel[0]  # blue: punch-in applied, stripe hidden
    assert second_segment_pixel[0] > second_segment_pixel[2]  # red: stripe still visible
