from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import JobCreate, JobStatus, JobType
from cortex.suggest.provider import (
    FallbackSuggestionProvider,
    SuggestionProviderCancelled,
    SuggestionProviderError,
    SuggestionProviderResult,
    _codex_error_detail,
)
from cortex.suggest.service import SuggestionService
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine


class FakeSuggestionProvider:
    def __init__(self, document: dict | None = None) -> None:
        self.document = document or {
            "schema_version": "1.0",
            "selection_notes": "Sem cortes suficientes no fixture.",
            "clips": [],
        }
        self.calls: list[tuple[str, dict]] = []

    def generate(self, prompt, schema, *, should_cancel):
        assert not should_cancel()
        self.calls.append((prompt, schema))
        return SuggestionProviderResult(
            document=self.document,
            provenance={
                "provider": "fake",
                "model": "fixture",
                "binary_version": "test",
            },
        )


class FailingSuggestionProvider:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def generate(self, prompt, schema, *, should_cancel):
        self.calls += 1
        raise self.error


def _config(tmp_dir: Path) -> CortexConfig:
    base = load_config()
    return base.model_copy(update={
        "paths": base.paths.model_copy(update={
            "data_dir": tmp_dir,
            "projects_dir": tmp_dir / "projects",
            "cache_dir": tmp_dir / "cache",
            "output_dir": tmp_dir / "output",
            "database": tmp_dir / "cortex.sqlite3",
        }),
    })


def _transcript(domain: DomainStore, tmp_dir: Path) -> TranscriptArtifact:
    project = domain.create_project("Seleção CLI")
    transcript_path = tmp_dir / "transcript.json"
    transcript_path.write_text(json.dumps({
        "schema_version": 1,
        "language": "pt",
        "duration_seconds": 90,
        "segments": [
            {"start": 0, "end": 20, "text": "Uma ideia completa para o teste."},
            {"start": 20, "end": 60, "text": "Ela possui contexto e conclusão."},
        ],
    }), encoding="utf-8")
    return domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project.id,
        source_asset_id="source-fixture",
        path=str(transcript_path),
        audio_sha256="a" * 64,
        engine="fake",
        model="fake",
        device="cpu",
        compute_type="int8",
        language="pt",
        vad=True,
        batch_size=1,
        duration_seconds=90,
    ))


def _analysis(domain: DomainStore, tmp_dir: Path, transcript: TranscriptArtifact) -> StageArtifact:
    analysis_path = tmp_dir / "analysis.json"
    analysis_path.write_text(json.dumps({
        "overall_speech_ratio": 0.72,
        "loudness": {"integrated_lufs": -20.0, "true_peak_dbfs": -3.0},
        "room_tone": [],
        "pauses": [{"start": 10.0, "end": 10.8, "duration": 0.8}],
    }), encoding="utf-8")
    return domain.create_stage_artifact(StageArtifact(
        project_id=transcript.project_id,
        stage="analysis",
        path=str(analysis_path),
        input_hash="b" * 64,
        metadata={"transcript_artifact_id": transcript.id},
    ))


def test_suggestion_service_validates_persists_and_reuses_cache():
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript(domain, tmp_dir)
        analysis = _analysis(domain, tmp_dir, transcript)
        provider = FakeSuggestionProvider()
        service = SuggestionService(config, domain, provider)

        first = service.run(
            transcript_artifact=transcript,
            analysis_artifact=analysis,
            brief={"count": 3, "minimum_seconds": 15, "maximum_seconds": 60},
            progress_cb=lambda _progress, _message: None,
            should_cancel=lambda: False,
        )
        second = service.run(
            transcript_artifact=transcript,
            analysis_artifact=analysis,
            brief={"count": 3, "minimum_seconds": 15, "maximum_seconds": 60},
            progress_cb=lambda _progress, _message: None,
            should_cancel=lambda: False,
        )

        assert first["cached"] is False
        assert second["cached"] is True
        assert first["suggestion_artifact_id"] == second["suggestion_artifact_id"]
        assert len(provider.calls) == 1
        persisted = json.loads(Path(first["suggestion_path"]).read_text(encoding="utf-8"))
        assert persisted["provenance"]["provider"] == "fake"
        assert persisted["selection"]["clips"] == []


