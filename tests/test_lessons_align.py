from __future__ import annotations

import pytest

from cortex.lessons.align import BeatSpan, beat_spans, cut_timings
from cortex.lessons.common import LessonError
from cortex.lessons.spec import Beat

BEATS = [Beat(id="S01", text="a"), Beat(id="S02", text="b"), Beat(id="S03", text="c"), Beat(id="S04", hold=4.0)]


def test_cut_rule_with_long_first_word_and_hold():
    spans = [
        BeatSpan(0.0, 0.14, 9.74),
        BeatSpan(10.56, 10.74, 14.98),   # short first word: onset = start
        BeatSpan(15.0, 16.0, 25.0),      # long first word (1.0 s): onset = end - 0.35 = 15.65
        None,
    ]
    t = cut_timings(spans, BEATS, narration_end=26.0, lead_in=0.8)
    assert t["S01"] == {"start": 0.0, "duration": 11.16}   # max(9.79, 10.36) + 0.8
    assert t["S02"]["start"] == 11.16
    assert t["S03"]["start"] == pytest.approx(16.25)       # max(15.03, 15.45) + 0.8
    assert t["S02"]["duration"] == pytest.approx(5.09)
    assert t["S03"]["duration"] == pytest.approx(11.25)    # ends at 26.0 + 0.8 + 0.7 = 27.5
    assert t["S04"] == {"start": 27.5, "duration": 4.0}


def test_previous_word_end_floors_the_cut():
    spans = [BeatSpan(0.0, 0.1, 5.0), BeatSpan(5.1, 5.2, 9.0)]
    t = cut_timings(spans, BEATS[:2], 9.5, 0.8)
    assert t["S02"]["start"] == 5.85                        # max(5.05, 4.9) + 0.8


def test_silent_beat_must_trail():
    beats = [Beat(id="a", text="x"), Beat(id="b", hold=1.0), Beat(id="c", text="y")]
    with pytest.raises(LessonError):
        cut_timings([BeatSpan(0, 0.1, 1), None, BeatSpan(2, 2.1, 3)], beats, 3.0, 0.8)


def test_beat_spans_match_words_and_fail_on_missing():
    words = [(0.0, 0.2, "Hello,"), (0.2, 0.5, "world"), (1.0, 1.2, "second"), (1.2, 1.6, "part.")]
    spans = beat_spans(words, ["hello world", "second part"])
    assert spans[1].first_start == 1.0 and spans[1].last_end == 1.6
    with pytest.raises(LessonError):
        beat_spans(words, ["hello world", "nothing matches here"])
