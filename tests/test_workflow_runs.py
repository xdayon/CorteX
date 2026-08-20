from __future__ import annotations

import hashlib
from pathlib import Path

from asgi_client import ASGITestClient as TestClient

from cortex.analyze.schemas import LoudnessMetrics, VadInterval
from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind
from cortex.domain.store import DomainStore
from cortex.ingest.ffprobe import probe_media
from cortex.jobs import JobStore
from cortex.suggest.provider import SuggestionProviderResult
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
    def generate(self, prompt, schema, *, should_cancel):
        del prompt, schema
        assert not should_cancel()
        clip = {
            "rank": 1,
            "title": "Ideia principal",
            "headline": "Uma ideia que vale o corte",
            "start_second": 0,
            "end_second": 40,
            "estimated_duration": 40,
            "primary_speaker": "Pessoa principal",
            "topic": "Teste",
            "pacing": "balanced",
            "hook": {"start_second": 0, "end_second": 5, "summary": "Gancho", "evidence": "Gancho"},
            "context": {"start_second": 5, "end_second": 30, "summary": "Contexto", "evidence": "Contexto"},
            "payoff": {"start_second": 30, "end_second": 40, "summary": "Conclusão", "evidence": "Conclusão"},
            "approximate_edl": [{
                "order": 0, "source_start": 0, "source_end": 40, "purpose": "development",
                "preferred_visual": "auto", "audio_mode": "source", "transition_in": "hard_cut",
                "mask_jump_with": "none", "confidence": 0.9, "notes": "Trecho contínuo",
            }],
            "scores": {
                "spoken_hook": 4, "standalone_clarity": 4, "emotion": 3, "quotability": 4,
                "payoff": 4, "compression_safety": 4, "audience_relevance": 4, "total": 27,
            },
            "reasoning": "Trecho autocontido.",
            "warnings": [],
        }
        return SuggestionProviderResult(
            document={"schema_version": "1.0", "selection_notes": "Teste", "clips": [clip]},
            provenance={"provider": "codex_cli", "model": "fixture"},
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
        json={"source_asset_id": source.id, "count": 10, "minimum_seconds": 40, "maximum_seconds": 120},
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    jobs = JobStore(config.paths.database)

    assert process_next(config, jobs, domain, FakeTranscriptionEngine(duration_seconds=40), _SuggestionProvider())
    assert domain.get_workflow_run(run_id).stage == "analyze"
    assert process_next(config, jobs, domain, FakeTranscriptionEngine(), _SuggestionProvider())
    assert domain.get_workflow_run(run_id).stage == "suggest"
    assert process_next(config, jobs, domain, FakeTranscriptionEngine(), _SuggestionProvider())

    run = domain.get_workflow_run(run_id)
    assert run.status == "ready_for_review"
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
