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
    # Max seconds a J/L-cut audio boundary may drift from its video boundary
    # (Gate 4). Slower-paced profiles tolerate wider offsets: at a
    # contemplative rhythm a held or leading voice reads as natural emphasis,
    # while a dynamic/fast cut needs A/V to stay tight or the join reads as
    # a sync error rather than a deliberate J/L-cut.
    max_jl_offset: float


PROFILES: dict[str, EditingProfile] = {
    # "dynamic" is the dry-cut ("corte seco") profile: aggressive, fast,
    # no sentence-pause preservation, no crossfade — every removed pause is
    # a hard video cut. It is the default profile (see resolve_profile).
    "dynamic": EditingProfile(
        name="dynamic", silence_threshold=0.25, pre_roll=0.05, post_roll=0.08,
        boundary_radius=0.12, crossfade=0.0, rhetorical_ceiling=0.0,
        min_removed_pause=0.10, max_cuts_per_30s=40, max_jl_offset=0.35,
    ),
    "balanced": EditingProfile(
        name="balanced", silence_threshold=1.10, pre_roll=0.12, post_roll=0.21,
        boundary_radius=0.30, crossfade=0.060, rhetorical_ceiling=1.65,
        min_removed_pause=0.42, max_cuts_per_30s=5, max_jl_offset=0.50,
    ),
    "contemplative": EditingProfile(
        name="contemplative", silence_threshold=1.65, pre_roll=0.16, post_roll=0.34,
        boundary_radius=0.34, crossfade=0.080, rhetorical_ceiling=2.45,
        min_removed_pause=0.55, max_cuts_per_30s=3, max_jl_offset=0.70,
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
    # Dry-cut ("dynamic") is the default rhythm: no requested profile, "auto",
    # or an unknown name all resolve to it. Explicit balanced/contemplative
    # requests are honored as-is; the old words-per-second heuristic no
    # longer decides the default.
    name = (requested or "auto").strip().lower()
    name = _PROFILE_ALIASES.get(name, name)
    return PROFILES.get(name, PROFILES["dynamic"])
