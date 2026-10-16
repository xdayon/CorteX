from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from asgi_client import ASGITestClient as TestClient
from conftest import FakeTranscriptionEngine

from cortex.analyze.schemas import LoudnessMetrics, VadInterval
from cortex.api import create_app
from cortex.diarize import voice_match
from cortex.diarize.service import DiarizationDocument, VoiceTurn
from cortex.diarize.voice_match import (
    OTHER_LABEL,
    SUBJECT_LABEL,
    VoiceProfile,
    calibrate_threshold,
    label_units,
    merge_turns,
    profile_from_embeddings,
    save_profile,
)
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact
from cortex.domain.store import DomainStore
from cortex.ingest.ffprobe import probe_media
from cortex.jobs import JobStore
from cortex.library import Episode, EpisodeLibrary
from cortex.suggest.provider import SuggestionProviderResult
from cortex.suggest.service import _selection_units, selection_unit_spans
from cortex.transcribe.schemas import TranscriptSegment, TranscriptWord
from cortex.worker import process_next

from test_workflow_runs import _config


def _words(spec):
    return [{"start": s, "end": e, "word": w, "probability": 1.0} for w, s, e in spec]


def test_selection_unit_spans_match_selection_units():
    transcript = {"segments": [
        {"start": 0, "end": 12, "text": "", "words": _words([
            ("Olá", 0.0, 0.5), ("mundo,", 0.5, 1.0), ("isso", 1.0, 1.5), ("segue,", 1.5, 2.2),
            ("hum", 2.3, 2.5), ("depois", 3.4, 4.0), ("fim.", 4.0, 5.0)])},
        {"start": 6, "end": 9, "text": "sem palavras", "words": []},
        {"start": 9, "end": 10, "text": "hum", "words": []},
    ]}
    units = _selection_units(transcript, voice=None, primary_subject="Dayon")
    assert selection_unit_spans(transcript) == [(u["start"], u["end"]) for u in units]
    assert len(units) >= 3


def test_label_units_fills_short_units_from_nearest_and_merges_turns():
    spans = [(0.0, 4.0), (4.0, 4.5), (4.6, 9.0), (9.0, 9.4), (9.5, 12.0), (12.0, 18.0)]
    scores = [0.8, 0.1, 0.1, 0.9, 0.7, 0.05]
    labels = label_units(spans, scores, 0.37)
    # unit 1 (0.5 s) copies unit 0 (nearest long) even though its own score is low;
    # unit 3 (0.4 s) copies unit 4 even though its own score is high
    assert labels == [True, True, False, True, True, False]
    turns = merge_turns(spans, labels)
    assert [(t.speaker, t.start, t.end) for t in turns] == [
        (SUBJECT_LABEL, 0.0, 4.5), (OTHER_LABEL, 4.6, 9.0),
        (SUBJECT_LABEL, 9.0, 12.0), (OTHER_LABEL, 12.0, 18.0),
    ]


def test_label_units_requires_one_reliable_unit():
    with pytest.raises(ValueError):
        label_units([(0.0, 0.5)], [0.9], 0.37)


def test_calibrate_threshold_uses_middle_of_best_plateau_and_default_without_negatives():
    assert calibrate_threshold([0.8, 0.7, 0.9], [0.1, 0.2, 0.3], 0.37) == pytest.approx(0.5)
    # one overlapping negative: best balanced accuracy sits between the clusters it still separates
    assert 0.2 < calibrate_threshold([0.8, 0.7, 0.4], [0.1, 0.2, 0.45], 0.37) < 0.8
    assert calibrate_threshold([0.8], [], 0.37) == 0.37


def test_profile_from_embeddings_uses_leave_one_out_scores():
    rng = np.random.default_rng(1)
    base = rng.normal(size=16)
    positives = np.array([base + rng.normal(scale=0.2, size=16) for _ in range(6)])
    negatives = np.array([rng.normal(size=16) for _ in range(6)])
    positives /= np.linalg.norm(positives, axis=1, keepdims=True)
    negatives /= np.linalg.norm(negatives, axis=1, keepdims=True)
    voiceprint, threshold = profile_from_embeddings(positives, negatives, 0.37)
    assert np.linalg.norm(voiceprint) == pytest.approx(1.0)
    assert (positives @ voiceprint).min() > threshold > (negatives @ voiceprint).max()


