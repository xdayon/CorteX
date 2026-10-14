from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex.diarize.attribution import subject_share, subject_share_for_ranges
from cortex.domain.store import DomainStore
from cortex.suggest.service import SuggestionService

from test_suggestion_cli import FakeSuggestionProvider, _config, _transcript, _valid_clip


def _turn(start: float, end: float, speaker: str = "dayon") -> dict:
    return {"start": start, "end": end, "speaker": speaker}


def _clip(start: float, end: float, **overrides) -> dict:
    return _valid_clip(start_second=start, end_second=end, **overrides)


def _service(tmp_path: Path, clip: dict):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    transcript = _transcript(domain, tmp_path)
    provider = FakeSuggestionProvider({
        "selection_notes": "Teste de atribuição.", "clips": [clip],
    })
    return SuggestionService(config, domain, provider), domain, transcript, provider


def _run(service, transcript, brief):
    return service.run(
        transcript_artifact=transcript, brief=brief,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )


def test_source_union_does_not_count_duplicate_or_overlapping_edl_twice():
    turns = [_turn(0, 10), _turn(10, 30, "interviewer")]
    assert subject_share_for_ranges(
        turns, "dayon", [(0, 10), (0, 10), (0, 10), (5, 20), (20, 30)],
    ) == pytest.approx(1 / 3)


def test_overlap_never_confirms_subject_and_duplicate_turns_do_not_inflate_share():
    turns = [_turn(0, 10), _turn(0, 10), _turn(5, 15, "interviewer")]
    assert subject_share(turns, "dayon", 0, 15) == pytest.approx(1 / 3)


def test_source_union_counts_only_selected_speech_and_excludes_silence():
    turns = [_turn(0, 3), _turn(3, 8, "interviewer"), _turn(10, 12, "guest")]
    assert subject_share_for_ranges(turns, "dayon", [(0, 3), (8, 12)]) == pytest.approx(0.6)
    assert subject_share_for_ranges(turns, "dayon", [(8, 10)]) == 0
    assert subject_share_for_ranges(turns, "dayon", []) == 0


@pytest.mark.parametrize("ranges", [[(5, 4)], [(0, float("nan"))], [(0, float("inf"))]])
def test_invalid_editorial_interval_fails_explicitly(ranges):
    with pytest.raises(ValueError, match="Intervalo de fonte inválido"):
        subject_share_for_ranges([_turn(0, 10)], "dayon", ranges)


# The LLM no longer returns a granular approximate_edl: normalization always
# emits a single "source" range covering the clip's full [start, end] window
# (the deterministic edit engine resolves fine cuts later, including via the
# tighten pass). So envelope_subject_share and editorial_subject_share are now
# computed over the same range and are always equal by construction — the two
# checks in SuggestionService.run collapse to one. These tests exercise that
# collapsed behavior instead of the old (now impossible) divergent-range case.
def test_suggestion_requires_subject_predominance_over_the_full_clip_span(tmp_path):
    service, domain, transcript, provider = _service(tmp_path, _clip(60, 90))
    brief = {"confirmed_voice": {"speaker": "dayon", "turns": [
        _turn(0, 60), _turn(60, 90, "interviewer"),
    ]}}
    with pytest.raises(ValueError, match="intervalo=1; EDL editorial=1"):
        _run(service, transcript, brief)
    assert len(provider.calls) == 1
    assert domain.list_stage_artifacts(transcript.project_id, "suggestion") == []


def test_suggestion_persists_both_metrics_and_reuses_only_matching_policy_cache(tmp_path, monkeypatch):
    import cortex.suggest.service as suggestion_module

    service, domain, transcript, provider = _service(tmp_path, _clip(0, 60))
    brief = {"confirmed_voice": {"diarization_artifact_id": "voices-id", "speaker": "dayon", "turns": [
        _turn(0, 45), _turn(45, 90, "interviewer"),
    ]}}
    first = _run(service, transcript, brief)
    document = json.loads(Path(first["suggestion_path"]).read_text())
    validation = document["voice_validation"]
    assert validation["scope"] == "suggestion_envelope_and_editorial_source_union"
    assert validation["clips"] == [{
        "clip_rank": 1, "envelope_subject_share": 0.75,
        "editorial_subject_share": 0.75, "accepted": True,
    }]
    assert _run(service, transcript, brief)["cached"] is True
    assert len(provider.calls) == 1
    monkeypatch.setattr(suggestion_module, "VOICE_ATTRIBUTION_VERSION", validation["version"] + 1)
    assert _run(service, transcript, brief)["cached"] is False
    assert len(provider.calls) == 2
    assert len(domain.list_stage_artifacts(transcript.project_id, "suggestion")) == 2


def test_no_confirmed_voice_preserves_existing_suggestion_path(tmp_path):
    service, _domain, transcript, provider = _service(tmp_path, _clip(60, 90))
    result = _run(service, transcript, {})
    assert result["selection"]["clips"][0]["approximate_edl"][0]["source_start"] == 60
    assert "voice_validation" not in json.loads(Path(result["suggestion_path"]).read_text())
    assert len(provider.calls) == 1


@pytest.mark.parametrize("voice", [
    None,
    {},
    {"speaker": "dayon", "turns": []},
    {"speaker": "dayon", "turns": [_turn(0, 10, "guest")]},
    {"speaker": "dayon", "turns": [_turn(10, 0)]},
    {"speaker": "dayon", "turns": [_turn(0, float("nan"))]},
])
def test_invalid_voice_reference_fails_before_provider_and_persistence(tmp_path, voice):
    service, domain, transcript, provider = _service(tmp_path, _clip(0, 30))
    with pytest.raises(ValueError, match="Referência de voz confirmada inválida"):
        _run(service, transcript, {"confirmed_voice": voice})
    assert provider.calls == []
    assert domain.list_stage_artifacts(transcript.project_id, "suggestion") == []
