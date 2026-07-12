from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.edit.schemas import (
    EditPlanDiagnostics,
    EditPlanDocument,
    EditQualityReport,
    EditSegment,
    EditTransition,
)
from cortex.ingest.ffprobe import probe_media
from cortex.jobs import JobStore
from cortex.render.schemas import RenderDocument
from cortex.render.schemas import (
    RenderCanvasSettings,
    RenderAnimationSettings,
    RenderCaptionSettings,
    RenderHeadlineSettings,
    RenderHeadlineAnimationSettings,
    RenderSettings,
    RenderSubtitleSettings,
)
from cortex.render.service import (
    RenderPreconditionError,
    RenderService,
    _encoder_args,
    _resolve_font_family,
)
from cortex.schemas import JobCreate, JobStatus, JobType
from cortex.transcribe.schemas import (
    EngineInfo,
    TranscriptDocument,
    TranscriptSegment,
    TranscriptWord,
)
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine


def _config(tmp_path: Path) -> CortexConfig:
    base = load_config()
    return base.model_copy(update={
        "paths": base.paths.model_copy(update={
            "data_dir": tmp_path,
            "projects_dir": tmp_path / "projects",
            "cache_dir": tmp_path / "cache",
            "output_dir": tmp_path / "output",
            "database": tmp_path / "cortex.sqlite3",
        }),
        "render": base.render.model_copy(update={
            "ffmpeg": Path(shutil.which("ffmpeg") or "ffmpeg"),
            "ffprobe": Path(shutil.which("ffprobe") or "ffprobe"),
            "encoder": "libx264", "width": 320, "height": 320, "fps": 30,
        }),
    })


