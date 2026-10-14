from __future__ import annotations

from pathlib import Path

import pytest
from asgi_client import ASGITestClient as TestClient

from cortex.api import create_app
from cortex.domain.store import DomainStore
from cortex.edit.schemas import EditPlanDocument
from cortex.jobs import JobStore
from cortex.schemas import JobCreate, JobStatus, JobType
from cortex.suggest.service import (
    _normalize_clip,
    _selection_units,
    _transcript_lines,
    _validate_and_normalize_selection,
)
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine
from test_edit_plan import _config as _edit_plan_config
from test_edit_plan import _fixture, _word
from test_selection_units import ScriptedProvider, _run
from test_suggestion_cli import _valid_clip
from test_tighten import _WORDS

BRIEF = {"minimum_seconds": 10, "maximum_seconds": 60, "primary_subject": "Dayon"}


def _units(count: int = 12, size: float = 10.0) -> list[dict]:
    return [
        {"id": i, "start": i * size, "end": (i + 1) * size, "text": f"linha {i}.",
         "speaker_label": None}
        for i in range(count)
    ]


def _clip(pieces, **overrides) -> dict:
    clip = {
        "title": "T", "headlines": ["H", "H2", "H3"],
        "pieces": [{"from_id": a, "to_id": b} for a, b in pieces], "emphasis": [],
        "score": 7, "reason": "r", "warnings": [],
    }
    clip.update(overrides)
    return clip


def _validate(clips, units=None, *, brief=None, duration=120, envelope=240.0):
    return _validate_and_normalize_selection(
        {"selection_notes": "n", "clips": clips}, brief=brief or BRIEF,
        transcript={"duration_seconds": duration}, units=units or _units(),
        max_envelope_seconds=envelope,
    )


def test_fine_units_sequential_ids_and_line_format():
    words = [{"start": s, "end": e, "word": w} for w, s, e in [
        ("a,", 0.0, 1.3), ("b", 1.3, 1.6), ("c", 2.5, 3.0)]]
    units = _selection_units(
        {"segments": [{"start": 0, "end": 3, "text": "", "words": words}]},
        voice=None, primary_subject="Dayon",
    )
    assert [(u["id"], u["text"]) for u in units] == [(0, "a,"), (1, "b"), (2, "c")]
    assert _transcript_lines(units).splitlines()[0] == "#0 [0.0] a,"
    labelled = [{**units[0], "speaker_label": "D"}, {**units[1], "speaker_label": "D"}]
    assert _transcript_lines(labelled).splitlines() == ["#0 [0.0] D: a,", "#1 [1.3] b"]


def test_pieces_resolution_discards_invalid_out_of_order_and_short():
    units = _units()
    units[7]["end"] = units[7]["start"] + 0.3
    clip = _clip([(0, 1), (9, 99), (5, 4), (0, 0), (3, 3), (7, 7)])
    result = _validate([clip], units)
    pieces = result["clips"][0]["pieces"]
    assert [(p["from_id"], p["to_id"]) for p in pieces] == [(0, 1), (3, 3)]
    warnings = " ".join(result["clips"][0]["warnings"])
    assert "IDs de linha inválidos" in warnings
    assert "fora de ordem" in warnings
    assert "curto demais" in warnings


def test_contiguous_pieces_are_merged_and_order_not_rearranged():
    result = _validate([_clip([(2, 3), (4, 5)])])
    assert [(p["from_id"], p["to_id"]) for p in result["clips"][0]["pieces"]] == [(2, 5)]
    result = _validate([_clip([(5, 5), (1, 2), (8, 8)])])
    assert [(p["from_id"], p["to_id"]) for p in result["clips"][0]["pieces"]] == [(5, 5), (8, 8)]


def test_clip_rejected_by_duration_sum_envelope_and_piece_count():
    good = _clip([(0, 0), (5, 5)], title="ok")
    too_short = _clip([(0, 0)], title="curto", score=9)
    too_long = _clip([(0, 3), (6, 8)], title="longo", score=9)
    result = _validate([good, too_short, too_long], brief={**BRIEF, "minimum_seconds": 15})
    assert [c["title"] for c in result["clips"]] == ["ok"]
    assert "2 corte(s)" in result["selection_notes"]

    wide = _clip([(0, 0), (10, 10)], title="largo")
    with pytest.raises(ValueError, match="Nenhum corte válido"):
        _validate([wide], envelope=100.0)

    units = _units(40, size=1.0)
    many = _clip([(i * 2, i * 2) for i in range(16)])
    with pytest.raises(ValueError, match="Nenhum corte válido"):
        _validate([many], units, brief={**BRIEF, "minimum_seconds": 1})
    ok = _clip([(i * 2, i * 2) for i in range(15)])
    assert _validate([ok], units, brief={**BRIEF, "minimum_seconds": 1})["clips"]


