from __future__ import annotations

import tempfile
from pathlib import Path

from cortex.diarize.attribution import ConfirmedVoice
from cortex.domain.store import DomainStore
from cortex.suggest.provider import SuggestionProviderResult
from cortex.suggest.service import (
    SuggestionService,
    _selection_units,
    _transcript_lines,
)

from test_suggestion_cli import _analysis, _config, _transcript, _valid_clip


def _words(spec):
    return [{"start": s, "end": e, "word": w, "probability": 1.0} for w, s, e in spec]


def _units(segments, voice=None):
    return _selection_units({"segments": segments}, voice=voice, primary_subject="Dayon")


def test_units_break_on_punctuation_after_min_seconds():
    words = _words([("Olá", 0.0, 0.5), ("mundo,", 0.5, 1.0), ("isso", 1.0, 1.5),
                    ("segue,", 1.5, 2.2), ("fim.", 2.2, 3.0)])
    units = _units([{"start": 0, "end": 3, "text": "", "words": words}])
    assert [u["text"] for u in units] == ["Olá mundo, isso segue,", "fim."]
    assert [u["id"] for u in units] == [0, 1]
    assert (units[0]["start"], units[0]["end"]) == (0.0, 2.2)


def test_units_break_at_eight_seconds_and_on_pause():
    words = _words([(f"p{i}", float(i), i + 1.0) for i in range(10)])
    units = _units([{"start": 0, "end": 10, "text": "", "words": words}])
    assert [(u["start"], u["end"]) for u in units] == [(0.0, 8.0), (8.0, 10.0)]

    paused = _words([("antes", 0.0, 1.0), ("depois", 1.7, 3.0)])
    units = _units([{"start": 0, "end": 3, "text": "", "words": paused}])
    assert [u["text"] for u in units] == ["antes", "depois"]
    assert [u["id"] for u in units] == [0, 1]


def test_units_fallback_without_words_and_hesitation_strip():
    segments = [
        {"start": 1.0, "end": 9.0, "text": "eh uma frase"},
        {"start": 9.0, "end": 12.0, "text": "hum"},
    ]
    units = _units(segments)
    assert units == [
        {"id": 0, "start": 1.0, "end": 9.0, "text": "uma frase", "speaker_label": None},
    ]
    assert _transcript_lines(units) == "#0 [1.0|8.0] uma frase"


def test_units_speaker_labels_from_confirmed_voice():
    voice = ConfirmedVoice.model_validate({"speaker": "S1", "turns": [
        {"start": 0, "end": 5, "speaker": "S0"},
        {"start": 5, "end": 10, "speaker": "S1"},
    ]})
    segments = [
        {"start": 0, "end": 4, "text": "Pergunta aqui"},
        {"start": 5, "end": 9, "text": "Resposta aqui"},
        {"start": 20, "end": 24, "text": "Sem turno"},
    ]
    units = _units(segments, voice)
    assert [u["speaker_label"] for u in units] == ["O", "D", None]
    assert _transcript_lines(units).splitlines() == [
        "#0 [0.0|4.0] O: Pergunta aqui", "#1 [5.0|4.0] D: Resposta aqui", "#2 [20.0|4.0] Sem turno",
    ]


class ScriptedProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt, schema, *, should_cancel, system_context=None):
        self.prompts.append(f"{system_context or ''}\n{prompt}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SuggestionProviderResult(
            document=response, provenance={"provider": "fake", "model": "m", "binary_version": "t"},
        )


def _doc(*windows):
    return {"selection_notes": "ok", "clips": [
        _valid_clip(start_second=s, end_second=e, score=score) for s, e, score in windows
    ]}


def _run(provider, brief, voice=None):
    with tempfile.TemporaryDirectory() as directory:
        tmp_dir = Path(directory)
        config = _config(tmp_dir)
        config.ensure_runtime_dirs()
        domain = DomainStore(config.paths.database)
        transcript = _transcript(domain, tmp_dir)
        analysis = _analysis(domain, tmp_dir, transcript)
        full = {"minimum_seconds": 5, "maximum_seconds": 20, **brief}
        if voice:
            full["confirmed_voice"] = voice
        return SuggestionService(config, domain, provider).run(
            transcript_artifact=transcript, analysis_artifact=analysis, brief=full,
            progress_cb=lambda _p, _m: None, should_cancel=lambda: False,
        )


def test_confirmed_voice_not_sent_to_provider():
    voice = {"speaker": "S1", "turns": [{"start": 0, "end": 60, "speaker": "S1"}]}
    provider = ScriptedProvider([_doc((0, 10, 7))])
    _run(provider, {"count": 1}, voice=voice)
    assert "confirmed_voice" not in provider.prompts[0]
    assert "D: Uma ideia completa" in provider.prompts[0]


def test_top_up_adds_clips_and_renumbers():
    provider = ScriptedProvider([
        _doc((0, 10, 8), (20, 30, 6)),
        _doc((40, 50, 9), (0, 10, 7), (60, 70, 5)),
    ])
    result = _run(provider, {"count": 10})
    clips = result["selection"]["clips"]
    assert len(provider.prompts) == 2
    assert "Segunda rodada" in provider.prompts[1]
    assert '"count":8' in provider.prompts[1]
    assert [c["rank"] for c in clips] == [1, 2, 3, 4, 5][: len(clips)]
    assert [c["scores"]["total"] for c in clips] == sorted(
        (c["scores"]["total"] for c in clips), reverse=True)
    windows = sorted((c["start_second"], c["end_second"]) for c in clips)
    assert all(a[1] <= b[0] for a, b in zip(windows, windows[1:]))
    assert result["provenance"]["top_up"]["requested"] == 8
    assert result["provenance"]["top_up"]["added"] == len(clips) - 2


def test_top_up_adds_three_when_second_round_has_no_overlap():
    provider = ScriptedProvider([
        _doc((0, 10, 8), (20, 30, 6)),
        _doc((40, 50, 9), (50, 60, 7), (70, 80, 5)),
    ])
    result = _run(provider, {"count": 10})
    clips = result["selection"]["clips"]
    assert [c["rank"] for c in clips] == [1, 2, 3, 4, 5]
    assert result["provenance"]["top_up"]["added"] == 3


def test_top_up_failure_keeps_first_round_with_note():
    provider = ScriptedProvider([_doc((0, 10, 8), (20, 30, 6)), RuntimeError("boom")])
    result = _run(provider, {"count": 10})
    assert len(result["selection"]["clips"]) == 2
    assert "Segunda rodada" in result["selection"]["selection_notes"]
    assert result["provenance"]["top_up"]["added"] == 0
