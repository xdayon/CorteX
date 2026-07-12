from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex.analyze.camera_schemas import CameraTimelineDocument
from cortex.analyze.camera_service import (
    CameraTimelinePreconditionError,
    CameraTimelineService,
    classify_camera_scene,
)
from cortex.analyze.face_schemas import FaceIndexDocument, SceneFaceSummary
from cortex.analyze.scene_schemas import SceneIndexDocument, SceneSegment
from cortex.analyze.speaker_schemas import SpeakerSegment, SpeakerTimelineDocument
from cortex.config import CortexConfig, load_config
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import JobCreate, JobStatus, JobType
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine


def _speaker(start: int, end: int, track: str, confidence: float = 0.9) -> SpeakerSegment:
    return SpeakerSegment(
        start_us=start, end_us=end, state="speaker", speaker_track_id=track,
        confidence=confidence, evidence="test",
    )


def test_close_is_speaker_close_only_when_visible_track_matches() -> None:
    scene = SceneSegment(index=0, start=0.0, end=2.0)
    summary = SceneFaceSummary(
        scene_index=0, dominant_shot_type="close",
        track_ids_present=["person_left"], sample_count=2,
    )
    confirmed = classify_camera_scene(scene, summary, [_speaker(0, 2_000_000, "person_left")])
    mismatch = classify_camera_scene(scene, summary, [_speaker(0, 2_000_000, "person_right")])

    assert confirmed.role == "speaker_close"
    assert confirmed.speaker_alignment == "confirmed"
    assert mismatch.role == "unknown"
    assert mismatch.speaker_alignment == "ambiguous"


def test_two_shot_and_wide_are_visual_roles_without_identity_claim() -> None:
    scene = SceneSegment(index=1, start=2.0, end=4.0)
    two_shot = SceneFaceSummary(
        scene_index=1, dominant_shot_type="two_shot",
        track_ids_present=["person_left", "person_right"], sample_count=2,
    )
    wide = two_shot.model_copy(update={"dominant_shot_type": "wide"})

    assert classify_camera_scene(scene, two_shot, []).role == "two_shot"
    assert classify_camera_scene(scene, wide, []).role == "wide"
    assert classify_camera_scene(scene, two_shot, []).speaker_alignment == "no_speech"


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
    })


def _write_artifact(
    domain: DomainStore, project_id: str, stage: str, path: Path, document: object,
) -> StageArtifact:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document.model_dump(mode="json")), encoding="utf-8")  # type: ignore[attr-defined]
    return domain.create_stage_artifact(StageArtifact(
        project_id=project_id, stage=stage, path=str(path), input_hash=f"{stage}-hash",
    ))