def test_clip_rejected_by_score_and_episode_end():
    with pytest.raises(ValueError):
        _validate([_clip([(0, 0)], score=11)])
    with pytest.raises(ValueError):
        _validate([_clip([(10, 11)])], duration=105)


def test_overlap_is_by_pieces_not_envelope():
    interleaved_a = _clip([(0, 0), (4, 4)], title="A", score=5)
    interleaved_b = _clip([(2, 2), (6, 6)], title="B", score=6)
    result = _validate([interleaved_a, interleaved_b])
    assert sorted(c["title"] for c in result["clips"]) == ["A", "B"]

    clash = _clip([(2, 2), (5, 5)], title="C", score=9)
    result = _validate([interleaved_a, clash, _clip([(4, 4)], title="D", score=3)])
    titles = [c["title"] for c in result["clips"]]
    assert "C" in titles and "D" not in titles


def test_heuristic_warnings():
    units = _units()
    units[0]["text"] = "Começo da ideia, com gancho."
    units[4]["text"] = "Isso muda tudo, de verdade."
    units[6]["text"] = "O desfecho final."
    clip = _clip([(0, 0), (4, 4), (6, 6)])
    warnings = _validate([clip], units)["clips"][0]["warnings"]
    assert "A peça 2 começa com 'isso': confira se o referente ficou de fora." in warnings

    clean = _clip([(0, 0)])
    assert _validate([clean], units)["clips"][0]["warnings"] == []


def test_normalize_clip_builds_composed_fields():
    result = _validate([_clip([(0, 0), (3, 4), (8, 8)])])
    clip = result["clips"][0]
    assert (clip["start_second"], clip["end_second"]) == (0.0, 90.0)
    assert clip["estimated_duration"] == 40.0
    assert clip["transcript_text"] == "linha 0. … linha 3. linha 4. … linha 8."
    assert clip["headline"] == "H"
    assert clip["headline_options"] == ["H", "H2", "H3"]
    assert clip["pieces"][1] == {
        "from_id": 3, "to_id": 4, "start": 30.0, "end": 50.0, "text": "linha 3. linha 4.",
    }
    edl = clip["approximate_edl"]
    assert [e["piece"] for e in edl] == [0, 1, 2]
    assert [(e["order"], e["source_start"], e["source_end"], e["purpose"]) for e in edl] == [
        (0, 0.0, 10.0, "hook"), (1, 30.0, 50.0, "development"), (2, 80.0, 90.0, "payoff"),
    ]
    assert "Peça 2 de 3" in edl[1]["notes"]
    assert clip["hook"]["end_second"] == 5.0
    assert clip["payoff"]["start_second"] == 82.0


def test_normalize_clip_single_piece_is_hook():
    resolved = {**_clip([(0, 0)]), "pieces": [
        {"from_id": 0, "to_id": 0, "start": 0.0, "end": 20.0, "text": "t"}]}
    clip = _normalize_clip(1, resolved, BRIEF)
    assert [e["purpose"] for e in clip["approximate_edl"]] == ["hook"]
    assert clip["payoff"]["start_second"] == 12.0


def test_prompt_carries_rules_and_numbered_lines():
    provider = ScriptedProvider([{"selection_notes": "ok", "clips": [_valid_clip()]}])
    _run(provider, {"count": 1, "minimum_seconds": 10, "maximum_seconds": 60})
    assert "#0 [0.0] Uma ideia completa" in provider.prompts[0]
    assert '"selection_rules":{"max_envelope_seconds":240.0,"max_pieces":15,"min_piece_seconds":0.6}' in provider.prompts[0]


def _two_piece_doc(*pieces_list):
    return {"selection_notes": "ok", "clips": [
        _valid_clip(title=f"c{i}", pieces=[{"from_id": a, "to_id": b} for a, b in pieces], score=7 + i)
        for i, pieces in enumerate(pieces_list)
    ]}


