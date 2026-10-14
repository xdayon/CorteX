from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from cortex.domain.models import TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.edit.schemas import EditPlanDocument
from cortex.jobs import JobStore
from cortex.schemas import JobCreate, JobStatus, JobType
from cortex.suggest.tighten import TightenService, build_phrases
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine
from test_edit_plan import _config as _edit_plan_config
from test_edit_plan import _fixture, _word
from test_suggestion_cli import FakeSuggestionProvider, _config as _suggestion_config


def _segment_words(*words: tuple[float, float, str]) -> dict:
    return {
        "start": words[0][0], "end": words[-1][1],
        "text": " ".join(word for _s, _e, word in words),
        "words": [{"start": s, "end": e, "word": w} for s, e, w in words],
    }


def test_build_phrases_splits_on_gap_and_flushes_last_word():
    transcript = {"segments": [_segment_words(
        (0.0, 0.3, "Um"), (0.3, 0.6, "dois"), (0.6, 0.9, "três"),
        (2.0, 2.3, "quatro"), (2.3, 2.6, "cinco."),
    )]}

    phrases = build_phrases(transcript, 0.0, 2.6)

    assert [p["text"] for p in phrases] == ["Um dois três", "quatro cinco."]
    assert phrases[0]["start"] == 0.0
    assert phrases[0]["end"] == 0.9
    assert phrases[1]["start"] == 2.0
    assert phrases[1]["end"] == 2.6


def test_build_phrases_splits_after_max_word_count():
    words = [(i * 0.1, i * 0.1 + 0.1, f"palavra{i}") for i in range(16)]
    transcript = {"segments": [_segment_words(*words)]}

    phrases = build_phrases(transcript, 0.0, 20.0)

    assert len(phrases) == 2
    assert len(phrases[0]["text"].split()) == 14
    assert len(phrases[1]["text"].split()) == 2


def test_build_phrases_ignores_words_outside_window():
    transcript = {"segments": [_segment_words(
        (0.0, 0.3, "fora"), (5.0, 5.3, "dentro."), (9.0, 9.3, "fora"),
    )]}

    phrases = build_phrases(transcript, 4.0, 6.0)

    assert len(phrases) == 1
    assert phrases[0]["text"] == "dentro."


def _transcript_artifact(domain: DomainStore, tmp_dir: Path, words: list[tuple[float, float, str]]) -> TranscriptArtifact:
    project = domain.create_project("Enxugamento")
    transcript_path = tmp_dir / "transcript.json"
    segment = _segment_words(*words)
    transcript_path.write_text(json.dumps({
        "schema_version": 1, "language": "pt", "duration_seconds": words[-1][1] + 5,
        "segments": [segment],
    }), encoding="utf-8")
    return domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project.id, source_asset_id="source-fixture", path=str(transcript_path),
        audio_sha256="c" * 64, engine="fake", model="fake", device="cpu", compute_type="int8",
        language="pt", vad=True, batch_size=1, duration_seconds=words[-1][1] + 5,
    ))


_WORDS = [
    (0.5, 1.0, "isso"), (1.0, 1.5, "bom."),
    (2.0, 2.5, "tangente"), (2.5, 3.0, "aqui."),
    (6.0, 6.5, "voltando"), (6.5, 7.0, "ponto."),
]


def test_tighten_service_merges_contiguous_kept_phrases_into_ranges():
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _suggestion_config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript_artifact(domain, tmp_dir, _WORDS)
        provider = FakeSuggestionProvider({"keep_ids": [0, 2], "notes": "removi a tangente"})
        service = TightenService(config, domain, provider)

        result = service.run(
            transcript_artifact=transcript, start=0.0, end=7.0, brief=None,
            progress_cb=lambda *_args: None, should_cancel=lambda: False,
        )

        assert result["ranges"] == [
            {"source_start": 0.5, "source_end": 1.5, "audio_mode": "source"},
            {"source_start": 6.0, "source_end": 7.0, "audio_mode": "source"},
        ]


