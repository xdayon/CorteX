from __future__ import annotations

import pytest

from cortex.lessons.common import LessonError
from cortex.lessons.spec import Beat, FlowRef, LessonSpec, ManimRef, Shot
from cortex.lessons.timeline import (Timings, flow_request, retime_factor, seconds_used, shot_windows)

DURATIONS = [4, 6, 8, 10]


def test_request_smallest_duration_that_covers():
    assert flow_request("A", 5.38, DURATIONS, 1.1) == 6
    assert flow_request("A", 8.76, DURATIONS, 1.1) == 8
    assert retime_factor(8.76, 0, 8) == pytest.approx(1.095)
    assert seconds_used(6, 5.38) == 5.38


def test_request_error_names_the_shot():
    with pytest.raises(LessonError, match=r"shot S07.*split the beat"):
        flow_request("S07", 11.5, DURATIONS, 1.1)


def _spec(shots):
    beats = [Beat(id=f"B{i}", text="x") for i in range(1, 5)]
    return LessonSpec(slug="t", title="t", narration="n.mp3", beats=beats, shots=shots)


def test_multi_beat_shot_sums_and_pads():
    flow = FlowRef(clip="01")
    shots = [Shot(id="A", beats=["B1"], kind="flow", flow=flow),
             Shot(id="B", beats=["B2", "B3"], kind="flow", flow=flow),
             Shot(id="C", beats=["B4"], kind="manim", manim=ManimRef(scene="s.py", cls="S", render="s.mp4"))]
    timings = Timings(audio_offset=0.8, total=20, scenes={
        "B1": {"start": 0, "duration": 5}, "B2": {"start": 5, "duration": 4}, "B3": {"start": 9, "duration": 4.76},
        "B4": {"start": 13.76, "duration": 6.24}})
    a, b, c = shot_windows(_spec(shots), timings, 0.4)
    assert (a.pad_before, a.pad_after, a.needed) == (0.0, 0.2, 5.2)
    assert (b.start, b.duration, b.needed) == (5, 8.76, 9.16)
    assert (c.pad_before, c.pad_after) == (0.2, 0.0)


def test_spec_rejects_gaps_and_bad_hybrid():
    flow = FlowRef(clip="01")
    with pytest.raises(ValueError, match="cover every beat"):
        _spec([Shot(id="A", beats=["B1", "B3"], kind="flow", flow=flow)])
    with pytest.raises(ValueError, match="transparent"):
        Shot(id="H", beats=["B1"], kind="hybrid", flow=flow,
             manim=ManimRef(scene="s.py", cls="S", render="s.mov"))
    with pytest.raises(ValueError, match="needs 'flow'"):
        Shot(id="F", beats=["B1"], kind="flow")
