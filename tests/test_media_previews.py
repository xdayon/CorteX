from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from asgi_client import ASGITestClient as TestClient

from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument
from cortex.api import create_app
from cortex.config import load_config
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact
from cortex.domain.store import DomainStore
from cortex.ingest.ffprobe import probe_media


def _fixture(tmp_path: Path):
    base = load_config()
    config = base.model_copy(update={
        "paths": base.paths.model_copy(update={
            "data_dir": tmp_path,
            "projects_dir": tmp_path / "projects",
            "cache_dir": tmp_path / "cache",
            "output_dir": tmp_path / "output",
            "database": tmp_path / "cortex.sqlite3",
        }),
    })
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Preview fixture")
    source_path = config.paths.projects_dir / project.id / "source" / "clip.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(config.render.ffmpeg), "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        str(source_path),
    ], check=True, timeout=30)
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id,
        kind=SourceKind.UPLOAD,
        original_filename="clip.mp4",
        stored_path=str(source_path.relative_to(config.paths.data_dir)),
        sha256=source_sha,
        size_bytes=source_path.stat().st_size,
        probe=probe_media(config.render.ffprobe, source_path),
    ))
    return config, domain, project, source


def test_source_and_identity_previews_are_real_cached_jpegs(tmp_path: Path) -> None:
    config, domain, project, source = _fixture(tmp_path)
    face_path = config.paths.projects_dir / project.id / "faces" / "faces.json"
    face_path.parent.mkdir(parents=True, exist_ok=True)
    face_document = FaceIndexDocument.model_validate({
        "project_id": project.id,
        "source_asset_id": source.id,
        "source_sha256": source.sha256,
        "scene_index_artifact_id": "scene",
        "input_hash": "face-input",
        "duration_seconds": 2.0,
        "frames": [{
            "time": 0.5,
            "shot_type": "close",
            "faces": [{
                "x": 0.05, "y": 0.2, "width": 0.2, "height": 0.4,
                "score": 0.99, "track_id": "track-1",
                "landmarks": {
                    "right_eye": [0.1, 0.3], "left_eye": [0.2, 0.3],
                    "nose_tip": [0.15, 0.4], "right_mouth_corner": [0.12, 0.5],
                    "left_mouth_corner": [0.18, 0.5],
                },
            }, {
                "x": 0.6, "y": 0.15, "width": 0.3, "height": 0.6,
                "score": 0.98, "track_id": "track-1",
                "landmarks": {
                    "right_eye": [0.68, 0.3], "left_eye": [0.82, 0.3],
                    "nose_tip": [0.75, 0.45], "right_mouth_corner": [0.7, 0.6],
                    "left_mouth_corner": [0.8, 0.6],
                },
            }],
        }],
        "scenes": [],
        "frame_count": 1,
        "engine": {
            "detector": "test", "model_path": "test.onnx", "providers": ["CPUExecutionProvider"],
            "score_threshold": 0.5, "nms_threshold": 0.3, "sample_fps": 1.0,
            "ffmpeg_path": str(config.render.ffmpeg), "ffmpeg_version": "test",
        },
    })
    face_path.write_text(face_document.model_dump_json(), encoding="utf-8")
    face_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="face_index", schema_version=2,
        path=str(face_path), input_hash="face-input",
    ))

    identity_path = config.paths.projects_dir / project.id / "identities" / "identities.json"
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_document = IdentityIndexDocument.model_validate({
        "project_id": project.id,
        "source_asset_id": source.id,
        "source_sha256": source.sha256,
        "face_index_artifact_id": face_artifact.id,
        "face_index_input_hash": face_artifact.input_hash,
        "camera_timeline_artifact_id": "camera",
        "camera_timeline_input_hash": "camera-input",
        "input_hash": "identity-input",
        "observations": [{
            "observation_id": "face-0-1", "time_us": 500_000, "scene_index": 0,
            "layout_id": "layout-1", "local_track_id": "track-1",
            "identity_id": "person-1", "status": "confirmed",
        }],
        "identities": [{
            "identity_id": "person-1", "status": "confirmed",
            "observation_ids": ["face-0-1"], "layout_ids": ["layout-1"],
            "scene_indices": [0], "local_track_ids": ["track-1"],
            "sample_count": 1, "evidence": ["fixture"],
        }],
        "unresolved_face_count": 0,
        "engine": {
            "algorithm": "test", "algorithm_version": "1", "recognizer": "sface_2021dec",
            "model_path": "test.onnx", "model_sha256": "a" * 64,
            "embedding_dimension": 128, "cosine_match_threshold": 0.5,
            "ambiguity_margin": 0.1,
        },
    })
    identity_path.write_text(identity_document.model_dump_json(), encoding="utf-8")
    identity_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="identity_index", schema_version=1,
        path=str(identity_path), input_hash="identity-input",
    ))

    client = TestClient(create_app(config))
    source_response = client.get(
        f"/api/v1/projects/{project.id}/sources/{source.id}/preview?time_seconds=0.5"
    )
    assert source_response.status_code == 200, source_response.text
    assert source_response.headers["content-type"] == "image/jpeg"
    assert source_response.content.startswith(b"\xff\xd8")

    face_response = client.get(
        f"/api/v1/projects/{project.id}/identities/{identity_artifact.id}/person-1/preview"
    )
    assert face_response.status_code == 200, face_response.text
    assert face_response.headers["content-type"] == "image/jpeg"
    assert face_response.content.startswith(b"\xff\xd8")
    assert len(list((identity_path.parent / "previews").glob("*.jpg"))) == 1