def test_voice_multi_piece_uses_editorial_share_only():
    # Speaker owns units 0 and 2 (0-10, 20-30) but not the gap, so envelope share is ~0.67
    # on a 3-unit span; make the gap large to push envelope below 0.5.
    turns = [{"start": 0, "end": 10, "speaker": "S1"}, {"start": 10, "end": 50, "speaker": "S0"},
             {"start": 50, "end": 60, "speaker": "S1"}]
    voice = {"speaker": "S1", "turns": turns}
    provider = ScriptedProvider([_two_piece_doc([(0, 0), (5, 5)])])
    result = _run(provider, {"count": 1, "minimum_seconds": 10}, voice=voice)
    clip = result["selection"]["clips"][0]
    assert len(clip["pieces"]) == 2

    single = ScriptedProvider([_two_piece_doc([(0, 5)])])
    with pytest.raises(ValueError, match="voz confirmada"):
        _run(single, {"count": 1, "minimum_seconds": 10, "maximum_seconds": 70}, voice=voice)


def test_exclude_and_top_up_use_pieces():
    excluded = [{"start": 20.0, "end": 30.0}]
    provider = ScriptedProvider([_two_piece_doc([(0, 0), (2, 2)], [(4, 4), (6, 6)])])
    result = _run(provider, {"count": 2, "exclude_ranges": excluded, "minimum_seconds": 10})
    assert [c["title"] for c in result["selection"]["clips"]] == ["c1"]

    first = _two_piece_doc([(0, 0), (4, 4)])
    second = _two_piece_doc([(2, 2), (6, 6)], [(4, 4), (7, 7)])
    provider = ScriptedProvider([first, second])
    result = _run(provider, {"count": 5, "minimum_seconds": 10})
    assert '"exclude_ranges":[{"start":0.0,"end":10.0},{"start":40.0,"end":50.0}]' in provider.prompts[1]
    clips = result["selection"]["clips"]
    assert len(clips) == 2
    assert {tuple(p["from_id"] for p in c["pieces"]) for c in clips} == {(0, 4), (2, 6)}


def test_refine_of_tiny_composed_edl_is_skipped_and_keeps_pieces(tmp_path: Path) -> None:
    config = _edit_plan_config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    jobs = JobStore(config.paths.database)
    words = [_word(text, start, end) for start, end, text in _WORDS]
    _project, _source, transcript, analysis = _fixture(config, domain, words, 8.0)
    edl = [
        {"order": 0, "source_start": 0.5, "source_end": 1.5, "purpose": "hook",
         "audio_mode": "source"},
        {"order": 1, "source_start": 6.0, "source_end": 7.0, "purpose": "payoff",
         "audio_mode": "source"},
    ]
    job = jobs.create(JobCreate(
        type=JobType.EDIT_PLAN, project_id=transcript.project_id,
        payload={
            "transcript_artifact_id": transcript.id, "analysis_artifact_id": analysis.id,
            "start": 0.0, "end": 7.0, "profile": "balanced", "tighten": True,
            "approximate_edl": edl,
        },
    ))

    class _NoCalls:
        calls = 0

        def generate(self, *_args, **_kwargs):
            _NoCalls.calls += 1
            raise AssertionError("a two-phrase clip has nothing to refine")

    assert process_next(config, jobs, domain, FakeTranscriptionEngine(), _NoCalls())
    final = jobs.get(job.id)
    assert final.status == JobStatus.SUCCEEDED, final.error
    assert final.result["refine"]["status"] == "skipped"
    assert _NoCalls.calls == 0
    artifact = domain.get_stage_artifact(final.result["edit_plan_artifact_id"])
    document = EditPlanDocument.model_validate_json(Path(artifact.path).read_text())
    assert [segment.piece_index for segment in document.segments] == [0, 1]


def test_edit_plan_endpoint_forwards_approximate_edl(tmp_path: Path) -> None:
    config = _edit_plan_config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    words = [_word(text, start, end) for start, end, text in _WORDS]
    project, _source, transcript, analysis = _fixture(config, domain, words, 8.0)
    edl = [{"order": 0, "source_start": 0.5, "source_end": 1.5, "audio_mode": "source"}]
    client = TestClient(create_app(config))
    response = client.post(f"/api/v1/projects/{project.id}/edit-plans", json={
        "transcript_artifact_id": transcript.id, "analysis_artifact_id": analysis.id,
        "start": 0.0, "end": 7.0, "profile": "balanced", "approximate_edl": edl,
    })
    assert response.status_code == 201, response.text
    job = JobStore(config.paths.database).get(response.json()["id"])
    assert job.payload["approximate_edl"] == edl


