from __future__ import annotations

import json
from pathlib import Path

import pytest
from asgi_client import ASGITestClient

from cortex.analyze.camera_map import CameraGroup, CameraMapDocument, CameraShot
from cortex.analyze.scene_suggestions import (
    CLIP_MARGIN, HOOK_SECONDS, MAX_INSERT, MIN_INSERT, SPACING, detect_question, output_starts,
    subject_only, suggest_scenes,
)
from cortex.api import create_app
from cortex.diarize.service import DiarizationDocument, VoiceTurn
from cortex.domain.models import StageArtifact, TranscriptArtifact
from cortex.edit.schemas import (
    EditPlanDiagnostics, EditPlanDocument, EditQualityReport, EditSegment, EditTransition,
)
from cortex.transcribe.schemas import EngineInfo, TranscriptDocument, TranscriptSegment, TranscriptWord

from test_camera_map import _setup, video  # noqa: F401 - module fixture

D, OTHERS = "protagonista", "outros"
ROLES = {0: "subject", 1: "interviewers", 2: "wide", 3: "ignore"}


def _turns(*spans):
    return [VoiceTurn(start=a, end=b, speaker=s) for a, b, s in spans]


TURNS = _turns((0, 15.8, D), (16.0, 19.8, OTHERS), (20.0, 120.0, D), (120.0, 125.0, OTHERS), (125.0, 300.0, D))


def _camera_map(shots) -> CameraMapDocument:
    return CameraMapDocument(
        algorithm_version="1", project_id="p", source_asset_id="s", source_sha256="x",
        duration_seconds=300.0, keyframe_count=10,
        groups=[CameraGroup(id=i, sample_time=0, airtime_seconds=1, airtime_share=0.25, shot_count=1,
                            median_shot_seconds=1, faces=f, thumbnail_path="t.jpg")
                for i, f in ((0, 2), (1, 1), (2, 3), (3, 0))],
        shots=[CameraShot(start=a, end=b, group=g) for a, b, g in shots], voice_artifact_id="v",
    )


SHOTS = [(0, 100, 0), (100, 112, 1), (112, 150, 0), (150, 160, 2), (160, 200, 1), (200, 300, 0)]


def _words(start: float, end: float, step: float = 0.5, every: int = 8) -> list[TranscriptWord]:
    words, t, n = [], start, 0
    while t + 0.4 <= end:
        n += 1
        words.append(TranscriptWord(start=t, end=t + 0.4, word="fim." if n % every == 0 else "palavra",
                                    probability=1.0))
        t += step
    return words


def _plan(segments, **kwargs) -> EditPlanDocument:
    return EditPlanDocument(
        project_id="p", source_asset_id="s", transcript_artifact_id="t", analysis_artifact_id="a",
        input_hash="h", clip_start=segments[0].start, clip_end=segments[-1].end, profile="dynamic",
        segments=segments, timeline_duration_seconds=sum(s.end - s.start for s in segments),
        diagnostics=EditPlanDiagnostics(profile="dynamic", waveform_used=True, candidate_pauses=0, cuts=0,
                                        saved_seconds=0, crossfade=0, vad_used=True),
        quality=EditQualityReport(passed=True, issues=[], profile="dynamic", degraded=False), **kwargs,
    )


def test_interval_helpers_and_output_starts():
    allowed = subject_only(TURNS, D)
    assert allowed[0] == (-0.3, 15.6) and allowed[1] == (20.2, 119.6)
    hard = EditTransition(video="cut", audio="hard_concat", duration=0.0, kind="hard")
    fade = EditTransition(video="fade", audio="tri", duration=0.2, kind="crossfade")
    plan = _plan([EditSegment(start=0, end=4, timeline_order=0, transition=hard),
                  EditSegment(start=10, end=14, timeline_order=1, transition=fade),
                  EditSegment(start=20, end=24, timeline_order=2)])
    assert output_starts(plan) == pytest.approx([0.0, 4.0, 7.8])


