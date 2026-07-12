"""Fail-safe validation for edit plans before rendering.

Known bug fixed here: a proposed cut boundary landing inside a word used to
abort the whole plan (severity=error, code=boundary_inside_word). Instead we
now re-anchor the boundary to the nearest safe gap (outside every word and
VAD interval) within 2x the profile's boundary_radius, recording a
severity=warning code=boundary_snapped issue. Only when no safe point exists
in that radius do we degrade the plan to the full, uncut clip — the job
itself never fails because of a boundary problem.
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

    forbidden = _word_intervals(words)
    for interval in vad_intervals or []:
        if interval.end > interval.start:
            forbidden.append((interval.start, interval.end))

    repaired = [dict(segment) for segment in segments]
    degraded = False

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

            if candidate is None:
                degraded = True
                issues.append({
                    "severity": "warning",
                    "code": "boundary_unsafe_degraded",
                    "segment": index,
                    "boundary": boundary_name,
                    "word": offending.word,
                    "time": round(boundary, 3),
                })
            else:
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

    if degraded:
        repaired = [{"start": round(clip_start, 3), "end": round(clip_end, 3), "timeline_order": 0}]

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
    return repaired, {"passed": passed, "issues": issues, "profile": profile.name, "degraded": degraded}