def _episode_with_confirmed_voice(config, domain, short_mp4: Path):
    project = domain.create_project("Episódio")
    stored = config.paths.projects_dir / project.id / "source" / "episode.mp4"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(short_mp4.read_bytes())
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="episode.mp4",
        stored_path=str(stored.relative_to(config.paths.data_dir)),
        sha256=hashlib.sha256(stored.read_bytes()).hexdigest(),
        size_bytes=stored.stat().st_size, probe=probe_media(config.render.ffprobe, stored),
    ))
    turns = [VoiceTurn(start=0, end=10, speaker="SPEAKER_00"),
             VoiceTurn(start=10, end=20, speaker="SPEAKER_01"),
             VoiceTurn(start=20, end=24, speaker="SPEAKER_00"),
             VoiceTurn(start=22, end=30, speaker="SPEAKER_01")]
    document = DiarizationDocument(
        engine="pyannote-community-1", engine_version="x", requested_device="cpu",
        effective_device="cpu", elapsed_seconds=1, turns=turns,
    )
    path = config.paths.projects_dir / project.id / "diar.json"
    path.write_text(document.model_dump_json())
    artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="diarization", path=str(path), input_hash="h",
        metadata={"source_asset_id": source.id, "source_sha256": source.sha256},
    ))
    library = EpisodeLibrary(config, domain)
    library.save(Episode(
        id="ep1", title="Ep", project_ids=[project.id],
        subject_reference={"diarization_artifact_id": artifact.id, "speaker": "SPEAKER_00"},
    ))
    library.save(Episode(id="ep2", title="Sem voz", project_ids=[project.id]))
    return project, source, artifact


def _fake_embed(calls):
    def runner(config, command, *, audio, spans, **_kwargs):
        calls.append((command, list(spans)))
        subject = [1.0, 0.0, 0.0]
        other = [0.0, 1.0, 0.0]
        # first 10 s speaker is SPEAKER_00; the clean negative turn starts at 10 s
        embeddings = [subject if a < 10 else other for a, _b in spans]
        return {"model_repo": "pyannote/x", "model_revision": "rev1", "embeddings": embeddings,
                "mean": subject}
    return runner


def test_profile_build_and_api(tmp_path: Path, short_mp4: Path, monkeypatch):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    _episode_with_confirmed_voice(config, domain, short_mp4)
    calls: list = []
    monkeypatch.setattr(voice_match, "run_voice_runner", _fake_embed(calls))
    monkeypatch.setattr(voice_match, "extract_normalized_audio",
                        lambda *a, **k: SimpleNamespace(path=tmp_path / "a.wav"))
    client = TestClient(create_app(config))

    assert client.get("/api/v1/voice/profile").json() == {"exists": False}
    assert client.post("/api/v1/voice/profile", json={"episode_id": "ep2"}).status_code == 409
    assert client.post("/api/v1/voice/profile", json={"episode_id": "nope"}).status_code == 404

    response = client.post("/api/v1/voice/profile", json={"episode_id": "ep1"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] and body["episode_id"] == "ep1" and body["speaker"] == "SPEAKER_00"
    # clean subject turn: 0-10 (cropped to 6 s); 20-24 overlaps SPEAKER_01 and is excluded
    assert body["seconds"] == 6.0
    assert 0.0 < body["threshold"] < 1.0
    assert body["calibration"]["method"] == "turns" and body["calibration"]["n_neg"] == 1
    command, spans = calls[0]
    assert command == "embed" and spans == [(0.0, 6.0), (10.0, 16.0)]
    stored = VoiceProfile.model_validate_json((tmp_path / "voice" / "profile.json").read_text())
    assert stored.source["negative_ranges"] == [[10.0, 16.0]]
    assert stored.model_revision == "rev1"
    assert client.get("/api/v1/voice/profile").json()["exists"] is True


def test_runner_failure_surfaces_as_error(tmp_path: Path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setenv("CORTEX_DIARIZATION_PYTHON", sys.executable)
    with pytest.raises(RuntimeError, match="reconhecimento de voz falhou"):
        voice_match.run_voice_runner(
            config, "embed", audio=tmp_path / "x.wav", spans=[(0.0, 4.0)], workdir=tmp_path / "w",
            stem="s", timeout=60, progress_cb=lambda *_: None, should_cancel=lambda: False,
        )


class _CapturingProvider:
    def __init__(self):
        self.system_context = None

    def generate(self, prompt, schema, *, should_cancel, system_context=None):
        del prompt, schema
        assert not should_cancel()
        self.system_context = system_context
        clip = {"title": "Ideia", "headlines": ["Uma ideia"], "pieces": [{"from_id": 0, "to_id": 1}],
                "emphasis": [], "score": 8, "reason": "ok", "teaser_ids": [], "warnings": []}
        return SuggestionProviderResult(
            document={"selection_notes": "t", "clips": [clip]},
            provenance={"provider": "claude_code_cli", "model": "fixture"},
        )


def _run_pipeline(tmp_path, short_mp4, monkeypatch, provider):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Episódio")
    stored = config.paths.projects_dir / project.id / "source" / "episode.mp4"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(short_mp4.read_bytes())
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="episode.mp4",
        stored_path=str(stored.relative_to(config.paths.data_dir)),
        sha256=hashlib.sha256(stored.read_bytes()).hexdigest(),
        size_bytes=stored.stat().st_size, probe=probe_media(config.render.ffprobe, stored),
    ))
    interval = VadInterval(start=0.1, end=1.8, duration=1.7, start_sample=1600, end_sample=28800)
    monkeypatch.setattr("cortex.analyze.service.detect_speech", lambda _a, _r: ([interval], "t"))
    monkeypatch.setattr("cortex.analyze.service.measure_loudness", lambda _f, _p: LoudnessMetrics(
        integrated_lufs=-20, loudness_range_lu=2, true_peak_dbfs=-3, threshold_lufs=-30,
        engine="t"))
    client = TestClient(create_app(config))
    response = client.post(f"/api/v1/projects/{project.id}/runs", json={
        "source_asset_id": source.id, "count": 10, "minimum_seconds": 40, "maximum_seconds": 120})
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    jobs = JobStore(config.paths.database)
    words = [TranscriptWord(start=0.0, end=3.0, word="Primeira fala.", probability=1.0),
             TranscriptWord(start=3.5, end=48.0, word="Segunda fala.", probability=1.0)]
    engine = FakeTranscriptionEngine(duration_seconds=48, segments=[TranscriptSegment(
        id=0, start=0.0, end=48.0, text="x", avg_logprob=-0.1, no_speech_prob=0.0, words=words)])
    for _ in range(3):
        assert process_next(config, jobs, domain, engine, provider)
    return config, domain, source, run_id


