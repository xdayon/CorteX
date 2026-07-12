from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex.analyze.camera_schemas import CameraTimelineDocument
from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument
from cortex.analyze.identity_service import (
    IdentityIndexPreconditionError,
    IdentityIndexService,
    cluster_identity_observations,
)
from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
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
    })


def _observation(name: str, embedding: list[float]) -> dict:
    return {
        "observation_id": name, "embedding": embedding, "time_us": 0,
        "scene_index": 0, "layout_id": name, "local_track_id": name,
        "assignment_similarity": None, "ambiguous_match": False,
    }


def test_complete_link_clusters_matches_and_isolates_ambiguous_observation() -> None:
    observations = [
        _observation("a", [1.0, 0.0]),
        _observation("b", [0.0, 1.0]),
        _observation("ambiguous", [0.7071, 0.7071]),
        _observation("a2", [0.999, 0.01]),
    ]
    clusters = cluster_identity_observations(observations, threshold=0.363, ambiguity_margin=0.08)

    assert [[item["observation_id"] for item in cluster] for cluster in clusters] == [
        ["a", "a2"], ["b"], ["ambiguous"],
    ]
    assert clusters[2][0]["ambiguous_match"] is True


def _write_artifact(
    domain: DomainStore, project_id: str, stage: str, path: Path, document: object,
) -> StageArtifact:
    path.write_text(json.dumps(document.model_dump(mode="json")), encoding="utf-8")  # type: ignore[attr-defined]
    return domain.create_stage_artifact(StageArtifact(
        project_id=project_id, stage=stage, path=str(path), input_hash=f"{stage}-hash",
    ))


