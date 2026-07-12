from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from cortex.analyze.face_schemas import FaceDetection, FaceIndexDocument, FaceLandmarks
from cortex.analyze.scene_schemas import SceneIndexDocument
from cortex.analyze.visual_quality import face_touches_edge, frame_metrics, normalized_frame_delta
from cortex.analyze.visual_quality_schemas import VisualQualityDocument
from cortex.analyze.visual_quality_service import (
    VisualQualityPreconditionError,
    VisualQualityService,
)
from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact
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


def _write_artifact(
    domain: DomainStore, project_id: str, stage: str, path: Path, document: object,
) -> StageArtifact:
    path.write_text(json.dumps(document.model_dump(mode="json")), encoding="utf-8")  # type: ignore[attr-defined]
    return domain.create_stage_artifact(StageArtifact(
        project_id=project_id,
        stage=stage,
        path=str(path),
        input_hash=f"{stage}-hash",
    ))


def _fixture(tmp_path: Path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Visual quality")
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"synthetic source placeholder")
    asset = domain.create_source_asset(SourceAsset(
        project_id=project.id,
        kind=SourceKind.UPLOAD,
        original_filename="source.mp4",
        stored_path=source_path.name,
        sha256="source-sha",
        size_bytes=source_path.stat().st_size,
    ))
    scene_document = SceneIndexDocument.model_validate({
        "project_id": project.id,
        "source_asset_id": asset.id,
        "source_sha256": asset.sha256,
        "input_hash": "scene-doc",
        "duration_seconds": 2.0,
        "cuts": [{"time": 1.0, "score": 20.0}],
        "scenes": [
            {"index": 0, "start": 0.0, "end": 1.0},
            {"index": 1, "start": 1.0, "end": 2.0},
        ],
        "cut_count": 1,
        "engine": {
            "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test", "filter": "scdet",
            "threshold_requested": 10.0, "threshold_effective": 10.0,
        },
    })
    scene = _write_artifact(domain, project.id, "scene_index", tmp_path / "scenes.json", scene_document)
    landmark = {
        "right_eye": [0.02, 0.1], "left_eye": [0.1, 0.1], "nose_tip": [0.06, 0.15],
        "right_mouth_corner": [0.03, 0.2], "left_mouth_corner": [0.09, 0.2],
    }
    face_document = FaceIndexDocument.model_validate({
        "project_id": project.id,
        "source_asset_id": asset.id,
        "source_sha256": asset.sha256,
        "scene_index_artifact_id": scene.id,
        "input_hash": "face-doc",
        "duration_seconds": 2.0,
        "frames": [
            {"time": 0.5, "shot_type": "close", "faces": [{
                "x": 0.0, "y": 0.05, "width": 0.2, "height": 0.3,
                "score": 0.9, "landmarks": landmark, "track_id": "person_left",
            }]},
            {"time": 1.5, "shot_type": "close", "faces": [{
                "x": 0.4, "y": 0.2, "width": 0.2, "height": 0.3,
                "score": 0.9, "landmarks": landmark, "track_id": "person_left",
            }]},
        ],
        "scenes": [
            {"scene_index": 0, "dominant_shot_type": "close", "track_ids_present": ["person_left"], "sample_count": 1},
            {"scene_index": 1, "dominant_shot_type": "close", "track_ids_present": ["person_left"], "sample_count": 1},
        ],
        "frame_count": 2,
        "engine": {
            "detector": "test", "model_path": "test", "providers": ["CPUExecutionProvider"],
            "score_threshold": 0.8, "nms_threshold": 0.3, "sample_fps": 1.0,
            "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test",
        },
    })
    face = _write_artifact(domain, project.id, "face_index", tmp_path / "faces.json", face_document)
    return config, domain, project, asset, scene, face


def _fake_frame(_ffmpeg: object, _source: Path, timestamp: float) -> np.ndarray:
    if timestamp < 1.0:
        return np.zeros((32, 32, 3), dtype=np.uint8)
    checker = (np.indices((32, 32)).sum(axis=0) % 2 * 255).astype(np.uint8)
    if timestamp >= 1.5:
        checker = 255 - checker
    return np.repeat(checker[..., None], 3, axis=2)


