"""Pure timing arithmetic: shot windows, crossfade pads, Flow durations to request, retime factors."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from cortex.lessons.common import LessonError
from cortex.lessons.spec import LessonSpec

RETIME_WARN = (0.8, 1.25)


class SceneTime(BaseModel):
    start: float
    duration: float


class Timings(BaseModel):
    source: str = ""
    audio_offset: float
    total: float
    scenes: dict[str, SceneTime]


def load_timings(path: Path) -> Timings:
    if not path.exists():
        raise LessonError(f"missing {path}; run `align` first")
    return Timings.model_validate(json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class ShotWindow:
    id: str
    start: float
    duration: float
    pad_before: float
    pad_after: float

    @property
    def needed(self) -> float:
        """Seconds of picture the segment must hold, crossfade pads included."""
        return round(self.duration + self.pad_before + self.pad_after, 3)


def shot_windows(spec: LessonSpec, timings: Timings, xfade: float) -> list[ShotWindow]:
    """One window per shot (multi-beat shot = first beat start, summed durations); 0.2 s pad on each inner side."""
    pad = xfade / 2
    out: list[ShotWindow] = []
    for i, shot in enumerate(spec.shots):
        try:
            scenes = [timings.scenes[b] for b in shot.beats]
        except KeyError as exc:
            raise LessonError(f"timings.json has no beat {exc.args[0]} (shot {shot.id})") from exc
        duration = round(sum(s.duration for s in scenes), 2)
        out.append(ShotWindow(shot.id, scenes[0].start, duration,
                              pad if i > 0 else 0.0, pad if i < len(spec.shots) - 1 else 0.0))
    return out


def flow_request(shot_id: str, needed: float, durations: list[float], max_retime: float) -> float:
    """Smallest Flow duration d with d * max_retime >= needed."""
    for d in sorted(durations):
        if d * max_retime + 1e-9 >= needed:
            return float(d)
    raise LessonError(
        f"shot {shot_id}: needs {needed:.2f}s of picture but the longest Flow clip ({max(durations):g}s x "
        f"{max_retime:g}) covers {max(durations) * max_retime:.2f}s; split the beat"
    )


def seconds_used(requested: float, needed: float) -> float:
    return round(min(requested, needed), 3)


def retime_factor(needed: float, clip_in: float, clip_out: float) -> float:
    """>1 slows the clip down to fill the window."""
    return needed / (clip_out - clip_in)


def retime_outside_range(factor: float) -> bool:
    return not RETIME_WARN[0] <= factor <= RETIME_WARN[1]