def test_suggestions_follow_the_placement_rules():
    plan = _plan([EditSegment(start=20, end=60, timeline_order=0, piece_index=0),
                  EditSegment(start=70, end=95, timeline_order=1, piece_index=0)])
    words = _words(16, 60) + _words(70, 95)
    result = suggest_scenes(plan, _camera_map(SHOTS), ROLES, TURNS, words, D)
    suggestions = result.suggestions
    assert 2 <= len(suggestions) <= plan.timeline_duration_seconds // 15
    outs = [s.output_start for s in suggestions]
    assert outs == sorted(outs) and outs[0] >= HOOK_SECONDS
    assert all(b - a >= SPACING for a, b in zip(outs, outs[1:]))
    windows = {"reaction": [(100.3, 111.7), (160.3, 199.7)], "wide": [(150.3, 159.7)]}
    used = []
    for s in suggestions:
        segment = next(seg for seg in plan.segments if seg.start <= s.anchor < seg.end)
        assert segment.start + 1.0 <= s.anchor and s.anchor + s.duration <= segment.end - 1.0
        assert MIN_INSERT <= s.duration <= MAX_INSERT
        assert any(a <= s.source_start and s.source_start + s.duration <= b for a, b in windows[s.kind])
        # Never footage the clip itself shows, never the same footage twice.
        assert all(s.source_start + s.duration <= seg.start - CLIP_MARGIN or s.source_start >= seg.end + CLIP_MARGIN
                   for seg in plan.segments)
        assert not any(s.source_start < b and s.source_start + s.duration > a for a, b in used)
        used.append((s.source_start, s.source_start + s.duration))
        assert s.id.startswith("sc-") and s.reason and s.alternatives
    kinds = [s.kind for s in suggestions]
    assert "reaction" in kinds and ("wide" in kinds or len(kinds) < 3)
    assert result.question is not None and 16.0 <= result.question.start < result.question.end <= 20.0
    again = suggest_scenes(plan, _camera_map(SHOTS), ROLES, TURNS, words, D)
    assert again == result


def test_no_suggestion_over_another_camera_or_another_voice():
    plan = _plan([EditSegment(start=20, end=60, timeline_order=0)])
    words = _words(20, 60)
    # The base camera is the interviewers' one: nothing to cut away from.
    shots = [(0, 100, 1), (100, 112, 1), (150, 160, 2), (200, 300, 0)]
    assert suggest_scenes(plan, _camera_map(shots), ROLES, TURNS, words, D).suggestions == []
    # Someone else speaks over the whole clip: no cutaway over their voice.
    talking = _turns((0, 120, OTHERS), (125, 300, D))
    assert suggest_scenes(plan, _camera_map(SHOTS), ROLES, talking, words, D).suggestions == []


def test_question_is_the_end_of_the_others_turn_right_before_the_answer():
    words = [TranscriptWord(start=a, end=a + 0.4, word=w, probability=1.0) for a, w in (
        (10.0, "Bom."), (11.0, "Mas"), (13.5, "e"), (14.0, "aí,"), (15.0, "o"), (16.5, "que"), (17.5, "você"),
        (18.5, "acha?"), (20.5, "Eu"), (21.0, "acho."))]
    plan = _plan([EditSegment(start=20.4, end=22, timeline_order=0)])
    turns = _turns((0, 9.5, D), (10.0, 19.0, OTHERS), (20.3, 30, D))
    question = detect_question(plan, words, turns, D)
    assert question.end == pytest.approx(18.9) and question.end - question.start <= 6.0
    # Starts at the sentence that opens after a pause, within the last 6 s.
    assert question.start == 13.5 and question.text == "e aí, o que você acha?"
    assert detect_question(plan, words, _turns((0, 30, D)), D) is None
    far = _plan([EditSegment(start=26, end=28, timeline_order=0)])
    assert detect_question(far, words, turns, D) is None


