from __future__ import annotations

from types import SimpleNamespace

import pytest

from cortex.render import face_focus
from cortex.render.face_focus import (
    FaceFocusError,
    decide_focus,
    resolve_face_focus,
    sample_times,
    split_spans,
)


def face(x, width=0.1, height=0.2, score=0.9, y=0.2):
    return {"x": x, "y": y, "width": width, "height": height, "score": score}


def seg(order, start, end):
    return SimpleNamespace(timeline_order=order, video_start=start, video_end=end)


def decide(samples, *, fraction=0.5, manual=0.5, side=None):
    return decide_focus(
        samples, crop_width_fraction=fraction, manual_focus_x=manual, primary_side=side,
    )


def test_no_faces_uses_manual_focus():
    assert decide([[]], manual=0.4) == (0.4, "no_faces")
    assert decide([], manual=0.4) == (0.4, "no_faces")


def test_small_or_low_score_faces_are_ignored():
    poster = face(0.1, height=0.05)
    weak = face(0.8, score=0.5)
    assert decide([[poster, weak]], manual=0.5) == (0.5, "no_faces")


def test_all_faces_fit_uses_union_center():
    focus, reason = decide([[face(0.2), face(0.4)]], fraction=0.5)
    assert reason == "all_faces"
    assert focus == pytest.approx(0.35)


def test_primary_side_picks_leftmost_or_rightmost():
    samples = [[face(0.05), face(0.85)]]
    assert decide(samples, fraction=0.5, side="left") == (pytest.approx(0.25), "primary_side")
    assert decide(samples, fraction=0.5, side="right") == (pytest.approx(0.75), "primary_side")


def test_largest_face_without_side():
    samples = [[face(0.05, width=0.1, height=0.15), face(0.8, width=0.15, height=0.3)]]
    focus, reason = decide(samples, fraction=0.5)
    assert reason == "largest_face"
    assert focus == pytest.approx(0.75)


def test_samples_aggregate_by_mean_center():
    samples = [[face(0.0), face(0.85)], [face(0.02), face(0.85)]]
    focus, reason = decide(samples, fraction=0.5, side="left")
    assert reason == "primary_side"
    assert focus == pytest.approx(0.25)  # mean center 0.06 clamped to crop half


def test_focus_is_clamped_to_crop_bounds():
    assert decide([[face(0.9)]], fraction=0.4)[0] == pytest.approx(0.8)
    assert decide([[face(0.0)]], fraction=0.4)[0] == pytest.approx(0.2)
    assert decide([[face(0.4)]], fraction=1.0)[0] == pytest.approx(0.5)


def test_split_spans_inside_boundary_and_multiple_cuts():
    segments = [seg(0, 10.0, 20.0), seg(1, 30.0, 34.0)]
    spans = split_spans(segments, [12.0, 15.0, 30.0, 33.0])
    assert spans == [
        (0, 0, 10.0, 12.0),
        (0, 1, 12.0, 15.0),
        (0, 2, 15.0, 20.0),
        (1, 3, 30.0, 33.0),
        (1, 4, 33.0, 34.0),
    ]


def test_cut_at_segment_boundary_does_not_split():
    spans = split_spans([seg(0, 10.0, 12.0), seg(1, 12.0, 14.0)], [12.0])
    assert spans == [(0, 0, 10.0, 12.0), (1, 1, 12.0, 14.0)]


def test_sample_times_midpoints_and_cut_margin():
    times = sample_times(
        [(10.0, 10.3), (20.0, 26.0)], 10.0, 30.0, start_is_cut=True, end_is_cut=True,
    )
    assert times[0] == pytest.approx(10.2)  # midpoint 10.15 pushed 0.2 s away from the cut
    assert 23.0 in times
    assert len(times) == 3
    assert all(10.2 <= t <= 29.8 for t in times)


def test_sample_times_caps_at_three_spread_over_spans():
    spans = [(float(i), i + 0.5) for i in range(0, 20, 2)]
    times = sample_times(spans, 0.0, 20.0, start_is_cut=False, end_is_cut=False)
    assert len(times) == 3
    assert times[0] == pytest.approx(0.25)
    assert times[-1] == pytest.approx(18.25)


def test_sample_times_tiny_scene_uses_scene_midpoint():
    assert sample_times([(5.0, 5.3)], 5.0, 5.3, start_is_cut=True, end_is_cut=True) == [5.15]


@pytest.fixture
def fake_pipeline(monkeypatch):
    calls = {"scdet": 0, "frames": [], "detect": 0}

    def fake_run(command, **kwargs):
        calls["scdet"] += 1
        stderr = "lavfi.scd.score: 40.0, lavfi.scd.time: 5.0\n"
        return SimpleNamespace(returncode=0, stderr=stderr, stdout="")

    def fake_extract(ffmpeg, source, timestamp):
        calls["frames"].append(timestamp)
        return timestamp

    def fake_detect(session, frame):
        calls["detect"] += 1
        return [face(0.1 if frame < 105.0 else 0.8)]

    monkeypatch.setattr(face_focus.subprocess, "run", fake_run)
    monkeypatch.setattr(face_focus, "_extract_frame_rgb", fake_extract)
    monkeypatch.setattr(face_focus, "detect_faces", fake_detect)
    monkeypatch.setattr(face_focus, "load_session", lambda: object())
    return calls


def _resolve(tmp_path, **overrides):
    kwargs = dict(
        ffmpeg="ffmpeg", source_path=tmp_path / "s.mp4", source_sha256="abc",
        segments=[seg(0, 100.0, 110.0)], scene_threshold=10.0, cache_dir=tmp_path,
        crop_width_fraction=0.4, manual_focus_x=0.5, primary_side=None,
    )
    kwargs.update(overrides)
    return resolve_face_focus(**kwargs)


def test_resolve_per_scene_and_caches(tmp_path, fake_pipeline):
    # scdet times are window-relative: 5.0 -> absolute cut at 105.0
    spans = _resolve(tmp_path)
    assert [(s.scene_index, s.source_start, s.source_end) for s in spans] == [
        (0, 100.0, 105.0), (1, 105.0, 110.0),
    ]
    assert spans[0].focus_x == pytest.approx(0.2) and spans[0].reason == "all_faces"
    assert spans[1].focus_x == pytest.approx(0.8)
    assert spans[0].face_count == 1
    assert all(len(s.sample_times) == 3 for s in spans)
    assert fake_pipeline["scdet"] == 1
    detections = fake_pipeline["detect"]
    assert detections == 6

    again = _resolve(tmp_path)
    assert again == spans
    assert fake_pipeline["scdet"] == 1
    assert fake_pipeline["detect"] == detections


def test_detection_failure_is_explicit(tmp_path, fake_pipeline, monkeypatch):
    def broken(session, frame):
        raise face_focus.FaceDetectionError("modelo quebrado")

    monkeypatch.setattr(face_focus, "detect_faces", broken)
    with pytest.raises(FaceFocusError):
        _resolve(tmp_path)


def test_cancellation(tmp_path, fake_pipeline):
    with pytest.raises(face_focus.FaceFocusCancelled):
        _resolve(tmp_path, should_cancel=lambda: True)
