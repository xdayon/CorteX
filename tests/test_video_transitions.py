from __future__ import annotations

import hashlib
import json
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
from cortex.render.schemas import (
    RenderCanvasSettings,
    RenderCaptionSettings,
    RenderDocument,
    RenderHeadlineSettings,
    RenderSettings,
    RenderSubtitleSettings,
)
from cortex.render.service import RenderService
from cortex.transcribe.schemas import EngineInfo, TranscriptDocument
from conftest import requires_remotion_e2e


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
            "encoder": "libx264",
            "width": 320,
            "height": 320,
            "fps": 30,
        }),
    })


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg and ffprobe required",
)
@requires_remotion_e2e
def test_multi_segment_crossfade_persists_av_quality_and_headline(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Video transition fixture")

    source_path = config.paths.projects_dir / project.id / "source" / "source.mp4"
    source_path.parent.mkdir(parents=True)
    subprocess.run(
        [
            str(config.render.ffmpeg), "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
            str(source_path),
        ],
        check=True,
        timeout=60,
    )
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id,
        kind=SourceKind.UPLOAD,
        original_filename="source.mp4",
        stored_path=str(source_path.relative_to(tmp_path)),
        sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        size_bytes=source_path.stat().st_size,
        probe=probe_media(config.render.ffprobe, source_path),
    ))

    transcript_path = config.paths.projects_dir / project.id / "transcripts" / "transcript.json"
    transcript_path.parent.mkdir(parents=True)
    transcript_document = TranscriptDocument(
        language="pt",
        duration_seconds=4.0,
        engine=EngineInfo(
            model="fixture",
            requested_device="cpu",
            effective_device="cpu",
            requested_compute_type="int8",
            effective_compute_type="int8",
            batch_size=1,
            vad=True,
        ),
        segments=[],
    )
    transcript_path.write_text(transcript_document.model_dump_json(), encoding="utf-8")
    transcript = domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project.id,
        source_asset_id=source.id,
        path=str(transcript_path),
        audio_sha256="fixture-audio",
        engine="fixture",
        model="fixture",
        device="cpu",
        compute_type="int8",
        language="pt",
        vad=True,
        batch_size=1,
        duration_seconds=4.0,
    ))

    plan = EditPlanDocument(
        project_id=project.id,
        source_asset_id=source.id,
        transcript_artifact_id=transcript.id,
        analysis_artifact_id="fixture-analysis",
        input_hash="video-transition-plan",
        clip_start=0.0,
        clip_end=3.8,
        profile="balanced",
        segments=[
            EditSegment(
                start=0.0,
                end=1.4,
                timeline_order=0,
                transition=EditTransition(
                    video="micro_dissolve",
                    audio="equal_power_crossfade",
                    duration=0.06,
                ),
            ),
            EditSegment(start=2.2, end=3.8, timeline_order=1),
        ],
        timeline_duration_seconds=2.94,
        diagnostics=EditPlanDiagnostics(
            profile="balanced",
            waveform_used=True,
            candidate_pauses=1,
            cuts=1,
            saved_seconds=0.8,
            crossfade=0.06,
            vad_used=True,
        ),
        quality=EditQualityReport(passed=True, issues=[], profile="balanced", degraded=False),
    )
    plan_path = config.paths.projects_dir / project.id / "edit_plans" / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    plan_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id,
        stage="edit_plan",
        path=str(plan_path),
        input_hash=plan.input_hash,
    ))

    settings = RenderSettings(
        encoder="libx264",
        canvas=RenderCanvasSettings(width=320, height=320, fps=30),
        captions=RenderCaptionSettings(
            enabled=False,
            font_family="Montserrat",
            font_size=28,
            words_per_cue=3,
            outline=False,
        ),
        headline=RenderHeadlineSettings(
            enabled=True,
            text="A pergunta que muda tudo",
            font_family="Montserrat",
            font_size=36,
            duration_seconds=1.6,
        ),
        subtitles=RenderSubtitleSettings(sidecar_srt=False),
    )
    result = RenderService(config, domain).run(
        edit_plan_artifact=plan_artifact,
        encoder=None,
        headline=None,
        progress_cb=lambda _progress, _message: None,
        should_cancel=lambda: False,
        render_settings=settings,
    )

    artifact = domain.get_stage_artifact(result["render_artifact_id"])
    assert artifact is not None
    manifest_path = Path(artifact.path)
    document = RenderDocument.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    output_path = Path(document.output_path)
    probe = json.loads(subprocess.run(
        [
            str(config.render.ffprobe), "-v", "error",
            "-show_entries", "format=duration:stream=codec_type",
            "-of", "json", str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout)

    assert result["cached"] is False
    assert result["publish_ready"] is True
    assert artifact.stage == "render"
    assert artifact.schema_version == document.schema_version
    assert manifest_path.is_file() and output_path.is_file()
    assert document.output_sha256 == hashlib.sha256(output_path.read_bytes()).hexdigest()
    assert {stream["codec_type"] for stream in probe["streams"]} == {"audio", "video"}
    assert float(probe["format"]["duration"]) == pytest.approx(2.94, abs=0.15)
    assert document.quality.passed is True
    assert document.quality.has_audio is True and document.quality.has_video is True
    assert document.quality.actual_duration_seconds == pytest.approx(2.94, abs=0.15)
    assert document.quality.headline_present is True
    assert document.transitions[0].requested_kind == "crossfade"
    assert document.transitions[0].effective_kind == "crossfade"
    assert document.transitions[0].requested_audio_offset_seconds == 0.0
    assert document.transitions[0].effective_audio_offset_seconds == 0.0
    assert document.overlays is not None
    assert document.overlays.headline_enabled is True
    assert document.overlays.headline_text == "A pergunta que muda tudo"
    assert document.overlays.artifact_path is not None
    overlay_path = Path(document.overlays.artifact_path)
    assert overlay_path.is_file()
    assert document.overlays.artifact_sha256 == hashlib.sha256(overlay_path.read_bytes()).hexdigest()
    assert document.publication is not None and document.publication.publish_ready is True
    assert document.publication.technical is not None
    assert document.publication.technical.full_decode_passed is True
    assert all(check.status != "fail" for check in document.publication.checks)