def _profile():
    return VoiceProfile(embedding=[1.0, 0.0], threshold=0.4, model_repo="r", model_revision="v",
                        source={"episode_id": "e"})


def test_worker_injects_confirmed_voice_and_selection_sees_labels(tmp_path, short_mp4, monkeypatch):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    save_profile(_config(tmp_path), _profile())
    seen = {}

    def runner(config, command, *, spans, **_kwargs):
        seen["command"], seen["spans"] = command, list(spans)
        return {"model_repo": "r", "model_revision": "v", "scores": [0.1, 0.9]}

    monkeypatch.setattr(voice_match, "run_voice_runner", runner)
    provider = _CapturingProvider()
    config, domain, source, run_id = _run_pipeline(tmp_path, short_mp4, monkeypatch, provider)
    run = domain.get_workflow_run(run_id)
    assert run.status == "ready_for_review", run.error or run.message
    assert seen["command"] == "score" and len(seen["spans"]) == 2
    voice = run.brief["confirmed_voice"]
    assert voice["source"] == "voice_match" and voice["speaker"] == SUBJECT_LABEL
    assert voice["threshold"] == 0.4
    assert [t["speaker"] for t in voice["turns"]] == [OTHER_LABEL, SUBJECT_LABEL]
    assert run.brief["voice_match"]["status"] == "ok"
    assert "O: Primeira fala." in provider.system_context and "D: Segunda fala." in provider.system_context
    artifact = domain.get_stage_artifact(voice["diarization_artifact_id"])
    assert artifact.metadata["method"] == "voice_match" and artifact.metadata["source_sha256"] == source.sha256
    assert artifact.metadata["effective_device"] == "cpu"
    assert artifact.metadata["profile_hash"] == _profile().content_hash()


def test_worker_records_skip_without_profile(tmp_path, short_mp4, monkeypatch):
    monkeypatch.setattr(voice_match, "run_voice_runner",
                        lambda *a, **k: pytest.fail("runner must not run without a profile"))
    provider = _CapturingProvider()
    _config_, domain, _source, run_id = _run_pipeline(tmp_path, short_mp4, monkeypatch, provider)
    run = domain.get_workflow_run(run_id)
    assert run.status == "ready_for_review", run.error
    assert "confirmed_voice" not in run.brief
    assert run.brief["voice_match"]["status"] == "skipped" and run.brief["voice_match"]["reason"]
    assert "D: " not in provider.system_context


def test_worker_records_runner_failure_and_continues(tmp_path, short_mp4, monkeypatch):
    save_profile(_config(tmp_path), _profile())

    def runner(*_args, **_kwargs):
        raise RuntimeError("modelo ausente")

    monkeypatch.setattr(voice_match, "run_voice_runner", runner)
    provider = _CapturingProvider()
    _config_, domain, _source, run_id = _run_pipeline(tmp_path, short_mp4, monkeypatch, provider)
    run = domain.get_workflow_run(run_id)
    assert run.status == "ready_for_review"
    assert "confirmed_voice" not in run.brief
    assert run.brief["voice_match"] == {"status": "failed", "reason": "modelo ausente"}


def test_calibration_units_use_unambiguous_units_away_from_voiceprint_crops():
    turns = [VoiceTurn(start=0, end=100, speaker="A"), VoiceTurn(start=100, end=200, speaker="B"),
             VoiceTurn(start=150, end=160, speaker="A")]
    spans = [(float(i), float(i) + 2.0) for i in range(0, 200, 3)] + [(50.0, 50.5)]
    subject, other = voice_match._calibration_units(spans, turns, "A", exclude=[(0.0, 10.0)])
    assert all(a >= 10.0 and b - a >= 0.8 for a, b in subject + other)
    assert all(b <= 100 for a, b in subject) and all(a >= 100 for a, b in other)
    # units overlapping A inside B's turn are ambiguous (< 80% one voice)
    assert not any(150 <= a < 160 for a, b in other + subject if b > 150 and a < 160 and a + 2 > 160)
    assert len(subject) <= voice_match.CALIBRATION_MAX_UNITS_PER_CLASS
