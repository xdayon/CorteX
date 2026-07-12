from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from cortex.analyze.multicam_sync import estimate_audio_offset
from cortex.analyze.multicam_sync_schemas import MulticamSyncDocument
from cortex.analyze.multicam_sync_service import MulticamSyncPreconditionError, MulticamSyncService
from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import JobCreate, JobStatus, JobType
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine

SAMPLE_RATE = 16_000


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


def _pattern(seed: int, seconds: float = 12.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    blocks = rng.uniform(0.05, 0.9, round(seconds * 10))
    envelope = np.repeat(blocks, SAMPLE_RATE // 10)
    time = np.arange(len(envelope)) / SAMPLE_RATE
    carrier = np.sin(2 * np.pi * 347.0 * time) + 0.35 * np.sin(2 * np.pi * 613.0 * time)
    return np.clip(envelope * carrier * 0.65, -1.0, 1.0).astype(np.float32)


def _write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = np.round(np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


def test_fft_offset_estimator_has_explicit_positive_alternate_delay() -> None:
    rng = np.random.default_rng(7)
    primary = rng.normal(size=1_000)
    alternate = np.r_[np.zeros(80), primary]

    offset, correlation, margin, overlap = estimate_audio_offset(
        primary, alternate, envelope_hz=100,
        max_offset_seconds=2.0, minimum_overlap_seconds=5.0,
    )
    assert offset == pytest.approx(0.8)
    assert correlation == pytest.approx(1.0)
    assert margin > 0.5 and overlap == pytest.approx(10.0)


def _fixture(tmp_path: Path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Multicam sync")
    primary_audio = _pattern(11)
    delay_samples = round(0.8 * SAMPLE_RATE)
    synced_audio = np.r_[np.zeros(delay_samples, dtype=np.float32), primary_audio]
    unrelated_audio = _pattern(93)
    sources = []
    for name, audio in (
        ("primary.wav", primary_audio),
        ("synced.wav", synced_audio),
        ("unrelated.wav", unrelated_audio),
    ):
        path = tmp_path / name
        _write_wav(path, audio)
        sources.append(domain.create_source_asset(SourceAsset(
            project_id=project.id, kind=SourceKind.UPLOAD, original_filename=name,
            stored_path=name, sha256=f"sha-{name}", size_bytes=path.stat().st_size,
        )))
    return config, domain, project, sources[0], sources[1], sources[2]


def test_multicam_service_syncs_matching_audio_rejects_unrelated_and_caches(tmp_path: Path) -> None:
    config, domain, project, primary, synced, unrelated = _fixture(tmp_path)
    service = MulticamSyncService(config, domain)
    first = service.run(
        primary_source=primary, alternate_sources=[synced, unrelated],
        max_offset_seconds=2.0, analysis_seconds=12.0,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    document = MulticamSyncDocument.model_validate_json(
        Path(first["multicam_sync_path"]).read_text(encoding="utf-8")
    )
    second = service.run(
        primary_source=primary, alternate_sources=[synced, unrelated],
        max_offset_seconds=2.0, analysis_seconds=12.0,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )

    assert document.cameras[0].status == "synced"
    assert document.cameras[0].offset_us == pytest.approx(800_000, abs=20_000)
    assert document.cameras[1].status == "rejected"
    assert document.cameras[1].reason is not None
    assert document.synced_camera_count == 1 and document.rejected_camera_count == 1
    assert second["cached"] is True
    assert len(domain.list_stage_artifacts(project.id, "multicam_sync")) == 1


def test_multicam_service_rejects_duplicate_alternates(tmp_path: Path) -> None:
    config, domain, _project, primary, synced, _unrelated = _fixture(tmp_path)

    with pytest.raises(MulticamSyncPreconditionError, match="duplicadas"):
        MulticamSyncService(config, domain).run(
            primary_source=primary, alternate_sources=[synced, synced],
            max_offset_seconds=2.0, analysis_seconds=12.0,
            progress_cb=lambda *_args: None, should_cancel=lambda: False,
        )


def test_multicam_worker_persists_artifact(tmp_path: Path) -> None:
    config, domain, project, primary, synced, _unrelated = _fixture(tmp_path)
    jobs = JobStore(config.paths.database)
    queued = jobs.create(JobCreate(
        type=JobType.MULTICAM_SYNC, project_id=project.id,
        payload={
            "primary_source_asset_id": primary.id,
            "alternate_source_asset_ids": [synced.id],
            "max_offset_seconds": 2.0,
            "analysis_seconds": 12.0,
        },
    ))

    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued.id)
    assert final.status == JobStatus.SUCCEEDED, final.error
    assert final.result is not None
    artifact = domain.get_stage_artifact(final.result["multicam_sync_artifact_id"])
    assert artifact.stage == "multicam_sync" and Path(artifact.path).exists()


def test_multicam_routes_are_exposed_in_openapi(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    paths = create_app(config).openapi()["paths"]
    collection = f"{config.app.api_prefix}/projects/{{project_id}}/multicam-sync"

    assert "post" in paths[collection]
    assert "get" in paths[collection + "/{artifact_id}"]