def _alpha_plane(ffmpeg: Path, overlay_path: Path, timestamp: float) -> bytes:
    result = subprocess.run(
        [
            str(ffmpeg), "-v", "error", "-c:v", "libvpx-vp9", "-i", str(overlay_path),
            "-ss", str(timestamp), "-frames:v", "1", "-vf", "alphaextract",
            "-pix_fmt", "gray", "-f", "rawvideo", "-",
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    return result.stdout


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg and ffprobe required")
def test_multi_segment_render_persists_valid_cached_artifact(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Render fixture")
    source_path = tmp_path / "projects" / project.id / "source" / "source.mp4"
    source_path.parent.mkdir(parents=True)
    subprocess.run([
        str(config.render.ffmpeg), "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
        str(source_path),
    ], check=True, timeout=60)
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="source.mp4",
        stored_path=str(source_path.relative_to(tmp_path)), sha256=source_sha,
        size_bytes=source_path.stat().st_size,
        probe=probe_media(config.render.ffprobe, source_path),
    ))
    transcript_path = tmp_path / "projects" / project.id / "transcripts" / "transcript.json"
    transcript_path.parent.mkdir(parents=True)
    transcript = TranscriptDocument(
        language="pt",
        duration_seconds=4.0,
        engine=EngineInfo(
            model="fixture", requested_device="cpu", effective_device="cpu",
            requested_compute_type="int8", effective_compute_type="int8",
            batch_size=1, vad=True,
        ),
        segments=[TranscriptSegment(
            id=0, start=0.0, end=3.8, text="um corte real com legenda persistida",
            avg_logprob=-0.1, no_speech_prob=0.01,
            words=[
                TranscriptWord(start=0.1, end=0.5, word="um", probability=0.99),
                TranscriptWord(start=0.55, end=1.0, word="corte", probability=0.99),
                TranscriptWord(start=2.3, end=2.7, word="real", probability=0.99),
                TranscriptWord(start=2.75, end=3.1, word="com", probability=0.99),
                TranscriptWord(start=3.15, end=3.7, word="legenda", probability=0.99),
            ],
        )],
    )
    transcript_path.write_text(transcript.model_dump_json(), encoding="utf-8")
    transcript_artifact = domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project.id, source_asset_id=source.id, path=str(transcript_path),
        audio_sha256="audio", engine="fixture", model="fixture", device="cpu",
        compute_type="int8", language="pt", vad=True, batch_size=1,
        duration_seconds=4.0,
    ))
    plan = EditPlanDocument(
        project_id=project.id, source_asset_id=source.id,
        transcript_artifact_id=transcript_artifact.id, analysis_artifact_id="analysis",
        input_hash="edit-plan-input", clip_start=0.0, clip_end=3.8,
        profile="balanced",
        segments=[
            EditSegment(
                start=0.0, end=1.4, timeline_order=0,
                transition=EditTransition(
                    video="micro_dissolve", audio="equal_power_crossfade", duration=0.06,
                ),
            ),
            EditSegment(start=2.2, end=3.8, timeline_order=1),
        ],
        timeline_duration_seconds=2.94,
        diagnostics=EditPlanDiagnostics(
            profile="balanced", waveform_used=True, candidate_pauses=1, cuts=1,
            saved_seconds=0.8, crossfade=0.06, vad_used=True,
        ),
        quality=EditQualityReport(passed=True, issues=[], profile="balanced", degraded=False),
    )
    plan_path = tmp_path / "projects" / project.id / "edit_plans" / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    plan_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="edit_plan", path=str(plan_path),
        input_hash=plan.input_hash,
    ))

    service = RenderService(config, domain)
    render_settings = RenderSettings(
        encoder="libx264",
        canvas=RenderCanvasSettings(width=320, height=320, fps=30),
        captions=RenderCaptionSettings(
            enabled=True, font_family="Montserrat", font_size=28,
            words_per_cue=3, outline=False,
        ),
        headline=RenderHeadlineSettings(
            enabled=True, text="Corte real", font_family="Montserrat",
            font_size=36, duration_seconds=2.5,
        ),
        subtitles=RenderSubtitleSettings(sidecar_srt=True),
    )
    first = service.run(
        edit_plan_artifact=plan_artifact, encoder=None, headline=None,
        progress_cb=lambda _progress, _message: None, should_cancel=lambda: False,
        render_settings=render_settings,
    )
    assert first["cached"] is False
    artifact = domain.get_stage_artifact(first["render_artifact_id"])
    document = RenderDocument.model_validate_json(Path(artifact.path).read_text())
    assert document.quality.passed is True
    assert document.quality.has_video is True and document.quality.has_audio is True
    assert document.quality.actual_duration_seconds == pytest.approx(2.94, abs=0.15)
    assert document.quality.loudness_target_lufs == -14.0
    assert document.quality.integrated_loudness_lufs == pytest.approx(-14.0, abs=1.0)
    assert document.quality.loudness_delta_lu is not None
    assert document.quality.loudness_delta_lu <= 1.0
    assert document.quality.true_peak_dbfs is not None
    assert document.quality.true_peak_dbfs <= -1.0
    assert document.engine.requested_encoder == document.engine.effective_encoder == "libx264"
    assert Path(document.output_path).stat().st_size == document.output_size_bytes
    assert document.quality.caption_cue_count == 2
    assert document.quality.subtitles_present is True
    assert document.quality.headline_present is True
    assert document.overlays is not None
    assert document.overlays.renderer == "remotion"
    assert document.requested_settings == render_settings
    assert document.effective_settings == render_settings
    assert document.overlays.caption_font_size == 28
    assert document.overlays.caption_outline is False
    assert document.overlays.caption_shadow is True
    assert document.overlays.karaoke_enabled is True
    assert document.overlays.artifact_path is not None
    assert Path(document.overlays.artifact_path).is_file()
    assert document.overlays.artifact_sha256 == hashlib.sha256(
        Path(document.overlays.artifact_path).read_bytes()
    ).hexdigest()
    assert document.overlays.codec == "vp9"
    assert document.overlays.pixel_format == "yuva420p"
    assert document.overlays.node_version
    assert document.overlays.remotion_version == "4.0.489"
    overlay_path = Path(document.overlays.artifact_path)
    early_alpha = _alpha_plane(config.render.ffmpeg, overlay_path, 0.2)
    next_word_alpha = _alpha_plane(config.render.ffmpeg, overlay_path, 0.6)
    blank_alpha = _alpha_plane(config.render.ffmpeg, overlay_path, 2.9)
    assert max(early_alpha) > 0
    assert early_alpha != next_word_alpha
    assert max(blank_alpha, default=0) == 0
    assert document.subtitles_path is not None
    assert Path(document.subtitles_path).read_text(encoding="utf-8").count(" --> ") == 2
    assert document.subtitles_sha256 == hashlib.sha256(
        Path(document.subtitles_path).read_bytes()
    ).hexdigest()

    second = service.run(
        edit_plan_artifact=plan_artifact, encoder=None, headline=None,
        progress_cb=lambda _progress, _message: None, should_cancel=lambda: False,
        render_settings=render_settings,
    )
    assert second["cached"] is True
    assert second["render_artifact_id"] == first["render_artifact_id"]
    assert len(domain.list_stage_artifacts(project.id, "render")) == 1
    assert len(domain.list_stage_artifacts(project.id, "render_overlay")) == 1

    jobs = JobStore(config.paths.database)
    job = jobs.create(JobCreate(
        type=JobType.RENDER, project_id=project.id,
        payload={
            "edit_plan_artifact_id": plan_artifact.id,
            "render_settings": render_settings.model_dump(mode="json"),
        },
    ))
    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    finished = jobs.get(job.id)
    assert finished.status == JobStatus.SUCCEEDED
    assert finished.result is not None and finished.result["cached"] is True


