from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cortex.analyze.multicam_sync_schemas import MulticamSyncDocument
from cortex.analyze.multicam_visual_schemas import IsoVisualIndex, MulticamVisualIndexDocument
from cortex.analyze.multicam_visual_service import (
    MulticamVisualIndexService,
    MulticamVisualPreconditionError,
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


def _artifact(
    domain: DomainStore, project_id: str, stage: str, path: Path, payload: object,
) -> StageArtifact:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return domain.create_stage_artifact(StageArtifact(
        project_id=project_id, stage=stage, path=str(path), input_hash=f"{stage}-{path.stem}",
    ))


def _fixture(tmp_path: Path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Multicam visual")
    sources = []
    for name in ("primary.mp4", "synced.mp4", "rejected.mp4"):
        path = tmp_path / name
        path.write_bytes(b"source")
        sources.append(domain.create_source_asset(SourceAsset(
            project_id=project.id, kind=SourceKind.UPLOAD, original_filename=name,
            stored_path=name, sha256=f"sha-{name}", size_bytes=path.stat().st_size,
        )))
    sync_document = MulticamSyncDocument.model_validate({
        "project_id": project.id,
        "primary_source_asset_id": sources[0].id,
        "primary_source_sha256": sources[0].sha256,
        "input_hash": "sync-doc",
        "cameras": [
            {"source_asset_id": sources[1].id, "source_sha256": sources[1].sha256,
             "status": "synced", "offset_us": 800_000, "correlation": 0.96,
             "peak_margin": 0.3, "overlap_seconds": 20.0},
            {"source_asset_id": sources[2].id, "source_sha256": sources[2].sha256,
             "status": "rejected", "offset_us": 0, "correlation": 0.1,
             "peak_margin": 0.01, "overlap_seconds": 20.0,
             "reason": "correlation_below_threshold"},
        ],
        "synced_camera_count": 1, "rejected_camera_count": 1,
        "engine": {
            "algorithm": "test", "algorithm_version": "1", "sample_rate": 16000,
            "envelope_hz": 100, "max_offset_seconds_requested": 2.0,
            "max_offset_seconds_effective": 2.0, "analysis_seconds_requested": 20.0,
            "correlation_threshold": 0.55, "peak_margin_threshold": 0.05,
            "minimum_overlap_seconds": 5.0, "ffmpeg_path": "ffmpeg", "ffprobe_path": "ffprobe",
        },
    })
    sync = _artifact(
        domain, project.id, "multicam_sync", tmp_path / "sync.json",
        sync_document.model_dump(mode="json"),
    )
    return config, domain, project, sources, sync


def _patch_stages(monkeypatch: pytest.MonkeyPatch, domain: DomainStore, tmp_path: Path):
    calls: list[tuple[str, str]] = []

    def scene_run(_self, *, source_asset, **_kwargs):
        calls.append(("scene", source_asset.id))
        artifact = _artifact(
            domain, source_asset.project_id, "scene_index", tmp_path / "scene.json", {},
        )
        return {"scene_index_artifact_id": artifact.id}

    def face_run(_self, *, source_asset, scene_index_artifact, **_kwargs):
        calls.append(("face", source_asset.id))
        assert scene_index_artifact.stage == "scene_index"
        artifact = _artifact(
            domain, source_asset.project_id, "face_index", tmp_path / "face.json", {},
        )
        return {"face_index_artifact_id": artifact.id}

    def quality_run(
        _self, *, source_asset, scene_index_artifact, face_index_artifact, **_kwargs,
    ):
        calls.append(("quality", source_asset.id))
        assert scene_index_artifact.stage == "scene_index"
        assert face_index_artifact.stage == "face_index"
        artifact = _artifact(
            domain, source_asset.project_id, "visual_quality_index", tmp_path / "quality.json", {},
        )
        return {"visual_quality_artifact_id": artifact.id}

    monkeypatch.setattr("cortex.analyze.multicam_visual_service.SceneIndexService.run", scene_run)
    monkeypatch.setattr("cortex.analyze.multicam_visual_service.FaceIndexService.run", face_run)
    monkeypatch.setattr("cortex.analyze.multicam_visual_service.VisualQualityService.run", quality_run)
    return calls


def test_iso_visual_schema_rejects_incomplete_indexed_entry() -> None:
    with pytest.raises(ValidationError, match="requires all visual artifacts"):
        IsoVisualIndex(
            source_asset_id="source", source_sha256="sha", offset_us=0,
            status="indexed", scene_index_artifact_id="scene",
        )


def test_multicam_visual_orchestrates_synced_only_persists_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, domain, project, sources, sync = _fixture(tmp_path)
    calls = _patch_stages(monkeypatch, domain, tmp_path)
    service = MulticamVisualIndexService(config, domain)
    first = service.run(
        multicam_sync_artifact=sync, progress_cb=lambda *_args: None,
        should_cancel=lambda: False,
    )
    document = MulticamVisualIndexDocument.model_validate_json(
        Path(first["multicam_visual_path"]).read_text(encoding="utf-8")
    )
    second = service.run(
        multicam_sync_artifact=sync, progress_cb=lambda *_args: None,
        should_cancel=lambda: False,
    )

    assert calls == [(stage, sources[1].id) for stage in ("scene", "face", "quality")]
    assert [camera.status for camera in document.cameras] == ["indexed", "rejected"]
    assert document.cameras[0].offset_us == 800_000
    assert document.indexed_camera_count == 1 and document.rejected_camera_count == 1
    assert second["cached"] is True
    assert len(domain.list_stage_artifacts(project.id, "multicam_visual_index")) == 1


def test_multicam_visual_rejects_missing_synced_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, domain, _project, sources, sync = _fixture(tmp_path)
    (tmp_path / "synced.mp4").unlink()
    _patch_stages(monkeypatch, domain, tmp_path)

    with pytest.raises(MulticamVisualPreconditionError, match="não corresponde ao sync"):
        MulticamVisualIndexService(config, domain).run(
            multicam_sync_artifact=sync, progress_cb=lambda *_args: None,
            should_cancel=lambda: False,
        )
    assert sources[1].stored_path == "synced.mp4"


def test_multicam_visual_worker_persists_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, domain, project, _sources, sync = _fixture(tmp_path)
    _patch_stages(monkeypatch, domain, tmp_path)
    jobs = JobStore(config.paths.database)
    queued = jobs.create(JobCreate(
        type=JobType.MULTICAM_VISUAL_INDEX, project_id=project.id,
        payload={"multicam_sync_artifact_id": sync.id},
    ))

    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued.id)
    assert final.status == JobStatus.SUCCEEDED, final.error
    assert final.result is not None
    artifact = domain.get_stage_artifact(final.result["multicam_visual_artifact_id"])
    assert artifact.stage == "multicam_visual_index" and Path(artifact.path).exists()


def test_multicam_visual_routes_are_exposed_in_openapi(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    paths = create_app(config).openapi()["paths"]
    collection = f"{config.app.api_prefix}/projects/{{project_id}}/multicam-visual"

    assert "post" in paths[collection]
    assert "get" in paths[collection + "/{artifact_id}"]
