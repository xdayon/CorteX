from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact
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
from cortex.render.service import RenderPreconditionError, RenderService, _encoder_args
from cortex.schemas import JobCreate, JobStatus, JobType
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
    plan = EditPlanDocument(
        project_id=project.id, source_asset_id=source.id,
        transcript_artifact_id="transcript", analysis_artifact_id="analysis",
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
    first = service.run(
        edit_plan_artifact=plan_artifact, encoder="libx264",
        progress_cb=lambda _progress, _message: None, should_cancel=lambda: False,
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

    second = service.run(
        edit_plan_artifact=plan_artifact, encoder="libx264",
        progress_cb=lambda _progress, _message: None, should_cancel=lambda: False,
    )
    assert second["cached"] is True
    assert second["render_artifact_id"] == first["render_artifact_id"]
    assert len(domain.list_stage_artifacts(project.id, "render")) == 1

    jobs = JobStore(config.paths.database)
    job = jobs.create(JobCreate(
        type=JobType.RENDER, project_id=project.id,
        payload={"edit_plan_artifact_id": plan_artifact.id, "encoder": "libx264"},
    ))
    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    finished = jobs.get(job.id)
    assert finished.status == JobStatus.SUCCEEDED
    assert finished.result is not None and finished.result["cached"] is True


def test_render_rejects_unknown_encoder_without_fallback(tmp_path: Path) -> None:
    del tmp_path
    with pytest.raises(RenderPreconditionError, match="encoder não suportado"):
        _encoder_args("mystery_encoder")