def _persist(tmp_path: Path, domain, source, name: str, stage: str, payload: str, **metadata) -> StageArtifact:
    path = tmp_path / f"{name}.json"
    path.write_text(payload)
    return domain.create_stage_artifact(StageArtifact(
        project_id=source.project_id, stage=stage, schema_version=1, path=str(path), input_hash=name,
        metadata=metadata,
    ))


def test_api_scenes_statuses_and_reviews(tmp_path, video):  # noqa: F811
    config, _jobs, domain, source = _setup(tmp_path, video)
    client = ASGITestClient(create_app(config))
    episode = next(e for e in client.get("/api/v1/episodes").json() if source.project_id in e["project_ids"])
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(TranscriptDocument(
        language="pt", duration_seconds=300.0,
        engine=EngineInfo(model="f", requested_device="cpu", effective_device="cpu", requested_compute_type="int8",
                          effective_compute_type="int8", batch_size=1, vad=True),
        segments=[TranscriptSegment(id=0, start=16, end=95, text="x", avg_logprob=0, no_speech_prob=0, words=_words(16, 60) + _words(70, 95))],
    ).model_dump_json())
    transcript = domain.create_transcript_artifact(TranscriptArtifact(
        project_id=source.project_id, source_asset_id=source.id, path=str(transcript_path), audio_sha256="a",
        engine="f", model="f", device="cpu", compute_type="int8", language="pt", vad=True, batch_size=1,
        duration_seconds=300.0,
    ))
    plan = _plan([EditSegment(start=20, end=60, timeline_order=0), EditSegment(start=70, end=95, timeline_order=1)])
    plan = plan.model_copy(update={"project_id": source.project_id, "source_asset_id": source.id,
                                   "transcript_artifact_id": transcript.id})
    plan_artifact = _persist(tmp_path, domain, source, "plan", "edit_plan", plan.model_dump_json())
    url = f"/api/v1/episodes/{episode['id']}/scenes?edit_plan_artifact_id={plan_artifact.id}"
    assert client.get(url).json()["status"] == "needs_camera_map"
    assert client.get(f"/api/v1/episodes/{episode['id']}/scenes?edit_plan_artifact_id=nope").status_code == 404

    voice = _persist(tmp_path, domain, source, "voice", "diarization", DiarizationDocument(
        engine="voice-match", engine_version="1", requested_device="cpu", effective_device="cpu",
        elapsed_seconds=1, turns=TURNS).model_dump_json())
    camera = _camera_map(SHOTS).model_copy(update={"project_id": source.project_id, "voice_artifact_id": voice.id})
    camera_artifact = _persist(tmp_path, domain, source, "camera", "camera_map", camera.model_dump_json())
    assert client.get(url).json()["status"] == "needs_roles"
    roles = client.request("PUT", f"/api/v1/episodes/{episode['id']}/cameras/roles", json={
        "artifact_id": camera_artifact.id, "roles": {str(k): v for k, v in ROLES.items()}})
    assert roles.status_code == 200

    body = client.get(url).json()
    assert body["status"] == "ok" and body["source_asset_id"] == source.id and body["suggestions"]
    assert body["question"]["end"] <= 20.0 and body["review"] == {"accepted": 0, "rejected": 0, "approval_rate": None}
    first, *rest = [s["id"] for s in body["suggestions"]]
    reviews = f"/api/v1/episodes/{episode['id']}/scene-reviews"
    put = client.request("PUT", reviews, json={"decisions": {first: "accepted", **{r: "rejected" for r in rest[:1]}}})
    assert put.json()["accepted"] == 1 and put.json()["rejected"] == len(rest[:1])
    assert client.get(url).json()["decisions"][first] == "accepted"
    cleared = client.request("PUT", reviews, json={"decisions": {first: None}}).json()
    assert cleared["accepted"] == 0
    assert client.request("PUT", reviews, json={"decisions": {first: "maybe"}}).status_code == 422
    assert json.loads(Path(camera_artifact.path).read_text())["voice_artifact_id"] == voice.id
