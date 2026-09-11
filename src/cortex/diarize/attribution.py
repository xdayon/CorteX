"""Conservative duration accounting over a union of source intervals."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cortex.diarize.service import VoiceTurn

VOICE_ATTRIBUTION_VERSION = 2


class ConfirmedVoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diarization_artifact_id: str | None = None
    speaker: str = Field(min_length=1, max_length=100, pattern=r"\S")
    turns: list[VoiceTurn] = Field(min_length=1)

    @model_validator(mode="after")
    def speaker_exists(self) -> ConfirmedVoice:
        if self.speaker not in {turn.speaker for turn in self.turns}:
            raise ValueError("Voz confirmada não existe nos turnos da diarização")
        return self


def source_interval_union(
    ranges: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    intervals = []
    for start, end in ranges:
        if (
            isinstance(start, bool) or isinstance(end, bool)
            or not isinstance(start, (float, int)) or not isinstance(end, (float, int))
            or not math.isfinite(start) or not math.isfinite(end)
            or start < 0 or end <= start
        ):
            raise ValueError("Intervalo de fonte inválido para atribuição de voz")
        intervals.append((float(start), float(end)))
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subject_share_for_ranges(
    turns: list[dict[str, Any]], speaker: str, ranges: Iterable[tuple[float, float]],
) -> float:
    """Count speech once; simultaneous different speakers never confirm the subject."""
    events: dict[float, Counter[str]] = defaultdict(Counter)
    for start, end in source_interval_union(ranges):
        for turn in turns:
            begin, finish = max(start, turn["start"]), min(end, turn["end"])
            if finish > begin:
                events[begin][turn["speaker"]] += 1
                events[finish][turn["speaker"]] -= 1
    active: Counter[str] = Counter()
    speech = subject = 0.0
    previous = 0.0
    for time, changes in sorted(events.items()):
        if active:
            speech += time - previous
            if set(active) == {speaker}:
                subject += time - previous
        active.update(changes)
        active = +active
        previous = time
    return subject / speech if speech else 0.0


def subject_share(turns: list[dict[str, Any]], speaker: str, start: float, end: float) -> float:
    return subject_share_for_ranges(turns, speaker, [(start, end)])