def _fixture(tmp_path: Path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Identity index")
    landmark = {
        "right_eye": [0.4, 0.4], "left_eye": [0.6, 0.4], "nose_tip": [0.5, 0.5],
        "right_mouth_corner": [0.43, 0.62], "left_mouth_corner": [0.57, 0.62],
    }
    face_document = FaceIndexDocument.model_validate({
        "project_id": project.id, "source_asset_id": "source-id", "source_sha256": "source-sha",
        "scene_index_artifact_id": "scene-id", "input_hash": "face-doc", "duration_seconds": 3.0,
        "frames": [
            {"time": 0.5, "shot_type": "close", "faces": [{"x": 0.3, "y": 0.2, "width": 0.3, "height": 0.4, "score": 0.9, "landmarks": landmark, "track_id": "person_left", "embedding": [1.0, 0.0, 0.0]}]},
            {"time": 1.5, "shot_type": "close", "faces": [{"x": 0.4, "y": 0.2, "width": 0.3, "height": 0.4, "score": 0.9, "landmarks": landmark, "track_id": "person_right", "embedding": [0.999, 0.01, 0.0]}]},
            {"time": 2.5, "shot_type": "close", "faces": [{"x": 0.4, "y": 0.2, "width": 0.3, "height": 0.4, "score": 0.9, "landmarks": landmark, "track_id": "person_right", "embedding": [0.0, 1.0, 0.0]}]},
        ],
        "scenes": [], "frame_count": 3,
        "engine": {
            "detector": "yunet", "model_path": "yunet.onnx", "providers": ["CPUExecutionProvider"],
            "score_threshold": 0.8, "nms_threshold": 0.3, "sample_fps": 1.0,
            "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test", "recognizer": "sface_2021dec",
            "recognition_model_path": "sface.onnx", "recognition_model_sha256": "sface-sha",
            "embedding_dimension": 3, "cosine_match_threshold": 0.363,
        },
    })
    face = _write_artifact(domain, project.id, "face_index", tmp_path / "faces.json", face_document)
    camera_document = CameraTimelineDocument.model_validate({
        "project_id": project.id, "source_asset_id": "source-id", "source_sha256": "source-sha",
        "scene_index_artifact_id": "scene-id", "scene_index_input_hash": "scene-hash",
        "face_index_artifact_id": face.id, "face_index_input_hash": face.input_hash,
        "speaker_timeline_artifact_id": "speaker-id", "speaker_timeline_input_hash": "speaker-hash",
        "input_hash": "camera-doc", "duration_us": 3_000_000,
        "scenes": [
            {"scene_index": 0, "start_us": 0, "end_us": 1_000_000, "layout_id": "layout-a", "shot_type": "close", "role": "unknown", "visible_track_ids": ["person_left"], "speaker_alignment": "no_speech", "speech_coverage": 0.0, "confidence": 0.4, "evidence": []},
            {"scene_index": 1, "start_us": 1_000_000, "end_us": 2_000_000, "layout_id": "layout-b", "shot_type": "close", "role": "unknown", "visible_track_ids": ["person_right"], "speaker_alignment": "no_speech", "speech_coverage": 0.0, "confidence": 0.4, "evidence": []},
            {"scene_index": 2, "start_us": 2_000_000, "end_us": 3_000_000, "layout_id": "layout-b", "shot_type": "close", "role": "unknown", "visible_track_ids": ["person_right"], "speaker_alignment": "no_speech", "speech_coverage": 0.0, "confidence": 0.4, "evidence": []},
        ],
        "engine": {"algorithm": "test", "algorithm_version": "1", "dominant_speaker_min_share": 0.55, "identity_scope": "layout_track_only"},
    })
    camera = _write_artifact(
        domain, project.id, "camera_timeline", tmp_path / "cameras.json", camera_document,
    )
    return config, domain, project, face, camera


def test_identity_service_confirms_only_cross_layout_embedding_match_and_caches(tmp_path: Path) -> None:
    config, domain, project, face, camera = _fixture(tmp_path)
    service = IdentityIndexService(config, domain)
    first = service.run(
        face_index_artifact=face, camera_timeline_artifact=camera,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    document = IdentityIndexDocument.model_validate_json(
        Path(first["identity_index_path"]).read_text(encoding="utf-8")
    )
    second = service.run(
        face_index_artifact=face, camera_timeline_artifact=camera,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )

    assert [identity.status for identity in document.identities] == ["confirmed", "single_layout"]
    assert document.identities[0].local_track_ids == ["person_left", "person_right"]
    assert document.engine.identity_scope == "cross_layout_face_embedding"
    assert second["cached"] is True
    assert len(domain.list_stage_artifacts(project.id, "identity_index")) == 1


def test_identity_service_rejects_face_index_without_sface_provenance(tmp_path: Path) -> None:
    config, domain, _project, face, camera = _fixture(tmp_path)
    payload = json.loads(Path(face.path).read_text(encoding="utf-8"))
    payload["engine"]["recognizer"] = None
    Path(face.path).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(IdentityIndexPreconditionError, match="proveniência SFace"):
        IdentityIndexService(config, domain).run(
            face_index_artifact=face, camera_timeline_artifact=camera,
            progress_cb=lambda *_args: None, should_cancel=lambda: False,
        )


def test_identity_worker_persists_artifact(tmp_path: Path) -> None:
    config, domain, project, face, camera = _fixture(tmp_path)
    jobs = JobStore(config.paths.database)
    queued = jobs.create(JobCreate(
        type=JobType.IDENTITY_ANALYSIS, project_id=project.id,
        payload={"face_index_artifact_id": face.id, "camera_timeline_artifact_id": camera.id},
    ))

    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued.id)
    assert final.status == JobStatus.SUCCEEDED, final.error
    assert final.result is not None
    artifact = domain.get_stage_artifact(final.result["identity_index_artifact_id"])
    assert artifact.stage == "identity_index" and Path(artifact.path).exists()


def test_identity_routes_are_exposed_in_openapi(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    paths = create_app(config).openapi()["paths"]
    collection = f"{config.app.api_prefix}/projects/{{project_id}}/identities"

    assert "post" in paths[collection]
    assert "get" in paths[collection + "/{artifact_id}"]
