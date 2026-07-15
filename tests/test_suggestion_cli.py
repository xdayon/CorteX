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
    FallbackSuggestionProvider,
    LocalHeuristicProvider,
    SuggestionProviderCancelled,
    SuggestionProviderError,
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
        assert len(persisted["selection"]["clips"]) == 1


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


def _heuristic_prompt(segments: list[dict], brief: dict, notable_pauses: list[dict] | None = None) -> str:
    transcript_lines = "\n".join(
        f"[{seg['start']:.3f}-{seg['end']:.3f}] {seg['text']}" for seg in segments
    )
    request_payload = {
        "brief": brief,
        "transcript": transcript_lines,
        "transcript_metadata": {"duration_seconds": segments[-1]["end"], "language": "pt"},
        "local_audio_analysis": {
            "overall_speech_ratio": 0.7,
            "loudness": {"integrated_lufs": -20.0},
            "notable_pauses": notable_pauses or [],
        },
    }
    return (
        "## Modelo de prompt de teste\n\n"
        "## Pedido atual e transcrição\n"
        f"{json.dumps(request_payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def _heuristic_segments() -> list[dict]:
    return [
        {"start": 0.0, "end": 10.0, "text": "Essa é a primeira ideia completa do episódio."},
        {"start": 10.0, "end": 22.0, "text": "Aqui entra o desenvolvimento com mais contexto e detalhes."},
        {"start": 22.8, "end": 34.0, "text": "E finalmente chegamos à conclusão marcante desse trecho."},
        {"start": 34.0, "end": 48.0, "text": "Um segundo bloco de assunto totalmente diferente começa aqui."},
        {"start": 48.0, "end": 60.0, "text": "E esse segundo bloco também termina com uma conclusão clara."},
    ]


def test_local_heuristic_provider_produces_schema_valid_document_within_bounds():
    brief = {"count": 2, "minimum_seconds": 15, "maximum_seconds": 40}
    notable_pauses = [{"start": 22.0, "end": 22.8, "duration": 0.8}]
    prompt = _heuristic_prompt(_heuristic_segments(), brief, notable_pauses)
    schema = CLIP_SELECTION_SCHEMA
    provider = LocalHeuristicProvider()

    result = provider.generate(prompt, schema, should_cancel=lambda: False)

    errors = sorted(Draft202012Validator(schema).iter_errors(result.document), key=str)
    assert errors == []
    assert result.provenance["provider"] == "local_heuristic"
    assert result.provenance["mode"] == "heuristic"
    assert result.provenance["llm_used"] is False
    clips = result.document["clips"]
    assert 1 <= len(clips) <= brief["count"]
    segment_starts = {seg["start"] for seg in _heuristic_segments()}
    segment_ends = {seg["end"] for seg in _heuristic_segments()}
    for clip in clips:
        assert brief["minimum_seconds"] <= clip["estimated_duration"] <= brief["maximum_seconds"]
        assert clip["start_second"] in segment_starts
        assert clip["end_second"] in segment_ends
        assert "sem LLM" in clip["reasoning"]


def test_local_heuristic_provider_is_deterministic():
    brief = {"count": 3, "minimum_seconds": 15, "maximum_seconds": 40}
    prompt = _heuristic_prompt(_heuristic_segments(), brief)
    schema = CLIP_SELECTION_SCHEMA

    first = LocalHeuristicProvider().generate(prompt, schema, should_cancel=lambda: False)
    second = LocalHeuristicProvider().generate(prompt, schema, should_cancel=lambda: False)

    assert first.document == second.document


def test_local_heuristic_provider_never_cuts_mid_segment():
    segments = _heuristic_segments()
    brief = {"count": 5, "minimum_seconds": 10, "maximum_seconds": 15}
    prompt = _heuristic_prompt(segments, brief)

    result = LocalHeuristicProvider().generate(prompt, CLIP_SELECTION_SCHEMA, should_cancel=lambda: False)

    valid_starts = {seg["start"] for seg in segments}
    valid_ends = {seg["end"] for seg in segments}
    for clip in result.document["clips"]:
        assert clip["start_second"] in valid_starts
        assert clip["end_second"] in valid_ends


def test_without_opt_in_heuristic_is_never_used_by_default_chain():
    config = load_config().ai.model_copy(update={
        "codex_binary": "cortex-nonexistent-cli-codex",
        "claude_binary": "cortex-nonexistent-cli-claude",
    })
    assert config.enable_local_heuristic_fallback is False
    provider = build_suggestion_provider(config, cwd=_PROJECT_ROOT)

    try:
        provider.generate("prompt", CLIP_SELECTION_SCHEMA, should_cancel=lambda: False)
    except SuggestionProviderError as exc:
        assert "local_heuristic" not in str(exc)
    else:
        raise AssertionError("cadeia padrão sem opt-in não deveria suceder com CLIs inexistentes")


def test_request_opt_in_marks_provenance_as_local_heuristic():
    codex = FailingSuggestionProvider(SuggestionProviderError("codex indisponível"))
    provider = FallbackSuggestionProvider(
        LocalHeuristicProvider(), None, primary_name="local_heuristic", fallback_name=None
    )
    brief = {"count": 1, "minimum_seconds": 15, "maximum_seconds": 40}
    prompt = _heuristic_prompt(_heuristic_segments(), brief)

    result = provider.generate(prompt, CLIP_SELECTION_SCHEMA, should_cancel=lambda: False)

    assert codex.calls == 0
    assert result.provenance["requested_provider"] == "local_heuristic"
    assert result.provenance["effective_provider"] == "local_heuristic"
    assert result.provenance["mode"] == "heuristic"
    assert result.provenance["fallback_used"] is False


def test_configured_fallback_chain_uses_local_heuristic_after_primary_and_secondary_fail():
    codex = FailingSuggestionProvider(SuggestionProviderError("codex indisponível"))
    claude = FailingSuggestionProvider(SuggestionProviderError("claude indisponível"))
    heuristic = LocalHeuristicProvider()
    provider = FallbackSuggestionProvider(
        codex,
        claude,
        primary_name="codex_cli",
        fallback_name="claude_cli",
        heuristic=heuristic,
        heuristic_name="local_heuristic",
    )
    brief = {"count": 1, "minimum_seconds": 15, "maximum_seconds": 40}
    prompt = _heuristic_prompt(_heuristic_segments(), brief)

    result = provider.generate(prompt, CLIP_SELECTION_SCHEMA, should_cancel=lambda: False)

    assert codex.calls == 1
    assert claude.calls == 1
    assert result.provenance["requested_provider"] == "codex_cli"
    assert result.provenance["effective_provider"] == "local_heuristic"
    assert result.provenance["mode"] == "heuristic"
    assert result.provenance["fallback_used"] is True
    assert "claude indisponível" in result.provenance["fallback_reason"]


def test_suggestion_job_with_request_provider_opt_in_uses_local_heuristic():
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
                "brief": {"count": 1, "minimum_seconds": 15, "maximum_seconds": 60},
                "provider": "local_heuristic",
            },
        ))

        assert process_next(config, jobs, domain, FakeTranscriptionEngine(), None)
        final = jobs.get(job.id)

        assert final.status == JobStatus.SUCCEEDED
        provenance = final.result["provenance"]
        assert provenance["provider"] == "local_heuristic"
        assert provenance["requested_provider"] == "local_heuristic"
        assert provenance["effective_provider"] == "local_heuristic"
        assert provenance["mode"] == "heuristic"
        assert provenance["fallback_used"] is False


def test_suggestion_request_accepts_local_heuristic_provider_field():
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

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["payload"]["provider"] == "local_heuristic"
        assert "provider" not in body["payload"]["brief"]

        bad_response = client.post(
            f"/api/v1/projects/{transcript.project_id}/suggest",
            json={
                "transcript_artifact_id": transcript.id,
                "analysis_artifact_id": analysis.id,
                "provider": "not_a_real_provider",
            },
        )
        assert bad_response.status_code == 422
