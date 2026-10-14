"""Fail-safe validation for edit plans before rendering.

Known bug fixed here: a proposed cut boundary landing inside a word used to
abort the whole plan (severity=error, code=boundary_inside_word). We
re-anchor the boundary to the nearest safe gap (outside every guarded word
span) within 2x the profile's boundary_radius, recording a
severity=warning code=boundary_snapped issue.

Per-cut fail-safe (Requirement R3): when no safe snap point exists for an
*interior* cut boundary, we no longer degrade the whole plan to the full,
uncut clip — we drop that single cut instead (re-joining the two
neighbouring segments, i.e. keeping that pause) and record a
severity=warning code=cut_dropped_unsafe_boundary issue. Only a plan that
ends up with no speech-safe timeline left at all is an error
(code=empty_timeline). VAD no longer forbids cut placement here either
(Requirement R2) — it is diagnostic only, upstream in the planner.
"""

from __future__ import annotations

from cortex.analyze.schemas import VadInterval
from cortex.edit.boundary import EnergyTrack
from cortex.edit.profiles import EditingProfile
from cortex.transcribe.schemas import TranscriptWord

_BOUNDARY_GUARD = 0.015


def _word_intervals(words: list[TranscriptWord], guard: float = 0.035) -> list[tuple[float, float]]:
    return [(w.start - guard, w.end + guard) for w in words]


def _word_containing(words: list[TranscriptWord], t: float) -> TranscriptWord | None:
    for word in words:
        if word.start + _BOUNDARY_GUARD < t < word.end - _BOUNDARY_GUARD:
            return word
    return None


def validate_edit_plan(
    segments: list[dict],
    words: list[TranscriptWord],
    profile: EditingProfile,
    clip_start: float,
    clip_end: float,
    energy: EnergyTrack | None = None,
    vad_intervals: list[VadInterval] | None = None,
) -> tuple[list[dict], dict]:
    """Validate (and, if needed, repair) an edit plan's segment boundaries.

    Returns (segments, report). ``segments`` is the input list unless a
    boundary had to be snapped or the plan degraded to the full clip.
    """
    issues: list[dict] = []
    if not segments:
        issues.append({"severity": "error", "code": "empty_timeline"})
        return segments, {"passed": False, "issues": issues, "profile": profile.name, "degraded": False}

    # Forbidden zones are guarded word spans only — VAD is diagnostic, not a
    # veto, at this stage too (Requirement R2).
    forbidden = _word_intervals(words)

    repaired = [dict(segment) for segment in segments]
    # drop_after[i] marks the cut between repaired[i] and repaired[i + 1] as
    # unsafe-and-unsnappable: that single cut is dropped (the two segments
    # are re-joined) instead of degrading the whole plan (Requirement R3).
    drop_after = [False] * len(repaired)

    for index, segment in enumerate(repaired):
        start = float(segment.get("start", 0))
        end = float(segment.get("end", 0))
        if end <= start:
            issues.append({"severity": "error", "code": "invalid_segment", "segment": index})
            continue
        if end - start < 0.50:
            issues.append({"severity": "error", "code": "segment_too_short", "segment": index})

        for boundary_key, boundary_name, boundary in (
            ("start", "in", start), ("end", "out", end)
        ):
            offending = _word_containing(words, boundary)
            if offending is None:
                continue

            candidate = None
            if energy is not None:
                snapped = energy.quiet_boundary(boundary, profile.boundary_radius * 2, forbidden)
                if _word_containing(words, snapped) is None and round(snapped, 3) != round(boundary, 3):
                    candidate = snapped

            if candidate is not None:
                segment[boundary_key] = candidate
                issues.append({
                    "severity": "warning",
                    "code": "boundary_snapped",
                    "segment": index,
                    "boundary": boundary_name,
                    "word": offending.word,
                    "snapped_from": round(boundary, 3),
                    "snapped_to": candidate,
                })
                continue

            if boundary_name == "out" and index < len(repaired) - 1:
                drop_after[index] = True
            elif boundary_name == "in" and index > 0:
                drop_after[index - 1] = True
            else:
                # An envelope boundary (very first start / very last end) —
                # there is no neighbour to re-join with. Extend outward to
                # fully include the offending word rather than truncate it;
                # this never collapses the plan.
                if boundary_key == "start":
                    segment[boundary_key] = round(max(0.0, offending.start - _BOUNDARY_GUARD), 3)
                else:
                    segment[boundary_key] = round(offending.end + _BOUNDARY_GUARD, 3)
                issues.append({
                    "severity": "warning",
                    "code": "boundary_extended_unsafe",
                    "segment": index,
                    "boundary": boundary_name,
                    "word": offending.word,
                    "time": round(boundary, 3),
                })
                continue

            issues.append({
                "severity": "warning",
                "code": "cut_dropped_unsafe_boundary",
                "segment": index,
                "boundary": boundary_name,
                "word": offending.word,
                "time": round(boundary, 3),
            })

    if any(drop_after):
        joined: list[dict] = []
        for index, segment in enumerate(repaired):
            if joined and drop_after[index - 1]:
                joined[-1]["end"] = segment["end"]
                for key in ("video_end", "audio_end"):
                    if key in segment:
                        joined[-1][key] = segment[key]
                joined[-1]["transition"] = segment.get("transition")
                if joined[-1]["transition"] is None:
                    joined[-1].pop("transition", None)
            else:
                joined.append(dict(segment))
        repaired = joined

    if not repaired or all(
        float(s.get("end", 0)) <= float(s.get("start", 0)) for s in repaired
    ):
        issues.append({"severity": "error", "code": "empty_timeline"})
        return repaired, {"passed": False, "issues": issues, "profile": profile.name, "degraded": False}

    for index, segment in enumerate(repaired):
        segment["timeline_order"] = index

    total_duration = sum(
        max(0.0, float(s.get("end", 0)) - float(s.get("start", 0))) for s in repaired
    )
    max_cuts = max(1, int(total_duration / 30.0 * profile.max_cuts_per_30s) + 1)
    if len(repaired) - 1 > max_cuts:
        issues.append({
            "severity": "warning",
            "code": "excessive_cut_density",
            "cuts": len(repaired) - 1,
            "recommended_max": max_cuts,
        })

    passed = not any(issue["severity"] == "error" for issue in issues)
    return repaired, {"passed": passed, "issues": issues, "profile": profile.name, "degraded": False}