def test_provider_prefers_codex_without_calling_claude_fallback():
    codex = FakeSuggestionProvider()
    claude = FakeSuggestionProvider()
    provider = FallbackSuggestionProvider(
        codex, claude, primary_name="codex_cli", fallback_name="claude_cli"
    )
    schema = {
        "type": "object",
        "required": ["schema_version", "selection_notes", "clips"],
    }

    result = provider.generate("prompt", schema, should_cancel=lambda: False)

    assert len(codex.calls) == 1
    assert claude.calls == []
    assert result.provenance["requested_provider"] == "codex_cli"
    assert result.provenance["effective_provider"] == "fake"
    assert result.provenance["fallback_used"] is False


def test_provider_falls_back_to_claude_and_records_codex_failure():
    codex = FailingSuggestionProvider(SuggestionProviderError("limite do Codex"))
    claude = FakeSuggestionProvider()
    provider = FallbackSuggestionProvider(
        codex, claude, primary_name="codex_cli", fallback_name="claude_cli"
    )
    schema = {
        "type": "object",
        "required": ["schema_version", "selection_notes", "clips"],
    }

    result = provider.generate("prompt", schema, should_cancel=lambda: False)

    assert codex.calls == 1
    assert len(claude.calls) == 1
    assert result.provenance["requested_provider"] == "codex_cli"
    assert result.provenance["effective_provider"] == "fake"
    assert result.provenance["fallback_used"] is True
    assert "limite do Codex" in result.provenance["fallback_reason"]


def test_provider_cancellation_never_invokes_fallback():
    codex = FailingSuggestionProvider(SuggestionProviderCancelled("cancelado"))
    claude = FakeSuggestionProvider()
    provider = FallbackSuggestionProvider(
        codex, claude, primary_name="codex_cli", fallback_name="claude_cli"
    )

    try:
        provider.generate("prompt", {"type": "object"}, should_cancel=lambda: True)
    except SuggestionProviderCancelled:
        pass
    else:
        raise AssertionError("cancelamento deveria ser propagado")

    assert claude.calls == []


def test_codex_error_detail_never_persists_echoed_transcript():
    stderr = '''user
[0.000-4.000] conteúdo privado da transcrição
ERROR: {"error":{"code":"rate_limit","message":"Limite temporário atingido"}}
'''

    detail = _codex_error_detail(stderr)

    assert detail == "Limite temporário atingido"
    assert "conteúdo privado" not in detail


def test_suggestion_job_runs_through_worker_with_injected_provider():
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        jobs = JobStore(config.paths.database)
        transcript = _transcript(domain, tmp_dir)
        analysis = _analysis(domain, tmp_dir, transcript)
        job = jobs.create(JobCreate(
            type=JobType.SUGGESTION,
            project_id=transcript.project_id,
            payload={
                "transcript_artifact_id": transcript.id,
                "analysis_artifact_id": analysis.id,
                "brief": {"count": 2},
            },
        ))

        assert process_next(
            config, jobs, domain, FakeTranscriptionEngine(), FakeSuggestionProvider()
        )
        final = jobs.get(job.id)
        assert final.status == JobStatus.SUCCEEDED
        assert final.result["selection"]["schema_version"] == "1.0"


def test_suggestion_endpoint_checks_transcript_ownership_and_queues_job():
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript(domain, tmp_dir)
        analysis = _analysis(domain, tmp_dir, transcript)
        client = TestClient(create_app(config))

        response = client.post(
            f"/api/v1/projects/{transcript.project_id}/suggest",
            json={
                "transcript_artifact_id": transcript.id,
                "analysis_artifact_id": analysis.id,
                "count": 2,
                "minimum_seconds": 30,
                "maximum_seconds": 90,
                "topic": "ideia do teste",
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["type"] == "suggestion"
        assert body["payload"]["brief"]["count"] == 2
