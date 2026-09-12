import json
from pathlib import Path

import pytest
from asgi_client import ASGITestClient
from conftest import FakeTranscriptionEngine
from cortex.api import create_app
from cortex.domain.store import DomainStore
from cortex.edit.planner import limit_timeline_duration, timeline_duration
from cortex.edit.profiles import PROFILES
from cortex.edit.schemas import EditPlanDocument
from cortex.edit.service import EditPlanService
from cortex.jobs import JobStore
from cortex.suggest.service import SuggestionService
from cortex.worker import process_next
from test_edit_plan import _config, _fixture, _word
from test_suggestion_cli import FakeSuggestionProvider, _transcript, _valid_clip


def test_suggestion_rejects_long_envelope_even_when_model_estimates_under_ceiling(tmp_path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    transcript = _transcript(domain, tmp_path)
    provider = FakeSuggestionProvider(
        {
            "schema_version": "1.0",
            "selection_notes": "Teste",
            "clips": [
                _valid_clip(rank=1, end_second=175, estimated_duration=110),
                _valid_clip(rank=2),
            ],
        }
    )
    result = SuggestionService(config, domain, provider).run(
        transcript_artifact=transcript,
        brief={"count": 2, "maximum_seconds": 120},
        progress_cb=lambda *_: None,
        should_cancel=lambda: False,
    )
    doc = json.loads(Path(result["suggestion_path"]).read_text())
    assert len(doc["selection"]["clips"]) == 1
    assert doc["selection"]["clips"][0]["rank"] == 2
    assert doc["duration_validation"]["rejected_count"] == 1
    assert "120s" in doc["selection"]["selection_notes"]


def test_ceiling_preserves_words_and_handles_incoming_overlap():
    words = [_word("fim.", 0, 1), _word("palavra.", 2, 3), _word("inteira", 3.8, 4.5)]
    segments = [
        {"start": 0, "end": 2, "timeline_order": 0, "transition": {"duration": 0.2}},
        {"start": 2, "end": 6, "timeline_order": 1},
    ]
    limited, issues = limit_timeline_duration(segments, words, 4, PROFILES["balanced"])
    assert timeline_duration(limited) <= 4
    assert all(not word.start < limited[-1]["end"] < word.end for word in words)
    assert issues[0]["code"] == "maximum_duration_trimmed"
    assert segments[-1]["end"] == 6


def test_maximum_is_persisted_propagated_by_worker_and_changes_cache(tmp_path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    jobs = JobStore(config.paths.database)
    domain = DomainStore(config.paths.database)
    words = [_word("fim.", i + 0.2, i + 0.65) for i in range(0, 180, 2)]
    project, source, transcript, analysis = _fixture(config, domain, words, 180)
    service = EditPlanService(config, domain)
    arguments = dict(
        transcript_artifact=transcript,
        analysis_artifact=analysis,
        start=0,
        end=175,
        profile="balanced",
        progress_cb=lambda *_: None,
        should_cancel=lambda: False,
    )
    old = service.run(**arguments)
    assert (
        EditPlanDocument.model_validate_json(
            Path(old["edit_plan_path"]).read_text()
        ).timeline_duration_seconds
        > 120
    )
    client = ASGITestClient(create_app(config))
    response = client.post(
        f"/api/v1/projects/{project.id}/edit-plans",
        json={
            "transcript_artifact_id": transcript.id,
            "analysis_artifact_id": analysis.id,
            "start": 0,
            "end": 175,
            "profile": "balanced",
            "maximum_seconds": 120,
        },
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    process_next(config, jobs, domain, FakeTranscriptionEngine())
    job = jobs.get(job_id)
    assert job.status.value == "succeeded", job.error
    doc = EditPlanDocument.model_validate_json(Path(job.result["edit_plan_path"]).read_text())
    assert doc.maximum_seconds == 120 and 118 <= doc.timeline_duration_seconds <= 120
    assert doc.input_hash != domain.get_stage_artifact(old["edit_plan_artifact_id"]).input_hash
    assert all(not w.start < doc.segments[-1].end < w.end for w in words)
    again = service.run(**arguments, maximum_seconds=120)
    assert again["cached"] and again["edit_plan_artifact_id"] == job.result["edit_plan_artifact_id"]


@pytest.mark.parametrize("maximum", [0, -1, float("inf"), float("nan")])
def test_invalid_limits_are_rejected(maximum):
    with pytest.raises(ValueError):
        limit_timeline_duration([{"start": 0, "end": 3}], [], maximum, PROFILES["balanced"])
