import pytest

from cortex.analyze.schemas import VadInterval
from cortex.edit.boundary import EnergyTrack
from cortex.edit.planner import (
    plan_safe_segments,
    protect_segment_boundaries,
    timeline_duration,
)
from cortex.edit.profiles import PROFILES, resolve_profile
from cortex.edit.quality import validate_edit_plan
from cortex.transcribe.schemas import TranscriptWord


def word(text: str, start: float, end: float) -> TranscriptWord:
    return TranscriptWord(word=text, start=start, end=end, probability=0.99)


def test_balanced_profile_does_not_cut_subsecond_pause() -> None:
    words = [
        word("uma", 0.2, 0.45),
        word("ideia", 1.25, 1.55),
        word("forte", 1.6, 1.9),
    ]

    segments, diagnostics = plan_safe_segments(
        words, 0.0, 2.2, PROFILES["balanced"]
    )

    assert len(segments) == 1
    assert diagnostics["cuts"] == 0


def test_waveform_moves_join_into_quiet_region_and_keeps_transition() -> None:
    words = [
        word("Isso", 0.20, 0.48),
        word("muda", 0.52, 0.80),
        word("tudo", 2.70, 3.02),
        word("agora", 3.08, 3.42),
    ]
    rms = [0.35] * 220
    rms[48:134] = [0.002] * 86
    energy = EnergyTrack(origin=0.0, frame_seconds=0.02, rms=rms)

    segments, diagnostics = plan_safe_segments(
        words, 0.0, 3.7, PROFILES["balanced"], energy
    )

    assert len(segments) == 2
    assert segments[0]["end"] > 0.80
    assert segments[1]["start"] < 2.70
    assert diagnostics["waveform_used"] is True
    assert diagnostics["cuts"] == 1
    assert segments[0]["transition"]["duration"] > 0


def test_rhetorical_pause_is_preserved() -> None:
    words = [
        word("E", 0.1, 0.2),
        word("acabou.", 0.25, 0.65),
        word("Depois", 2.05, 2.35),
        word("voltou", 2.4, 2.7),
    ]

    segments, diagnostics = plan_safe_segments(
        words, 0.0, 3.0, PROFILES["balanced"]
    )

    assert len(segments) == 1
    assert diagnostics["cuts"] == 0


def test_vad_region_blocks_cut_when_no_safe_boundary_exists() -> None:
    words = [
        word("primeiro", 0.1, 0.5),
        word("ponto", 0.55, 0.8),
        word("segundo", 2.4, 2.8),
    ]
    speech = [
        VadInterval(
            start=0.0,
            end=3.0,
            duration=3.0,
            start_sample=0,
            end_sample=48_000,
        )
    ]

    segments, diagnostics = plan_safe_segments(
        words,
        0.0,
        3.0,
        PROFILES["dynamic"],
        vad_intervals=speech,
    )

    assert len(segments) == 1
    assert diagnostics["vad_used"] is True
    assert diagnostics["cuts"] == 0


def test_boundary_protection_extends_cut_inside_words() -> None:
    words = [word("inteira", 1.0, 1.5), word("frase", 1.55, 1.95)]
    profile = PROFILES["balanced"]

    protected = protect_segment_boundaries(
        [{"start": 1.2, "end": 1.8, "timeline_order": 0}], words, profile
    )
    repaired, quality = validate_edit_plan(
        protected,
        words,
        profile,
        clip_start=0.0,
        clip_end=2.3,
    )

    assert repaired[0]["start"] <= 0.88
    assert repaired[0]["end"] >= 2.16
    assert quality["passed"] is True
    assert quality["degraded"] is False


def test_timeline_duration_accounts_for_crossfades() -> None:
    segments = [
        {"start": 0, "end": 5, "transition": {"duration": 0.06}},
        {"start": 10, "end": 15, "transition": {"duration": 0.08}},
        {"start": 20, "end": 25},
    ]

    assert timeline_duration(segments) == pytest.approx(14.86)


def test_auto_profile_respects_delivery_speed() -> None:
    fast = [word(str(index), index * 0.2, index * 0.2 + 0.12) for index in range(30)]
    slow = [word(str(index), index * 0.7, index * 0.7 + 0.2) for index in range(8)]

    assert resolve_profile("auto", fast, 0, 6).name == "dynamic"
    assert resolve_profile("auto", slow, 0, 6).name == "contemplative"