def test_render_rejects_unknown_encoder_without_fallback(tmp_path: Path) -> None:
    del tmp_path
    with pytest.raises(RenderPreconditionError, match="encoder não suportado"):
        _encoder_args("mystery_encoder")


def test_render_rejects_missing_font_without_fallback() -> None:
    with pytest.raises(RenderPreconditionError, match="não está instalada sem fallback"):
        _resolve_font_family("CorteX Font That Does Not Exist")


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg and ffprobe required")
def test_render_with_none_animations_does_not_hit_zero_range_interpolation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Render none animations")
    source_path = tmp_path / "projects" / project.id / "source" / "source.mp4"
    source_path.parent.mkdir(parents=True)
    subprocess.run([
        str(config.render.ffmpeg), "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
        str(source_path),
    ], check=True, timeout=60)
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="source.mp4",
        stored_path=str(source_path.relative_to(tmp_path)), sha256=source_sha,
        size_bytes=source_path.stat().st_size,
        probe=probe_media(config.render.ffprobe, source_path),
    ))
    transcript_path = tmp_path / "projects" / project.id / "transcripts" / "transcript.json"
    transcript_path.parent.mkdir(parents=True)
    transcript = TranscriptDocument(
        language="pt",
        duration_seconds=2.0,
        engine=EngineInfo(
            model="fixture", requested_device="cpu", effective_device="cpu",
            requested_compute_type="int8", effective_compute_type="int8",
            batch_size=1, vad=True,
        ),
        segments=[TranscriptSegment(
            id=0, start=0.0, end=1.9, text="animacao none deve renderizar",
            avg_logprob=-0.1, no_speech_prob=0.01,
            words=[
                TranscriptWord(start=0.1, end=0.5, word="animacao", probability=0.99),
                TranscriptWord(start=0.55, end=0.9, word="none", probability=0.99),
                TranscriptWord(start=1.0, end=1.6, word="deve", probability=0.99),
            ],
        )],
    )
    transcript_path.write_text(transcript.model_dump_json(), encoding="utf-8")
    transcript_artifact = domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project.id, source_asset_id=source.id, path=str(transcript_path),
        audio_sha256="audio", engine="fixture", model="fixture", device="cpu",
        compute_type="int8", language="pt", vad=True, batch_size=1,
        duration_seconds=2.0,
    ))
    plan = EditPlanDocument(
        project_id=project.id, source_asset_id=source.id,
        transcript_artifact_id=transcript_artifact.id, analysis_artifact_id="analysis",
        input_hash="edit-plan-input-none", clip_start=0.0, clip_end=1.9,
        profile="balanced",
        segments=[EditSegment(start=0.0, end=1.9, timeline_order=0)],
        timeline_duration_seconds=1.9,
        diagnostics=EditPlanDiagnostics(
            profile="balanced", waveform_used=True, candidate_pauses=0, cuts=0,
            saved_seconds=0.0, crossfade=0.0, vad_used=True,
        ),
        quality=EditQualityReport(passed=True, issues=[], profile="balanced", degraded=False),
    )
    plan_path = tmp_path / "projects" / project.id / "edit_plans" / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    plan_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="edit_plan", path=str(plan_path),
        input_hash=plan.input_hash,
    ))

    service = RenderService(config, domain)
    render_settings = RenderSettings(
        encoder="libx264",
        canvas=RenderCanvasSettings(width=320, height=320, fps=30),
        captions=RenderCaptionSettings(
            enabled=True, font_family="Montserrat", font_size=28,
            words_per_cue=2, outline=False,
            animation=RenderAnimationSettings(style="none", duration_seconds=0.18),
        ),
        headline=RenderHeadlineSettings(
            enabled=True, text="Sem interpolacao vazia", font_family="Montserrat",
            font_size=36, duration_seconds=1.7,
            animation=RenderHeadlineAnimationSettings(
                entrance="none", exit="none", duration_seconds=0.24,
            ),
        ),
        subtitles=RenderSubtitleSettings(sidecar_srt=True),
    )
    result = service.run(
        edit_plan_artifact=plan_artifact, encoder=None, headline=None,
        progress_cb=lambda _progress, _message: None, should_cancel=lambda: False,
        render_settings=render_settings,
    )
    assert result["cached"] is False