def _fixture(tmp_path: Path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Camera timeline")
    source_id = "source-id"
    source_sha = "source-sha"
    scene_document = SceneIndexDocument.model_validate({
        "project_id": project.id, "source_asset_id": source_id, "source_sha256": source_sha,
        "input_hash": "scene-doc", "duration_seconds": 4.0, "cuts": [],
        "scenes": [{"index": 0, "start": 0.0, "end": 2.0},
                   {"index": 1, "start": 2.0, "end": 4.0}],
        "cut_count": 0,
        "engine": {"ffmpeg_path": "ffmpeg", "ffmpeg_version": "test", "filter": "scdet",
                   "threshold_requested": 10.0, "threshold_effective": 10.0},
    })
    scene_artifact = _write_artifact(
        domain, project.id, "scene_index", tmp_path / "scenes.json", scene_document,
    )
    face_document = FaceIndexDocument.model_validate({
        "project_id": project.id, "source_asset_id": source_id, "source_sha256": source_sha,
        "scene_index_artifact_id": scene_artifact.id, "input_hash": "face-doc",
        "duration_seconds": 4.0, "frames": [], "frame_count": 0,
        "scenes": [
            {"scene_index": 0, "dominant_shot_type": "close",
             "track_ids_present": ["person_left"], "sample_count": 2},
            {"scene_index": 1, "dominant_shot_type": "two_shot",
             "track_ids_present": ["person_left", "person_right"], "sample_count": 2},
        ],
        "engine": {"detector": "test", "model_path": "test", "providers": ["CPUExecutionProvider"],
                   "score_threshold": 0.8, "nms_threshold": 0.3, "sample_fps": 1.0,
                   "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test"},
    })
    face_artifact = _write_artifact(
        domain, project.id, "face_index", tmp_path / "faces.json", face_document,
    )
    speaker_document = SpeakerTimelineDocument.model_validate({
        "project_id": project.id, "source_asset_id": source_id, "source_sha256": source_sha,
        "scene_index_artifact_id": scene_artifact.id, "scene_index_input_hash": scene_artifact.input_hash,
        "face_index_artifact_id": face_artifact.id, "face_index_input_hash": face_artifact.input_hash,
        "analysis_artifact_id": "analysis-id", "analysis_input_hash": "analysis-hash",
        "input_hash": "speaker-doc", "duration_us": 4_000_000, "observations": [],
        "segments": [
            {"start_us": 0, "end_us": 2_000_000, "state": "speaker",
             "speaker_track_id": "person_left", "confidence": 0.9, "evidence": "test"},
            {"start_us": 2_000_000, "end_us": 4_000_000, "state": "unknown",
             "confidence": 0.2, "evidence": "test"},
        ],
        "engine": {"algorithm": "test", "algorithm_version": "1", "sample_fps_requested": 4.0,
                   "sample_fps_effective": 4.0, "frame_width_requested": 320,
                   "frame_width_effective": 320, "vad_padding_seconds": 0.15,
                   "motion_threshold": 0.08, "winner_margin": 0.03, "ffmpeg_path": "ffmpeg",
                   "ffmpeg_version": "test", "decoder_requested": "software",
                   "decoder_effective": "software", "device_requested": "cpu",
                   "device_effective": "cpu"},
    })
    speaker_artifact = _write_artifact(
        domain, project.id, "speaker_timeline", tmp_path / "speakers.json", speaker_document,
    )
    return config, domain, project, scene_artifact, face_artifact, speaker_artifact


def test_camera_timeline_service_persists_and_reuses_cache(tmp_path: Path) -> None:
    config, domain, project, scene, face, speaker = _fixture(tmp_path)
    service = CameraTimelineService(config, domain)
    first = service.run(
        scene_index_artifact=scene, face_index_artifact=face,
        speaker_timeline_artifact=speaker, progress_cb=lambda *_args: None,
        should_cancel=lambda: False,
    )
    document = CameraTimelineDocument.model_validate_json(
        Path(first["camera_timeline_path"]).read_text(encoding="utf-8")
    )
    second = service.run(
        scene_index_artifact=scene, face_index_artifact=face,
        speaker_timeline_artifact=speaker, progress_cb=lambda *_args: None,
        should_cancel=lambda: False,
    )

    assert [item.role for item in document.scenes] == ["speaker_close", "two_shot"]
    assert document.engine.identity_scope == "layout_track_only"
    assert second["cached"] is True
    assert len(domain.list_stage_artifacts(project.id, "camera_timeline")) == 1


def test_camera_timeline_rejects_mismatched_speaker_chain(tmp_path: Path) -> None:
    config, domain, _project, scene, face, speaker = _fixture(tmp_path)
    payload = json.loads(Path(speaker.path).read_text(encoding="utf-8"))
    payload["face_index_artifact_id"] = "other-face-index"
    Path(speaker.path).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CameraTimelinePreconditionError, match="cadeia visual"):
        CameraTimelineService(config, domain).run(
            scene_index_artifact=scene, face_index_artifact=face,
            speaker_timeline_artifact=speaker, progress_cb=lambda *_args: None,
            should_cancel=lambda: False,
        )


def test_camera_timeline_worker_persists_real_artifact(tmp_path: Path) -> None:
    config, domain, project, scene, face, speaker = _fixture(tmp_path)
    jobs = JobStore(config.paths.database)
    queued = jobs.create(JobCreate(
        type=JobType.CAMERA_ANALYSIS,
        project_id=project.id,
        payload={
            "scene_index_artifact_id": scene.id,
            "face_index_artifact_id": face.id,
            "speaker_timeline_artifact_id": speaker.id,
        },
    ))

    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued.id)
    assert final.status == JobStatus.SUCCEEDED, final.error
    assert final.result is not None
    artifact = domain.get_stage_artifact(final.result["camera_timeline_artifact_id"])
    assert artifact.stage == "camera_timeline"
    assert Path(artifact.path).exists()
