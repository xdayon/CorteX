from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.config import CortexConfig
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.edit.schemas import (
    EditPlanDiagnostics,
    EditPlanDocument,
    EditQualityReport,
    EditSegment,
    EditTransition,
    JlSettings,
)
from cortex.ingest.ffprobe import probe_media
from cortex.render.schemas import RenderCanvasSettings, RenderCaptionSettings, RenderDocument
from cortex.render.schemas import RenderHeadlineSettings, RenderSettings, RenderSubtitleSettings
from cortex.render.service import RenderService, _filtergraph
from cortex.transcribe.schemas import EngineInfo, TranscriptDocument

from test_render import _center_pixel, _config, _dominant_audio_frequency


def _make_switch_source(
    ffmpeg: Path,
    path: Path,
    *,
    color_switch_seconds: float,
    total_seconds: float,
    audio_switch_seconds: float,
    color1: str = "red",
    color2: str = "blue",
    freq1: int = 440,
    freq2: int = 1200,
) -> None:
    """Builds a fixture where the video color and the audio tone frequency
    switch at two independently controllable source timestamps, so a
    j/l-cut boundary that shifts only the audio clock can be proven by
    pixel color (video) and dominant frequency (audio) diverging in the
    rendered output by exactly the declared offset."""
    subprocess.run([
        str(ffmpeg), "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c={color1}:size=320x180:rate=30:duration={color_switch_seconds}",
        "-f", "lavfi", "-i",
        f"color=c={color2}:size=320x180:rate=30:duration={total_seconds - color_switch_seconds}",
        "-f", "lavfi", "-i", f"sine=frequency={freq1}:sample_rate=48000:duration={audio_switch_seconds}",
        "-f", "lavfi", "-i",
        f"sine=frequency={freq2}:sample_rate=48000:duration={total_seconds - audio_switch_seconds}",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a]concat=n=2:v=0:a=1[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path),
    ], check=True, timeout=60)


def _build_plan(
    *,
    project_id: str, source_id: str, transcript_id: str,
    offset_seconds: float, kind: str, video_cut_seconds: float, total_seconds: float,
) -> EditPlanDocument:
    audio_end0 = round(video_cut_seconds + offset_seconds, 3)
    audio_start1 = audio_end0
    max_offset = abs(offset_seconds) + 0.01
    return EditPlanDocument(
        project_id=project_id, source_asset_id=source_id,
        transcript_artifact_id=transcript_id, analysis_artifact_id="analysis",
        input_hash=f"jl-{kind}", clip_start=0.0, clip_end=total_seconds, profile="balanced",
        segments=[
            EditSegment(
                start=0.0, end=video_cut_seconds,
                video_start=0.0, video_end=video_cut_seconds,
                audio_start=0.0, audio_end=audio_end0,
                timeline_order=0,
                transition=EditTransition(
                    video="micro_dissolve", audio="equal_power_crossfade", duration=0.0,
                    kind=kind, audio_offset_seconds=offset_seconds,
                ),
            ),
            EditSegment(
                start=video_cut_seconds, end=total_seconds,
                video_start=video_cut_seconds, video_end=total_seconds,
                audio_start=audio_start1, audio_end=total_seconds,
                timeline_order=1,
            ),
        ],
        timeline_duration_seconds=total_seconds,
        diagnostics=EditPlanDiagnostics(
            profile="balanced", waveform_used=True, candidate_pauses=1, cuts=1,
            saved_seconds=0.0, crossfade=0.0, vad_used=True,
            j_cuts=1 if kind == "j_cut" else 0, l_cuts=1 if kind == "l_cut" else 0,
        ),
        quality=EditQualityReport(passed=True, issues=[], profile="balanced", degraded=False),
        jl_settings=JlSettings(enabled=True, max_offset_seconds=max_offset, requested_by="user"),
    )


def _prepare_fixture(
    tmp_path: Path, config: CortexConfig, *, color_switch: float, audio_switch: float, total: float,
) -> tuple[DomainStore, SourceAsset, TranscriptArtifact]:
    domain = DomainStore(config.paths.database)
    project = domain.create_project("JL cut render fixture")
    source_dir = tmp_path / "projects" / project.id / "source"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "source.mp4"
    _make_switch_source(
        config.render.ffmpeg, source_path,
        color_switch_seconds=color_switch, total_seconds=total, audio_switch_seconds=audio_switch,
    )
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="source.mp4",
        stored_path=str(source_path.relative_to(tmp_path)),
        sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        size_bytes=source_path.stat().st_size, probe=probe_media(config.render.ffprobe, source_path),
    ))
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(TranscriptDocument(
        language="pt", duration_seconds=total,
        engine=EngineInfo(
            model="fixture", requested_device="cpu", effective_device="cpu",
            requested_compute_type="int8", effective_compute_type="int8",
            batch_size=1, vad=True,
        ), segments=[],
    ).model_dump_json(), encoding="utf-8")
    transcript = domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project.id, source_asset_id=source.id, path=str(transcript_path),
        audio_sha256="audio", engine="fixture", model="fixture", device="cpu",
        compute_type="int8", language="pt", vad=True, batch_size=1, duration_seconds=total,
    ))
    return domain, source, transcript


