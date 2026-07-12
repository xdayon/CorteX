from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from cortex.transcribe.schemas import TranscriptWord


class EditingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    silence_threshold: float
    pre_roll: float
    post_roll: float
    boundary_radius: float
    crossfade: float
    rhetorical_ceiling: float
    min_removed_pause: float
    max_cuts_per_30s: int


PROFILES: dict[str, EditingProfile] = {
    "dynamic": EditingProfile(
        name="dynamic", silence_threshold=0.82, pre_roll=0.10, post_roll=0.15,
        boundary_radius=0.24, crossfade=0.045, rhetorical_ceiling=1.20,
        min_removed_pause=0.32, max_cuts_per_30s=7,
    ),
    "balanced": EditingProfile(
        name="balanced", silence_threshold=1.10, pre_roll=0.12, post_roll=0.21,
        boundary_radius=0.30, crossfade=0.060, rhetorical_ceiling=1.65,
        min_removed_pause=0.42, max_cuts_per_30s=5,
    ),
    "contemplative": EditingProfile(
        name="contemplative", silence_threshold=1.65, pre_roll=0.16, post_roll=0.34,
        boundary_radius=0.34, crossfade=0.080, rhetorical_ceiling=2.45,
        min_removed_pause=0.55, max_cuts_per_30s=3,
    ),
}

_PROFILE_ALIASES = {
    "fast": "dynamic",
    "viral": "dynamic",
    "normal": "balanced",
    "professional": "balanced",
    "deep": "contemplative",
    "reflective": "contemplative",
}


def resolve_profile(
    requested: str | None,
    words: list[TranscriptWord],
    start_second: float,
    end_second: float,
) -> EditingProfile:
    name = (requested or "auto").strip().lower()
    name = _PROFILE_ALIASES.get(name, name)
    if name in PROFILES:
        return PROFILES[name]

    clip_words = [w for w in words if w.end > start_second and w.start < end_second]
    duration = max(0.1, end_second - start_second)
    words_per_second = len(clip_words) / duration
    punctuation = "".join(w.word[-1:] for w in clip_words)
    question_energy = punctuation.count("?") + punctuation.count("!")

    if words_per_second >= 2.85 or question_energy >= 3:
        return PROFILES["dynamic"]
    if words_per_second <= 1.72 and question_energy == 0:
        return PROFILES["contemplative"]
    return PROFILES["balanced"]
