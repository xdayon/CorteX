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
    ClaudeCodeCliProvider,
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
    (_PROJECT_ROOT / "prompts/clip_selection.v3.schema.json").read_text(encoding="utf-8")
)


def _valid_clip(**overrides) -> dict:
    """Build a v3 clip; start_second/end_second map onto the fixture's 10 s units."""
    start = overrides.pop("start_second", 0)
    end = overrides.pop("end_second", 30)
    clip = {
        "title": "Um corte de teste",
        "headline": "Headline de teste",
        "pieces": [{"from_id": int(start // 10), "to_id": int(end // 10) - 1}],
        "connective_logic": "Uma única ideia contínua.",
        "hook_quote": "Frase de abertura.",
        "payoff_quote": "Frase final.",
        "score": 7,
        "reason": "Corte autocontido com abertura, desenvolvimento e conclusão claros.",
        "warnings": [],
    }
    clip.update(overrides)
    return clip


class FakeSuggestionProvider:
    def __init__(self, document: dict | None = None) -> None:
        self.document = document or {
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
            {"start": 0, "end": 10, "text": "Uma ideia completa para o teste."},
            {"start": 10, "end": 20, "text": "Ela possui contexto e conclusão."},
            *({"start": 10 * i, "end": 10 * i + 10, "text": f"Trecho número {i}."}
              for i in range(2, 9)),
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
            brief={"count": 1, "minimum_seconds": 15, "maximum_seconds": 60},
            progress_cb=lambda _progress, _message: None,
            should_cancel=lambda: False,
        )
        second = service.run(
            transcript_artifact=transcript,
            analysis_artifact=analysis,
            brief={"count": 1, "minimum_seconds": 15, "maximum_seconds": 60},
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
        clip = persisted["selection"]["clips"][0]
        assert clip["rank"] == 1
        assert clip["primary_speaker"] == "Dayon"
        assert clip["pacing"] == "dynamic"
        assert clip["scores"] == {"total": 7}
        assert clip["approximate_edl"] == [{
            "order": 0, "source_start": 0.0, "source_end": 30.0, "purpose": "hook",
            "preferred_visual": "primary_speaker", "audio_mode": "source",
            "transition_in": "hard_cut", "mask_jump_with": "none", "confidence": 1.0,
            "notes": "Peça 1 de 1; o motor de edição resolve o corte fino e o enxugamento.",
        }]


def test_provider_factory_returns_claude_code_cli_by_default():
    provider = build_suggestion_provider(load_config().ai, cwd=_PROJECT_ROOT)

    assert isinstance(provider, ClaudeCodeCliProvider)


def test_provider_factory_returns_codex_when_configured():
    ai_config = load_config().ai.model_copy(update={"provider": "codex_cli"})
    provider = build_suggestion_provider(ai_config, cwd=_PROJECT_ROOT)

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
            assert "corte" in str(exc)
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
            "selection_notes": "Corte curto demais.",
            "clips": [_valid_clip(end_second=10)],
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
            assert "Nenhum corte válido" in str(exc)
        else:
            raise AssertionError("clip com duração 14 deveria ser rejeitado")


def test_suggestion_service_keeps_higher_score_on_overlap():
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript(domain, tmp_dir)
        analysis = _analysis(domain, tmp_dir, transcript)
        provider = FakeSuggestionProvider(document={
            "selection_notes": "Dois cortes sobrepostos.",
            "clips": [
                _valid_clip(title="Fraco", start_second=0, end_second=30, score=5),
                _valid_clip(title="Forte", start_second=10, end_second=40, score=9),
            ],
        })
        service = SuggestionService(config, domain, provider)

        result = service.run(
            transcript_artifact=transcript,
            analysis_artifact=analysis,
            brief={"minimum_seconds": 15, "maximum_seconds": 60},
            progress_cb=lambda _progress, _message: None,
            should_cancel=lambda: False,
        )

        clips = result["selection"]["clips"]
        assert len(clips) == 1
        assert clips[0]["title"] == "Forte"
        assert clips[0]["rank"] == 1


def test_suggestion_service_rejects_score_out_of_range():
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript(domain, tmp_dir)
        analysis = _analysis(domain, tmp_dir, transcript)
        provider = FakeSuggestionProvider(document={
            "selection_notes": "Pontuação inválida.",
            "clips": [_valid_clip(score=11), _valid_clip(title="Válido", score=8, start_second=40, end_second=70)],
        })
        service = SuggestionService(config, domain, provider)

        result = service.run(
            transcript_artifact=transcript,
            analysis_artifact=analysis,
            brief={"minimum_seconds": 15, "maximum_seconds": 60},
            progress_cb=lambda _progress, _message: None,
            should_cancel=lambda: False,
        )

        clips = result["selection"]["clips"]
        assert len(clips) == 1
        assert clips[0]["title"] == "Válido"


def test_clip_selection_v3_schema_direct_validation():
    valid_document = {"selection_notes": "notas", "clips": [_valid_clip()]}
    errors = list(Draft202012Validator(CLIP_SELECTION_SCHEMA).iter_errors(valid_document))
    assert errors == []

    invalid_document = {"selection_notes": "notas", "clips": [{"title": "sem os outros campos"}]}
    errors = sorted(Draft202012Validator(CLIP_SELECTION_SCHEMA).iter_errors(invalid_document), key=str)
    assert errors, "clip incompleto deveria falhar a validação do schema"


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