def _run_tighten(keep_ids):
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _suggestion_config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript_artifact(domain, tmp_dir, _WORDS)
        provider = FakeSuggestionProvider({"keep_ids": keep_ids, "notes": "x"})
        return TightenService(config, domain, provider).run(
            transcript_artifact=transcript, start=0.0, end=7.0, brief=None,
            progress_cb=lambda *_args: None, should_cancel=lambda: False,
        )


def test_tighten_service_rejects_only_unknown_ids():
    with pytest.raises(ValueError, match="nenhuma frase"):
        _run_tighten([99])


def test_tighten_service_normalizes_order_duplicates_and_unknown_ids():
    # Phrases are never reordered, so order/duplicates carry no meaning and an
    # unknown id is ignored instead of blocking the export.
    assert _run_tighten([2, 0, 0, 99])["ranges"] == _run_tighten([0, 2])["ranges"]


def test_tighten_service_rejects_empty_keep_ids():
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _suggestion_config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript_artifact(domain, tmp_dir, _WORDS)
        provider = FakeSuggestionProvider({"keep_ids": [], "notes": "x"})
        service = TightenService(config, domain, provider)

        with pytest.raises(ValueError, match="não manteve nenhuma frase"):
            service.run(
                transcript_artifact=transcript, start=0.0, end=7.0, brief=None,
                progress_cb=lambda *_args: None, should_cancel=lambda: False,
            )


def test_tighten_service_rejects_kept_ratio_below_minimum():
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _suggestion_config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript_artifact(domain, tmp_dir, _WORDS)
        provider = FakeSuggestionProvider({"keep_ids": [0], "notes": "x"})
        service = TightenService(config, domain, provider)

        with pytest.raises(ValueError, match="abaixo de 40%"):
            service.run(
                transcript_artifact=transcript, start=0.0, end=7.0, brief=None,
                progress_cb=lambda *_args: None, should_cancel=lambda: False,
            )


def test_tighten_service_cache_hit_skips_provider():
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _suggestion_config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript_artifact(domain, tmp_dir, _WORDS)
        provider = FakeSuggestionProvider({"keep_ids": [0, 2], "notes": "removi a tangente"})
        service = TightenService(config, domain, provider)

        first = service.run(
            transcript_artifact=transcript, start=0.0, end=7.0, brief=None,
            progress_cb=lambda *_args: None, should_cancel=lambda: False,
        )
        second = service.run(
            transcript_artifact=transcript, start=0.0, end=7.0, brief=None,
            progress_cb=lambda *_args: None, should_cancel=lambda: False,
        )

        assert first["cached"] is False
        assert second["cached"] is True
        assert first["tighten_artifact_id"] == second["tighten_artifact_id"]
        assert len(provider.calls) == 1


def test_worker_edit_plan_job_with_tighten_uses_tighten_edl(tmp_path: Path) -> None:
    config = _edit_plan_config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    jobs = JobStore(config.paths.database)
    words = [_word(text, start, end) for start, end, text in _WORDS]
    _project, _source, transcript, analysis = _fixture(config, domain, words, 8.0)

    job = jobs.create(JobCreate(
        type=JobType.EDIT_PLAN,
        project_id=transcript.project_id,
        payload={
            "transcript_artifact_id": transcript.id,
            "analysis_artifact_id": analysis.id,
            "start": 0.0, "end": 7.0, "profile": "balanced",
            "tighten": True,
        },
    ))
    provider = FakeSuggestionProvider({"keep_ids": [0, 2], "notes": "removi a tangente"})

    assert process_next(config, jobs, domain, FakeTranscriptionEngine(), provider)

    final = jobs.get(job.id)
    assert final.status == JobStatus.SUCCEEDED, final.error
    assert "tighten_artifact_id" in final.result
    tighten_artifact = domain.get_stage_artifact(final.result["tighten_artifact_id"])
    assert tighten_artifact.stage == "tighten"
    edit_plan_artifact = domain.get_stage_artifact(final.result["edit_plan_artifact_id"])
    document = EditPlanDocument.model_validate_json(Path(edit_plan_artifact.path).read_text())
    assert len(document.segments) == 2
