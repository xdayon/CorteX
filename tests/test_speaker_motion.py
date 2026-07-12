from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from cortex.analyze.speaker_motion import compensated_motion, select_speaker, smooth_scores
from cortex.analyze.speaker_schemas import SpeakerSegment


def _face() -> dict:
    return {
        "x": 0.2, "y": 0.1, "width": 0.6, "height": 0.8,
        "landmarks": {
            "right_eye": (0.38, 0.35), "left_eye": (0.62, 0.35), "nose_tip": (0.5, 0.5),
            "right_mouth_corner": (0.4, 0.68), "left_mouth_corner": (0.6, 0.68),
        },
    }


def test_compensated_motion_ignores_uniform_brightness_shift() -> None:
    first = np.full((100, 100, 3), 80, dtype=np.uint8)
    second = np.full((100, 100, 3), 120, dtype=np.uint8)
    assert compensated_motion(first, second, _face(), _face()) == pytest.approx(0.0)


def test_compensated_motion_detects_local_mouth_change() -> None:
    first = np.full((100, 100, 3), 80, dtype=np.uint8)
    second = first.copy()
    second[67:72, 45:55] = 220
    assert compensated_motion(first, second, _face(), _face()) > 0.08


def test_smoothing_removes_single_sample_spike() -> None:
    assert smooth_scores([0.0, 1.0, 0.0]) == [0.0, 0.0, 0.0]


def test_select_speaker_requires_threshold_and_margin() -> None:
    assert select_speaker({"left": 0.02})[0] == "unknown"
    assert select_speaker({"left": 0.4, "right": 0.39})[0] == "overlap"
    assert select_speaker({"left": 0.4, "right": 0.1})[:2] == ("speaker", "left")


def test_segment_rejects_inverted_interval_and_invalid_speaker_state() -> None:
    with pytest.raises(ValidationError):
        SpeakerSegment(start_us=10, end_us=5, state="unknown", confidence=0.5, evidence="test")
    with pytest.raises(ValidationError):
        SpeakerSegment(
            start_us=0, end_us=5, state="speaker", speaker_track_id=None,
            confidence=0.5, evidence="test",
        )
