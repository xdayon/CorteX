from __future__ import annotations

import pytest

from cortex.render.auto_framing import SceneFramingOverride, resolve_auto_framing
from cortex.render.schemas import RenderFramingSettings, RenderFramingSettingsPatch
from test_auto_framing import _plan, _speakers
from test_face_crop import _document, _face

SECOND = 1_000_000


def _voice(turns, speaker="dayon"):
    return {"speaker": speaker, "turns": [
        {"start": a, "end": b, "speaker": who} for a, b, who in turns]}


def _two_shot(seconds=25, step=.5, left_x=.15, right_x=.65, left_w=.1, right_w=.1, missing=()):
    times = [round(i * step + .1, 3) for i in range(int(seconds / step))]
    frames = []
    for time in times:
        faces = [_face(left_x, left_w, "person_left"), _face(right_x, right_w, "person_right")]
        faces = [face for face in faces if (face["track_id"], int(time)) not in missing]
        frames.append({"time": time, "shot_type": "two_shot", "faces": faces})
    return _document(frames)


def _single(seconds=6, x=.55, w=.12, step=.5, absent_every=0):
    frames = []
    for index in range(int(seconds / step)):
        absent = absent_every and index % absent_every == 0
        frames.append({"time": round(index * step + .1, 3), "shot_type": "close",
                       "faces": [] if absent else [_face(x, w, "person")]})
    return _document(frames)


def _resolve(plan, faces, **kwargs):
    return resolve_auto_framing(
        plan, faces, _speakers([]), source_width=1920, source_height=1080,
        width=1080, height=1920, fps=30, policy="face_first", **kwargs)


def _center(span):
    return span.crop_x + span.crop_width / 2


def test_single_person_shot_crops_on_the_face_ignoring_context_signals():
    plan = _plan([(0, 6 * SECOND)])  # two_shot + context intent must be ignored
    spans = _resolve(plan, _single())
    assert len(spans) == 1 and spans[0].mode == "face_crop"
    assert spans[0].reason == "face_first_single_person" and spans[0].track_id == "person"
    assert abs(_center(spans[0]) - (.55 + .06) * 1920) < 2


def test_two_shot_follows_voice_and_ignores_short_interjection():
    plan = _plan([(0, 25 * SECOND)])
    voice = _voice([(0, 10, "dayon"), (10, 15, "other"), (15, 25, "dayon"), (20, 21, "other")])
    spans = _resolve(plan, _two_shot(), confirmed_voice=voice, primary_side="left")
    assert [(s.track_id, s.source_start_us // SECOND, s.source_end_us // SECOND) for s in spans] == [
        ("person_left", 0, 10), ("person_right", 10, 15), ("person_left", 15, 25)]
    assert all(s.mode == "face_crop" and s.reason == "face_first_voice_turn" for s in spans)
    assert _center(spans[0]) < _center(spans[1])
    assert sum(s.source_end_us - s.source_start_us for s in spans) == 25 * SECOND


def test_short_subspan_is_absorbed_by_neighbor():
    plan = _plan([(0, 12 * SECOND)])
    voice = _voice([(0, 6, "dayon"), (6, 7.8, "other"), (7.8, 12, "dayon")])
    spans = _resolve(plan, _two_shot(12), confirmed_voice=voice, primary_side="left")
    assert [s.track_id for s in spans] == ["person_left"]


def test_without_voice_always_primary_side():
    plan = _plan([(0, 10 * SECOND)])
    for side, track in (("left", "person_left"), ("right", "person_right")):
        spans = _resolve(plan, _two_shot(10), primary_side=side)
        assert [(s.track_id, s.reason) for s in spans] == [(track, "face_first_primary_side")]


def test_without_primary_side_uses_dominant_face():
    plan = _plan([(0, 10 * SECOND)])
    spans = _resolve(plan, _two_shot(10, right_w=.2), confirmed_voice=_voice([(0, 10, "dayon")]))
    assert [(s.track_id, s.reason) for s in spans] == [("person_right", "face_first_dominant_face")]


def test_target_missing_in_more_than_20_percent_falls_back_to_blur():
    plan = _plan([(0, 10 * SECOND)])
    missing = {("person_left", second) for second in range(0, 4)}
    spans = _resolve(plan, _two_shot(10, missing=missing), primary_side="left")
    assert len(spans) == 1 and spans[0].mode == "blurred_background"
    assert spans[0].reason == "face_first_target_missing"
    spans = _resolve(plan, _single(10, absent_every=2))
    assert spans[0].reason == "face_first_target_missing"


def test_manual_override_wins():
    plan = _plan([(0, 10 * SECOND)])
    plan.shots[0].visual_origin = "primary_in_place"
    faces = _two_shot(10)
    spans = _resolve(plan, faces, primary_side="left",
                     overrides=[SceneFramingOverride(scene_index=0, target="right")])
    assert [(s.track_id, s.reason) for s in spans] == [("person_right", "manual_right")]
    spans = _resolve(plan, faces, primary_side="left",
                     overrides=[SceneFramingOverride(scene_index=0, target="full")])
    assert spans[0].mode == "blurred_background" and spans[0].reason == "manual_full_frame"


def test_default_policy_is_unchanged():
    plan = _plan([(0, 10 * SECOND)])
    spans = resolve_auto_framing(plan, _two_shot(10), _speakers([]), source_width=1920,
                                 source_height=1080, width=1080, height=1920, fps=30)
    assert spans[0].reason == "source_context_preserved"


def test_schema_accepts_speaker_face_and_primary_side():
    settings = RenderFramingSettings(mode="speaker_face", primary_side="right")
    assert settings.mode == "speaker_face" and settings.primary_side == "right"
    patch = RenderFramingSettingsPatch(mode="speaker_face", primary_side="left")
    assert patch.primary_side == "left"
    with pytest.raises(ValueError):
        RenderFramingSettings(primary_side="center")


def test_render_request_requires_camera_plan_for_speaker_face():
    from cortex.api import RenderRequest
    from test_auto_framing import _render_settings
    with pytest.raises(ValueError, match="speaker_face"):
        RenderRequest(edit_plan_artifact_id="edit", render_settings=_render_settings("speaker_face"))