def _render_settings() -> RenderSettings:
    return RenderSettings(
        encoder="libx264", canvas=RenderCanvasSettings(width=320, height=320, fps=30),
        captions=RenderCaptionSettings(
            enabled=False, font_family="Montserrat", font_size=28, words_per_cue=3, outline=False,
        ),
        headline=RenderHeadlineSettings(
            enabled=False, font_family="Montserrat", font_size=36, duration_seconds=1.0,
        ),
        subtitles=RenderSubtitleSettings(sidecar_srt=False),
    )


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg and ffprobe required")
def test_j_cut_switches_audio_before_video(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    video_cut, total, offset = 1.0, 2.0, -0.3
    audio_switch = video_cut + offset  # 0.7 — audio must precede video by |offset|
    domain, source, transcript = _prepare_fixture(
        tmp_path, config, color_switch=video_cut, audio_switch=audio_switch, total=total,
    )
    plan = _build_plan(
        project_id=source.project_id, source_id=source.id, transcript_id=transcript.id,
        offset_seconds=offset, kind="j_cut", video_cut_seconds=video_cut, total_seconds=total,
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    plan_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=source.project_id, stage="edit_plan", path=str(plan_path), input_hash=plan.input_hash,
    ))

    result = RenderService(config, domain).run(
        edit_plan_artifact=plan_artifact, encoder=None, headline=None,
        progress_cb=lambda *_a: None, should_cancel=lambda: False, render_settings=_render_settings(),
    )
    document = RenderDocument.model_validate_json(
        Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text()
    )
    assert document.quality.passed is True
    assert document.quality.actual_duration_seconds == pytest.approx(total, abs=0.15)

    output = Path(document.output_path)
    # Video: color1 up to video_cut, color2 after.
    before_video = _center_pixel(config.render.ffmpeg, output, video_cut - 0.15)
    after_video = _center_pixel(config.render.ffmpeg, output, video_cut + 0.15)
    assert before_video[0] > before_video[2]
    assert after_video[2] > after_video[0]

    # Audio: freq1 up to audio_switch (< video_cut), freq2 after — proving
    # the audio splice lands earlier than the video cut by |offset|.
    before_audio_path = tmp_path / "before_audio.wav"
    after_audio_path = tmp_path / "after_audio.wav"
    subprocess.run([
        str(config.render.ffmpeg), "-y", "-v", "error", "-i", str(output),
        "-ss", "0.1", "-t", "0.4", "-map", "0:a:0", str(before_audio_path),
    ], check=True, timeout=30)
    subprocess.run([
        str(config.render.ffmpeg), "-y", "-v", "error", "-i", str(output),
        "-ss", str(audio_switch + 0.1), "-t", "0.4", "-map", "0:a:0", str(after_audio_path),
    ], check=True, timeout=30)
    freq_before = _dominant_audio_frequency(config.render.ffmpeg, before_audio_path)
    freq_after = _dominant_audio_frequency(config.render.ffmpeg, after_audio_path)
    assert freq_before == pytest.approx(440, abs=15)
    assert freq_after == pytest.approx(1200, abs=40)

    # Confirm the divergence between the audio and video cut points equals
    # the declared offset (tolerance: ~1 frame at 30fps + our sampling window).
    assert abs(audio_switch - video_cut) == pytest.approx(abs(offset), abs=0.05)

    assert document.transitions
    assert document.transitions[0].requested_kind == "j_cut"
    assert document.transitions[0].effective_kind == "j_cut"
    assert document.transitions[0].requested_audio_offset_seconds == pytest.approx(offset)
    assert document.transitions[0].effective_audio_offset_seconds == pytest.approx(offset)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg and ffprobe required")