def test_visual_metrics_detect_black_freeze_blur_and_face_edge() -> None:
    black = np.zeros((16, 16, 3), dtype=np.uint8)
    checker = (np.indices((16, 16)).sum(axis=0) % 2 * 255).astype(np.uint8)
    checker = np.repeat(checker[..., None], 3, axis=2)
    mean_luma, black_ratio, black_blur = frame_metrics(black)
    _mean, _ratio, checker_blur = frame_metrics(checker)

    assert mean_luma == 0.0 and black_ratio == 1.0
    assert black_blur == 0.0 and checker_blur > 20.0
    assert normalized_frame_delta(black, black) == 0.0
    face = FaceDetection(
        x=0.0, y=0.1, width=0.2, height=0.3, score=0.9,
        landmarks=FaceLandmarks(
            right_eye=(0.02, 0.1), left_eye=(0.1, 0.1), nose_tip=(0.06, 0.15),
            right_mouth_corner=(0.03, 0.2), left_mouth_corner=(0.09, 0.2),
        ),
    )
    assert face_touches_edge(face, 0.01) is True


def test_visual_quality_service_persists_scene_metrics_and_reuses_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, domain, project, asset, scene, face = _fixture(tmp_path)
    monkeypatch.setattr("cortex.analyze.visual_quality_service._extract_frame_rgb", _fake_frame)
    monkeypatch.setattr("cortex.analyze.visual_quality_service.ffmpeg_version", lambda _path: "test")
    service = VisualQualityService(config, domain)
    first = service.run(
        source_asset=asset,
        scene_index_artifact=scene,
        face_index_artifact=face,
        sample_fps=2.0,
        progress_cb=lambda *_args: None,
        should_cancel=lambda: False,
    )
    document = VisualQualityDocument.model_validate_json(
        Path(first["visual_quality_path"]).read_text(encoding="utf-8")
    )
    second = service.run(
        source_asset=asset,
        scene_index_artifact=scene,
        face_index_artifact=face,
        sample_fps=2.0,
        progress_cb=lambda *_args: None,
        should_cancel=lambda: False,
    )

    assert document.scenes[0].issues == ["black", "blurred", "frozen", "face_edge_occlusion"]
    assert document.scenes[0].usable is False
    assert document.scenes[1].issues == []
    assert document.scenes[1].usable is True
    assert document.engine.decoder_effective == "software"
    assert second["cached"] is True
    assert len(domain.list_stage_artifacts(project.id, "visual_quality_index")) == 1


def test_visual_quality_rejects_mismatched_face_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, domain, _project, asset, scene, face = _fixture(tmp_path)
    payload = json.loads(Path(face.path).read_text(encoding="utf-8"))
    payload["scene_index_artifact_id"] = "other-scenes"
    Path(face.path).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("cortex.analyze.visual_quality_service.ffmpeg_version", lambda _path: "test")

    with pytest.raises(VisualQualityPreconditionError, match="cadeia visual"):
        VisualQualityService(config, domain).run(
            source_asset=asset,
            scene_index_artifact=scene,
            face_index_artifact=face,
            sample_fps=2.0,
            progress_cb=lambda *_args: None,
            should_cancel=lambda: False,
        )


def test_visual_quality_worker_persists_real_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, domain, project, asset, scene, face = _fixture(tmp_path)
    monkeypatch.setattr("cortex.analyze.visual_quality_service._extract_frame_rgb", _fake_frame)
    monkeypatch.setattr("cortex.analyze.visual_quality_service.ffmpeg_version", lambda _path: "test")
    jobs = JobStore(config.paths.database)
    queued = jobs.create(JobCreate(
        type=JobType.VISUAL_QUALITY_ANALYSIS,
        project_id=project.id,
        payload={
            "source_asset_id": asset.id,
            "scene_index_artifact_id": scene.id,
            "face_index_artifact_id": face.id,
            "sample_fps": 2.0,
        },
    ))

    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued.id)
    assert final.status == JobStatus.SUCCEEDED, final.error
    assert final.result is not None
    artifact = domain.get_stage_artifact(final.result["visual_quality_artifact_id"])
    assert artifact.stage == "visual_quality_index"
    assert Path(artifact.path).exists()


def test_visual_quality_routes_are_exposed_in_openapi(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    paths = create_app(config).openapi()["paths"]

    collection = f"{config.app.api_prefix}/projects/{{project_id}}/visual-quality"
    artifact = collection + "/{artifact_id}"
    assert "post" in paths[collection]
    assert "get" in paths[artifact]
