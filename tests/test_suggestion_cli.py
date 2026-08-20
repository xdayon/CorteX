from __future__ import annotations

import json
import tempfile
from pathlib import Path

from asgi_client import ASGITestClient as TestClient
from jsonschema import Draft202012Validator

from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import JobCreate, JobStatus, JobType
from cortex.suggest.provider import (
    CodexCliProvider,
    SuggestionProviderResult,
    _codex_error_detail,
    build_suggestion_provider,
)
from cortex.suggest.service import SuggestionService
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIP_SELECTION_SCHEMA = json.loads(
    (_PROJECT_ROOT / "prompts/clip_selection.schema.json").read_text(encoding="utf-8")
)


def _valid_clip(**overrides) -> dict:
    clip = {
        "rank": 1,
        "title": "Um corte de teste",
        "headline": "Headline de teste",
        "start_second": 0,
        "end_second": 30,
        "estimated_duration": 30,
        "primary_speaker": "Convidado",
        "topic": "Tópico de teste",
        "pacing": "balanced",
        "hook": {
            "start_second": 0, "end_second": 5,
            "summary": "Abertura do corte.", "evidence": "Frase de abertura.",
        },
        "context": {
            "start_second": 5, "end_second": 20,
            "summary": "Desenvolvimento do corte.", "evidence": "Trecho intermediário.",
        },
        "payoff": {
            "start_second": 20, "end_second": 30,
            "summary": "Conclusão do corte.", "evidence": "Frase final.",
        },
        "approximate_edl": [
            {
                "order": 0, "source_start": 0, "source_end": 30, "purpose": "hook",
                "preferred_visual": "primary_speaker", "audio_mode": "source",
                "transition_in": "hard_cut", "mask_jump_with": "none",
                "confidence": 0.9, "notes": "Segmento único.",
            },
        ],
        "scores": {
            "spoken_hook": 4, "standalone_clarity": 4, "emotion": 3, "quotability": 4,
            "payoff": 4, "compression_safety": 4, "audience_relevance": 4, "total": 27,
        },
        "reasoning": "Corte autocontido com abertura, desenvolvimento e conclusão claros.",
        "warnings": [],
    }
    clip.update(overrides)
    return clip


class FakeSuggestionProvider:
    def __init__(self, document: dict | None = None) -> None:
        self.document = document or {
            "schema_version": "1.0",
            "selection_notes": "Corte de teste selecionado.",
            "clips": [_valid_clip()],
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
        assert len(persisted["selection"]["clips"]) == 1


def test_provider_factory_returns_codex_directly():
    provider = build_suggestion_provider(load_config().ai, cwd=_PROJECT_ROOT)

    assert isinstance(provider, CodexCliProvider)


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


def test_suggestion_service_rejects_empty_clip_list():
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript(domain, tmp_dir)
        analysis = _analysis(domain, tmp_dir, transcript)
        provider = FakeSuggestionProvider(document={
            "schema_version": "1.0",
            "selection_notes": "Sem cortes.",
            "clips": [],
        })
        service = SuggestionService(config, domain, provider)

        try:
            service.run(
                transcript_artifact=transcript,
                analysis_artifact=analysis,
                brief={"count": 3, "minimum_seconds": 15, "maximum_seconds": 60},
                progress_cb=lambda _progress, _message: None,
                should_cancel=lambda: False,
            )
        except ValueError as exc:
            assert "clips" in str(exc)
        else:
            raise AssertionError("resposta com 0 clips deveria ser rejeitada")


def test_suggestion_service_rejects_clip_below_minimum_duration():
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript(domain, tmp_dir)
        analysis = _analysis(domain, tmp_dir, transcript)
        provider = FakeSuggestionProvider(document={
            "schema_version": "1.0",
            "selection_notes": "Corte curto demais.",
            "clips": [_valid_clip(end_second=14, estimated_duration=14)],
        })
        service = SuggestionService(config, domain, provider)

        try:
            service.run(
                transcript_artifact=transcript,
                analysis_artifact=analysis,
                brief={"count": 3, "minimum_seconds": 15, "maximum_seconds": 60},
                progress_cb=lambda _progress, _message: None,
                should_cancel=lambda: False,
            )
        except ValueError as exc:
            assert "estimated_duration" in str(exc)
        else:
            raise AssertionError("clip com estimated_duration 14 deveria ser rejeitado")


def test_clip_selection_schema_boundary_validation_directly():
    minimal_document = {
        "schema_version": "1.0",
        "selection_notes": "notas",
        "clips": [],
    }
    errors = sorted(Draft202012Validator(CLIP_SELECTION_SCHEMA).iter_errors(minimal_document), key=str)
    assert errors, "clips vazio deveria falhar com minItems: 1"

    document_with_short_clip = {
        "schema_version": "1.0",
        "selection_notes": "notas",
        "clips": [_valid_clip(estimated_duration=14)],
    }
    errors = sorted(
        Draft202012Validator(CLIP_SELECTION_SCHEMA).iter_errors(document_with_short_clip), key=str
    )
    assert errors, "estimated_duration 14 deveria falhar com minimum: 15"

    document_with_valid_clip = {
        "schema_version": "1.0",
        "selection_notes": "notas",
        "clips": [_valid_clip(estimated_duration=15, end_second=15)],
    }
    errors = list(Draft202012Validator(CLIP_SELECTION_SCHEMA).iter_errors(document_with_valid_clip))
    assert errors == []


def test_suggestion_request_boundary_counts_and_durations():
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript(domain, tmp_dir)
        analysis = _analysis(domain, tmp_dir, transcript)
        client = TestClient(create_app(config))

        def post(**overrides):
            payload = {
                "transcript_artifact_id": transcript.id,
                "analysis_artifact_id": analysis.id,
                "count": 2,
                "minimum_seconds": 15,
                "maximum_seconds": 60,
            }
            payload.update(overrides)
            return client.post(f"/api/v1/projects/{transcript.project_id}/suggest", json=payload)

        assert post(count=0).status_code == 422
        assert post(count=1).status_code == 201
        assert post(count=25).status_code == 201
        assert post(count=26).status_code == 422
        assert post(minimum_seconds=14).status_code == 422
        assert post(minimum_seconds=15).status_code == 201
        assert post(maximum_seconds=180).status_code == 201
        assert post(maximum_seconds=181).status_code == 422
        assert post(minimum_seconds=60, maximum_seconds=30).status_code == 422


def test_clip_config_maximum_seconds_boundary():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.yaml"

        path.write_text("clips:\n  minimum_seconds: 15\n  maximum_seconds: 15\n", encoding="utf-8")
        config = load_config(path)
        assert config.clips.maximum_seconds == 15

        path.write_text("clips:\n  minimum_seconds: 14\n  maximum_seconds: 14\n", encoding="utf-8")
        try:
            load_config(path)
        except ValueError:
            pass
        else:
            raise AssertionError("maximum_seconds 14 deveria ser rejeitado (ge=15)")


def test_suggestion_request_rejects_provider_selection():
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
                "count": 1,
                "minimum_seconds": 15,
                "maximum_seconds": 60,
                "provider": "local_heuristic",
            },
        )

        assert response.status_code == 422
