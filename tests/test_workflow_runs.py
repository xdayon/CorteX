from __future__ import annotations

import hashlib
from pathlib import Path


from asgi_client import ASGITestClient as TestClient

from cortex.analyze.schemas import LoudnessMetrics, VadInterval
from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind, WorkflowRun
from cortex.domain.store import DomainStore
from cortex.ingest.ffprobe import probe_media
from cortex.jobs import JobStore
from cortex.suggest.provider import SuggestionProviderResult
from cortex.transcribe.schemas import TranscriptSegment
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


class _SuggestionProvider:
    def generate(self, prompt, schema, *, should_cancel, system_context=None):
        del prompt, schema, system_context
        assert not should_cancel()
        clip = {
            "title": "Ideia principal",
            "headlines": ["Uma ideia que vale o corte"],
            "pieces": [{"from_id": 0, "to_id": 0}],
            "emphasis": [],
            "score": 8,
            "reason": "Trecho autocontido.",
            "teaser_ids": [],
            "warnings": [],
        }
        return SuggestionProviderResult(
            document={"selection_notes": "Teste", "clips": [clip]},
            provenance={"provider": "claude_code_cli", "model": "fixture"},
        )


def test_one_action_persists_and_advances_to_review(
    tmp_path: Path, short_mp4: Path, monkeypatch,
) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Episódio")
    stored = config.paths.projects_dir / project.id / "source" / "episode.mp4"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(short_mp4.read_bytes())
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id,
        kind=SourceKind.UPLOAD,
        original_filename="episode.mp4",
        stored_path=str(stored.relative_to(config.paths.data_dir)),
        sha256=hashlib.sha256(stored.read_bytes()).hexdigest(),
        size_bytes=stored.stat().st_size,
        probe=probe_media(config.render.ffprobe, stored),
    ))
    interval = VadInterval(start=0.1, end=1.8, duration=1.7, start_sample=1600, end_sample=28800)
    monkeypatch.setattr("cortex.analyze.service.detect_speech", lambda _audio, _rate: ([interval], "test-vad"))
    monkeypatch.setattr(
        "cortex.analyze.service.measure_loudness",
        lambda _ffmpeg, _path: LoudnessMetrics(
            integrated_lufs=-20, loudness_range_lu=2, true_peak_dbfs=-3,
            threshold_lufs=-30, engine="test-loudness",
        ),
    )
    client = TestClient(create_app(config))

    response = client.post(
        f"/api/v1/projects/{project.id}/runs",
        json={"source_asset_id": source.id, "count": 10, "minimum_seconds": 40, "maximum_seconds": 120,
              "hotwords": [" Teo  Nanacacho ", "", "Teo Nanacacho"]},
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    jobs = JobStore(config.paths.database)

    engine = FakeTranscriptionEngine(duration_seconds=40, segments=[
        TranscriptSegment(id=0, start=0.0, end=40.0, text="ola mundo", avg_logprob=-0.1, no_speech_prob=0.0, words=[]),
    ])
    assert process_next(config, jobs, domain, engine, _SuggestionProvider())
    assert engine.calls[0]["hotwords"] == [*config.transcription.hotwords, "Teo Nanacacho"]
    assert domain.get_workflow_run(run_id).stage == "analyze"
    assert process_next(config, jobs, domain, FakeTranscriptionEngine(), _SuggestionProvider())
    assert domain.get_workflow_run(run_id).stage == "suggest"
    assert process_next(config, jobs, domain, FakeTranscriptionEngine(), _SuggestionProvider())

    run = domain.get_workflow_run(run_id)
    assert run.status == "ready_for_review", run.error or run.message
    assert set(run.artifacts) == {"transcript", "analysis", "suggestion"}
    assert client.get(f"/api/v1/projects/{project.id}/runs/{run_id}").status_code == 200
    assert client.get(f"/api/v1/projects/{project.id}/sources").json()[0]["id"] == source.id


def test_run_rejects_inverted_duration(tmp_path: Path) -> None:
    config = _config(tmp_path)
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Episódio")
    client = TestClient(create_app(config))

    response = client.post(
        f"/api/v1/projects/{project.id}/runs",
        json={"source_asset_id": "missing", "minimum_seconds": 120, "maximum_seconds": 40},
    )
    assert response.status_code == 422


def test_legacy_run_with_identity_fields_stays_readable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Episódio")
    source = domain.create_source_asset(SourceAsset(
        id="source", project_id=project.id, kind=SourceKind.UPLOAD,
        stored_path="episode.mp4", sha256="a" * 64, size_bytes=1,
    ))
    run = domain.create_workflow_run(WorkflowRun(
        project_id=project.id, source_asset_id=source.id, status="ready_for_review",
        artifacts={"face_index": "x", "identity_index": "y"},
        subject_identity_id="old-subject", interviewer_identity_id="me",
    ))
    client = TestClient(create_app(config))
    response = client.get(f"/api/v1/projects/{project.id}/runs/{run.id}")
    assert response.status_code == 200
    assert response.json()["interviewer_identity_id"] == "me"
    assert client.request(
        "PUT", f"/api/v1/projects/{project.id}/runs/{run.id}/identity", json={},
    ).status_code in (404, 405)