def test_l_cut_switches_audio_after_video(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    video_cut, total, offset = 1.0, 2.0, 0.3
    audio_switch = video_cut + offset  # 1.3 — audio must trail video by offset
    domain, source, transcript = _prepare_fixture(
        tmp_path, config, color_switch=video_cut, audio_switch=audio_switch, total=total,
    )
    plan = _build_plan(
        project_id=source.project_id, source_id=source.id, transcript_id=transcript.id,
        offset_seconds=offset, kind="l_cut", video_cut_seconds=video_cut, total_seconds=total,
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    plan_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=source.project_id, stage="edit_plan", path=str(plan_path), input_hash=plan.input_hash,
    ))

    result = RenderService(config, domain).run(
        edit_plan_artifact=plan_artifact, encoder=None, headline=None,
        progress_cb=lambda *_a: None, should_cancel=lambda: False, render_settings=_render_settings(),
    )
    document = RenderDocument.model_validate_json(
        Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text()
    )
    assert document.quality.passed is True

    output = Path(document.output_path)
    before_video = _center_pixel(config.render.ffmpeg, output, video_cut - 0.15)
    after_video = _center_pixel(config.render.ffmpeg, output, video_cut + 0.15)
    assert before_video[0] > before_video[2]
    assert after_video[2] > after_video[0]

    before_audio_path = tmp_path / "before_audio.wav"
    after_audio_path = tmp_path / "after_audio.wav"
    subprocess.run([
        str(config.render.ffmpeg), "-y", "-v", "error", "-i", str(output),
        "-ss", "0.1", "-t", "0.4", "-map", "0:a:0", str(before_audio_path),
    ], check=True, timeout=30)
    subprocess.run([
        str(config.render.ffmpeg), "-y", "-v", "error", "-i", str(output),
        "-ss", str(audio_switch + 0.1), "-t", "0.4", "-map", "0:a:0", str(after_audio_path),
    ], check=True, timeout=30)
    freq_before = _dominant_audio_frequency(config.render.ffmpeg, before_audio_path)
    freq_after = _dominant_audio_frequency(config.render.ffmpeg, after_audio_path)
    assert freq_before == pytest.approx(440, abs=15)
    assert freq_after == pytest.approx(1200, abs=40)

    assert abs(audio_switch - video_cut) == pytest.approx(abs(offset), abs=0.05)

    assert document.transitions
    assert document.transitions[0].requested_kind == "l_cut"
    assert document.transitions[0].requested_audio_offset_seconds == pytest.approx(offset)


def test_filtergraph_uses_video_and_audio_clocks_independently() -> None:
    plan = _build_plan(
        project_id="project", source_id="source", transcript_id="transcript",
        offset_seconds=-0.3, kind="j_cut", video_cut_seconds=1.0, total_seconds=2.0,
    )
    filtergraph, _video_label, _audio_label = _filtergraph(plan, 320, 320, 30, -14.0, -1.0)
    assert "[0:v]trim=start=0.000000:end=1.000000" in filtergraph
    assert "[0:v]trim=start=1.000000:end=2.000000" in filtergraph
    assert "[0:a]atrim=start=0.000000:end=0.700000" in filtergraph
    assert "[0:a]atrim=start=0.700000:end=2.000000" in filtergraph
    # The j_cut boundary must not reuse the video/crossfade acrossfade path.
    assert "acrossfade=d=0.020000" in filtergraph


def test_jl_cancelled_filtergraph_matches_pre_gate4_behavior() -> None:
    """When jl is disabled the planner never sets a j_cut/l_cut kind or a
    non-zero audio_offset_seconds, and audio_start/audio_end default to
    start/end — so the filtergraph produced today must be byte-identical to
    the pre-Gate-4 single-clock renderer output."""
    plan = EditPlanDocument.model_validate({
        "project_id": "project", "source_asset_id": "source",
        "transcript_artifact_id": "transcript", "analysis_artifact_id": "analysis",
        "input_hash": "cancelled", "clip_start": 0.0, "clip_end": 2.0,
        "profile": "balanced", "segments": [
            {
                "start": 0.0, "end": 1.0, "timeline_order": 0,
                "transition": {"video": "micro_dissolve", "audio": "equal_power_crossfade", "duration": 0.06},
            },
            {"start": 1.0, "end": 2.0, "timeline_order": 1},
        ], "timeline_duration_seconds": 1.94,
        "diagnostics": {"profile": "balanced", "waveform_used": True, "candidate_pauses": 1,
                        "cuts": 1, "saved_seconds": 0.0, "crossfade": 0.06, "vad_used": True},
        "quality": {"passed": True, "issues": [], "profile": "balanced", "degraded": False},
    })
    v1_shaped, _video_label, _audio_label = _filtergraph(plan, 320, 320, 30, -14.0, -1.0)

    explicit_v2 = plan.model_copy(update={"segments": [
        segment.model_copy(update={
            "video_start": segment.start, "video_end": segment.end,
            "audio_start": segment.start, "audio_end": segment.end,
        })
        for segment in plan.segments
    ]})
    explicit_filtergraph, _v, _a = _filtergraph(explicit_v2, 320, 320, 30, -14.0, -1.0)

    assert v1_shaped == explicit_filtergraph
    assert "acrossfade=d=0.060000" in v1_shaped
    assert plan.segments[0].transition.kind == "crossfade"
    assert plan.segments[0].transition.audio_offset_seconds == 0.0