def _word_transcript(words: list[tuple[str, float, float]], duration: float = 120.0) -> dict:
    return {"duration_seconds": duration, "segments": [{
        "start": 0.0, "end": duration, "text": "",
        "words": [{"word": w, "start": s, "end": e} for w, s, e in words],
    }]}


def test_leading_fillers_are_trimmed_from_the_cold_open():
    words = [("Então,", 0.0, 0.4), ("né,", 0.5, 0.8), ("a", 1.0, 1.1), ("consciência", 1.2, 2.0)]
    words += [(f"palavra{i}", 2.0 + i * 0.5, 2.4 + i * 0.5) for i in range(60)]
    transcript = _word_transcript(words)
    units = [{"id": 0, "start": 0.0, "end": 32.0, "text": "Então, né, a consciência …",
              "speaker_label": None}]
    result = _validate_and_normalize_selection(
        {"selection_notes": "n", "clips": [_clip([(0, 0)])]}, brief=BRIEF,
        transcript=transcript, units=units, max_envelope_seconds=240.0,
    )
    clip = result["clips"][0]
    assert clip["start_second"] == 1.0
    assert clip["lead_trimmed"] == "Então, né,"
    assert clip["approximate_edl"][0]["source_start"] == 1.0


def test_meaningful_first_word_is_never_trimmed():
    words = [("É", 0.0, 0.3), ("impressionante", 0.4, 1.2)]
    words += [(f"palavra{i}", 1.5 + i * 0.5, 1.9 + i * 0.5) for i in range(60)]
    units = [{"id": 0, "start": 0.0, "end": 31.5, "text": "É impressionante …",
              "speaker_label": None}]
    result = _validate_and_normalize_selection(
        {"selection_notes": "n", "clips": [_clip([(0, 0)])]}, brief=BRIEF,
        transcript=_word_transcript(words), units=units, max_envelope_seconds=240.0,
    )
    assert result["clips"][0]["start_second"] == 0.0
    assert result["clips"][0]["lead_trimmed"] == ""


def test_emphasis_phrases_map_to_transcript_word_indices():
    words = [("A", 0.0, 0.2), ("glândula", 0.3, 0.9), ("pineal", 1.0, 1.5), ("é", 1.6, 1.7)]
    words += [(f"palavra{i}", 2.0 + i * 0.5, 2.4 + i * 0.5) for i in range(60)]
    units = [{"id": 0, "start": 0.0, "end": 32.0, "text": "A glândula pineal é …",
              "speaker_label": None}]
    clip = _clip([(0, 0)], emphasis=["glândula pineal", "inexistente", "PALAVRA3"])
    result = _validate_and_normalize_selection(
        {"selection_notes": "n", "clips": [clip]}, brief=BRIEF,
        transcript=_word_transcript(words), units=units, max_envelope_seconds=240.0,
    )
    assert result["clips"][0]["emphasis_word_indices"] == [1, 2, 7]


def test_headline_options_are_deduplicated_and_first_is_recommended():
    clip = _clip([(0, 2)], headlines=["  Uma  revelação ", "uma revelação", "Outra"])
    normalized = _validate([clip])["clips"][0]
    assert normalized["headline"] == "Uma revelação"
    assert normalized["headline_options"] == ["Uma revelação", "Outra"]


def test_clip_without_headline_is_discarded():
    result = _validate([_clip([(0, 2)], headlines=[" "]), _clip([(4, 6)], title="ok")])
    assert [clip["title"] for clip in result["clips"]] == ["ok"]


def test_cache_identity_ignores_binary_path_and_timeout(tmp_path):
    from cortex.suggest.service import provider_identity
    from test_suggestion_cli import _config as suggestion_config

    config = suggestion_config(tmp_path)
    before = provider_identity(config)
    config.ai.claude_binary = "/outro/caminho/claude"
    config.ai.timeout_seconds = 1200
    assert provider_identity(config) == before
    config.ai.claude_effort = "high"
    assert provider_identity(config) != before
