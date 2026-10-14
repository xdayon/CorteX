"""Rows written while the camera-dynamics stack existed must stay readable."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex.config import load_config
from cortex.domain.models import (
    ClipRevision,
    ExportBatch,
    ExportItem,
    ExportItemStatus,
    SourceAsset,
    SourceKind,
    StageArtifact,
    WorkflowRun,
)
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.render.schemas import RenderDocument, RenderSettings
from cortex.schemas import JobCreate, JobStatus, JobType
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine

LEGACY_JOB_TYPES = [
    JobType.FACE_ANALYSIS, JobType.SPEAKER_ANALYSIS, JobType.CAMERA_ANALYSIS,
    JobType.VISUAL_QUALITY_ANALYSIS, JobType.IDENTITY_ANALYSIS,
    JobType.REACTION_CANDIDATE_ANALYSIS, JobType.CAMERA_PLANNING,
]


def _config(tmp_path: Path):
    base = load_config()
    return base.model_copy(update={"paths": base.paths.model_copy(update={
        "data_dir": tmp_path, "projects_dir": tmp_path / "projects",
        "cache_dir": tmp_path / "cache", "output_dir": tmp_path / "output",
        "database": tmp_path / "cortex.sqlite3",
    })})


def _settings(mode: str = "speaker_auto") -> dict:
    return {
        "schema_version": 1, "encoder": "h264_nvenc",
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "framing": {
            "schema_version": 1, "mode": mode, "primary_side": "left",
            "punch_in": {"enabled": True, "scale": 1.12, "anchor": "center"},
            "scene_overrides": [{"scene_index": 2, "target": "full"}],
        },
        "captions": {"enabled": True, "font_family": "Montserrat", "font_size": 56,
                     "words_per_cue": 5, "outline": True},
        "headline": {"enabled": True, "font_family": "Montserrat", "font_size": 48,
                     "duration_seconds": 4},
        "subtitles": {"sidecar_srt": True},
    }


def _legacy_manifest() -> dict:
    """Schema-14 manifest shape from a speaker_auto render with a camera plan
    (ids only; copied from a real render artifact's metadata, anonymized)."""
    span = {
        "segment_order": 0, "scene_index": 1, "source_start_us": 1_000_000,
        "source_end_us": 4_000_000, "visual_origin": "primary_in_place",
        "mode": "face_crop", "track_id": "track-1", "reason": "speaker_close",
        "sample_count": 12, "crop_x": 410, "crop_y": 0, "crop_width": 608,
        "crop_height": 1080, "requested_zoom": 1.12, "effective_zoom": 1.12,
        "zoom_reason": "applied",
    }
    return {
        "schema_version": 14, "project_id": "p" * 32, "source_asset_id": "s" * 32,
        "edit_plan_artifact_id": "e" * 32,
        "camera_edit_plan_artifact_id": "c" * 32,
        "face_index_artifact_id": "f" * 32,
        "identity_index_artifact_id": "i" * 32,
        "target_identity_id": "identity-1",
        "static_face_crops": [{
            "segment_order": 0, "crop_x": 10, "crop_y": 0, "crop_width": 608,
            "crop_height": 1080, "provenance": {"fallback": "center_crop"},
        }],
        "auto_framing": [span],
        "auto_framing_inputs": {"algorithm_version": "1.3.0", "policy": "face_first"},
        "sources": [{"source_asset_id": "s" * 32, "sha256": "a" * 64,
                     "video_used": True, "audio_used": True}],
        "input_hash": "h" * 64, "output_path": "renders/render-x.mp4",
        "output_sha256": "b" * 64, "output_size_bytes": 10,
        "timeline_duration_seconds": 3.0, "segment_count": 1,
        "engine": {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "requested_encoder": "h264_nvenc",
                   "effective_encoder": "h264_nvenc", "width": 1080, "height": 1920, "fps": 30},
        "requested_settings": _settings("speaker_auto"),
        "effective_settings": _settings("speaker_face"),
        "quality": {
            "passed": True, "expected_duration_seconds": 3.0, "actual_duration_seconds": 3.0,
            "duration_delta_seconds": 0.0, "has_video": True, "has_audio": True, "issues": [],
            "warnings": ["auto_framing_context_preserved"],
        },
        "publication": {
            "publish_ready": True, "reasons": [], "warnings": ["auto_framing_context_preserved"],
            "loudness": {}, "visual": {}, "captions": {}, "safe_zones": {}, "encoder": {},
            "dimensions": {}, "hashes": {}, "provenance": {},
            "punch_in": {"scope": "camera_shot", "shots": [span]},
        },
    }


def test_legacy_render_manifest_still_validates() -> None:
    document = RenderDocument.model_validate_json(json.dumps(_legacy_manifest()))
    assert document.camera_edit_plan_artifact_id == "c" * 32
    assert document.auto_framing[0]["mode"] == "face_crop"
    assert document.static_face_crops[0]["provenance"] == {"fallback": "center_crop"}
    assert document.requested_settings.framing.mode == "speaker_auto"
    assert document.effective_settings.framing.mode == "speaker_face"
    assert document.requested_settings.framing.scene_overrides[0].target == "full"


def test_legacy_render_settings_stay_readable() -> None:
    for mode in ("face_static_crop", "speaker_auto", "speaker_face"):
        framing = RenderSettings.model_validate(_settings(mode)).framing
        assert framing.mode == mode and framing.primary_side == "left"


def test_legacy_jobs_load_from_the_store_and_fail_with_a_clear_message(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    jobs = JobStore(config.paths.database)
    project = domain.create_project("Legado")
    created = [
        jobs.create(JobCreate(type=job_type, project_id=project.id, payload={"legacy": True}))
        for job_type in LEGACY_JOB_TYPES
    ]
    assert {job.type for job in jobs.list(limit=50)} == set(LEGACY_JOB_TYPES)

    engine = FakeTranscriptionEngine()
    for _ in created:
        assert process_next(config, jobs, domain, engine)
    for job in created:
        final = jobs.get(job.id)
        assert final.status == JobStatus.FAILED
        assert "descontinuado" in (final.error or "")
    assert not process_next(config, jobs, domain, engine)


def test_legacy_export_item_status_and_revision_fields_stay_readable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Legado")
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, stored_path="source.mp4",
        sha256="a" * 64, size_bytes=1,
    ))
    artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="suggestion", path="s.json", input_hash="s",
    ))
    run = domain.create_workflow_run(WorkflowRun(
        project_id=project.id, source_asset_id=source.id, interviewer_identity_id="me",
        artifacts={"suggestion": artifact.id},
    ))
    revision = domain.create_clip_revision(ClipRevision(
        project_id=project.id, workflow_run_id=run.id, selection_artifact_id=artifact.id,
        selected_clips=[{"key": "k"}], render_settings=_settings("speaker_auto"),
        visual_artifacts={"scene": "scene-1", "camera": "camera-1", "identity": "identity-1"},
        interviewer_identity_id="me", reuse_reactions=True,
    ))
    reread = domain.get_clip_revision(revision.id)
    assert reread.reuse_reactions is True and reread.interviewer_identity_id == "me"
    assert reread.visual_artifacts["scene"] == "scene-1"

    batch = ExportBatch(
        project_id=project.id, workflow_run_id=run.id, revision_id=revision.id,
        idempotency_key="legacy", reaction_candidate_artifact_id="reactions",
    )
    batch = batch.model_copy(update={"items": [ExportItem(
        batch_id=batch.id, project_id=project.id, position=0, clip_key="k", title="K",
        clip={"key": "k"}, render_settings=_settings("speaker_auto"),
        status=ExportItemStatus.CAMERA_PLANNING, camera_edit_plan_artifact_id="camera-plan",
    )]})
    with domain.transaction() as connection:
        domain.create_export_batch(batch, connection=connection)
    stored = domain.get_export_batch(batch.id)
    assert stored.items[0].status == ExportItemStatus.CAMERA_PLANNING
    assert stored.items[0].camera_edit_plan_artifact_id == "camera-plan"
    assert stored.reaction_candidate_artifact_id == "reactions"


@pytest.mark.parametrize("job_type", LEGACY_JOB_TYPES)
def test_legacy_job_types_keep_their_wire_values(job_type: JobType) -> None:
    assert JobType(job_type.value) is job_type
