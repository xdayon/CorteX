from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from asgi_client import ASGITestClient as TestClient

from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.face_service import FaceIndexPreconditionError, FaceIndexService
from cortex.analyze.scene_service import SceneIndexService
from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import JobStatus
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


def _build_talking_head_video(path: Path, ffmpeg: str) -> None:
    """A short synthetic clip (no real face) - good enough to exercise the
    sampling/service/cache plumbing without asserting on detection content."""
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc=size=640x360:rate=10:duration=4", "-pix_fmt", "yuv420p", str(path),
    ], check=True, timeout=30)


def _setup_project_with_scene_index(tmp_path: Path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Face index fixture")

    source_dir = config.paths.projects_dir / project.id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    media_path = source_dir / "clip.mp4"
    _build_talking_head_video(media_path, str(config.render.ffmpeg))

    asset = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="clip.mp4",
        stored_path=str(media_path.relative_to(config.paths.data_dir)),
        sha256="fake-sha256", size_bytes=media_path.stat().st_size,
    ))

    scene_service = SceneIndexService(config, domain)
    scene_result = scene_service.run(
        source_asset=asset, threshold=10.0,
        progress_cb=lambda *_a: None, should_cancel=lambda: False,
    )
    scene_artifact = domain.get_stage_artifact(scene_result["scene_index_artifact_id"])
    return config, domain, project, asset, scene_artifact


# -- 1. Precondition: scene_index missing ------------------------------------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_face_index_service_requires_scene_index_file(tmp_path: Path) -> None:
    config, domain, project, asset, scene_artifact = _setup_project_with_scene_index(tmp_path)
    Path(scene_artifact.path).unlink()

    service = FaceIndexService(config, domain)
    with pytest.raises(FaceIndexPreconditionError):
        service.run(
            source_asset=asset, scene_index_artifact=scene_artifact, sample_fps=1.0,
            progress_cb=lambda *_a: None, should_cancel=lambda: False,
        )


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_face_index_endpoint_rejects_missing_scene_index(tmp_path: Path) -> None:
    config, domain, project, asset, _scene_artifact = _setup_project_with_scene_index(tmp_path)
    client = TestClient(create_app(config))
    response = client.post(
        f"/api/v1/projects/{project.id}/faces",
        json={"source_asset_id": asset.id, "scene_index_artifact_id": "does-not-exist"},
    )
    assert response.status_code == 404, response.text


# -- 2. Service: detection, persistence and cache hit ------------------------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_face_index_service_detects_and_caches(tmp_path: Path) -> None:
    config, domain, project, asset, scene_artifact = _setup_project_with_scene_index(tmp_path)

    service = FaceIndexService(config, domain)
    progress: list[float] = []

    first = service.run(
        source_asset=asset, scene_index_artifact=scene_artifact, sample_fps=1.0,
        progress_cb=lambda value, _message: progress.append(value),
        should_cancel=lambda: False,
    )
    assert first["cached"] is False
    assert progress == sorted(progress) and progress[-1] == 100.0

    document = FaceIndexDocument.model_validate_json(Path(first["face_index_path"]).read_text())
    assert document.schema_version == 2
    assert document.engine.recognizer == "sface_2021dec"
    assert document.scene_index_artifact_id == scene_artifact.id
    assert document.frame_count > 0
    assert len(document.scenes) > 0
    assert len(domain.list_stage_artifacts(project.id, "face_index")) == 1

    second = service.run(
        source_asset=asset, scene_index_artifact=scene_artifact, sample_fps=1.0,
        progress_cb=lambda _v, _m: None, should_cancel=lambda: False,
    )
    assert second["cached"] is True
    assert second["face_index_artifact_id"] == first["face_index_artifact_id"]
    assert len(domain.list_stage_artifacts(project.id, "face_index")) == 1


# -- 3. API endpoints ---------------------------------------------------------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_face_index_endpoint_creates_job_and_get_returns_document(tmp_path: Path) -> None:
    config, domain, project, asset, scene_artifact = _setup_project_with_scene_index(tmp_path)

    client = TestClient(create_app(config))
    response = client.post(
        f"/api/v1/projects/{project.id}/faces",
        json={"source_asset_id": asset.id, "scene_index_artifact_id": scene_artifact.id},
    )
    assert response.status_code == 201, response.text
    queued = response.json()
    assert queued["type"] == "face_analysis" and queued["status"] == "queued"

    jobs = JobStore(config.paths.database)
    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued["id"])
    assert final.status == JobStatus.SUCCEEDED, final.error
    assert final.result is not None and final.result["cached"] is False

    artifact_id = final.result["face_index_artifact_id"]
    fetched = client.get(f"/api/v1/projects/{project.id}/faces/{artifact_id}")
    assert fetched.status_code == 200, fetched.text
    payload = fetched.json()
    assert payload["artifact"]["stage"] == "face_index"
    assert payload["document"]["frame_count"] > 0


def test_face_index_endpoint_rejects_missing_source(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Face index missing source")
    asset = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="ghost.mp4",
        stored_path="source/ghost.mp4", sha256="fake-sha256", size_bytes=100,
    ))
    client = TestClient(create_app(config))
    response = client.post(
        f"/api/v1/projects/{project.id}/faces",
        json={"source_asset_id": asset.id, "scene_index_artifact_id": "whatever"},
    )
    assert response.status_code == 409, response.text
