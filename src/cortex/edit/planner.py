from __future__ import annotations

import math

from cortex.analyze.schemas import VadInterval
from cortex.edit.boundary import EnergyTrack, word_containing
from cortex.edit.profiles import EditingProfile
from cortex.transcribe.schemas import TranscriptWord

_JL_OFFSET_EPSILON = 0.001


def word_intervals(words: list[TranscriptWord], guard: float = 0.035) -> list[tuple[float, float]]:
    """Word spans padded by ``guard`` seconds — used to build forbidden zones."""
    return [(w.start - guard, w.end + guard) for w in words]


def _is_sentence_pause(word: TranscriptWord) -> bool:
    return word.word.strip().endswith((".", "!", "?", "…"))


def protect_segment_boundaries(
    segments: list[dict], words: list[TranscriptWord], profile: EditingProfile,
) -> list[dict]:
    """Extend unsafe in/out points so no estimated word is truncated."""
    protected: list[dict] = []
    ordered_words = sorted(words, key=lambda w: w.start)
    for original in segments:
        segment = dict(original)
        start = float(segment["start"])
        end = float(segment["end"])
        overlapping = [w for w in ordered_words if w.end > start and w.start < end]
        if overlapping:
            first = overlapping[0]
            last = overlapping[-1]
            if first.start - start < profile.pre_roll:
                start = max(0.0, min(start, first.start - profile.pre_roll))
            if end - last.end < profile.post_roll:
                end = max(end, last.end + profile.post_roll)
        segment["start"] = round(start, 3)
        segment["end"] = round(end, 3)
        protected.append(segment)
    return protected


def _merge_short_segments(segments: list[dict], minimum: float = 0.72) -> list[dict]:
    if len(segments) < 2:
        return segments
    merged: list[dict] = []
    for segment in segments:
        if segment["end"] - segment["start"] < minimum and merged:
            merged[-1]["end"] = segment["end"]
        else:
            merged.append(dict(segment))
    if len(merged) > 1 and merged[0]["end"] - merged[0]["start"] < minimum:
        merged[1]["start"] = merged[0]["start"]
        merged.pop(0)
    return merged


def plan_safe_segments(
    words: list[TranscriptWord],
    start_second: float,
    end_second: float,
    profile: EditingProfile,
    energy: EnergyTrack | None = None,
    vad_intervals: list[VadInterval] | None = None,
    minimum_total_saving: float = 0.70,
) -> tuple[list[dict], dict]:
    """Build speech-safe keep ranges and return diagnostics for QA/UI."""
    clip_words = sorted(
        (w for w in words if w.end > start_second and w.start < end_second),
        key=lambda w: w.start,
    )
    diagnostics = {
        "profile": profile.name,
        "waveform_used": energy is not None,
        "candidate_pauses": 0,
        "cuts": 0,
        "saved_seconds": 0.0,
        "crossfade": profile.crossfade,
        "vad_used": bool(vad_intervals),
    }
    if len(clip_words) < 3:
        return [{"start": start_second, "end": end_second, "timeline_order": 0}], diagnostics

    start_second = max(0.0, min(start_second, clip_words[0].start - profile.pre_roll))
    end_second = max(end_second, clip_words[-1].end + profile.post_roll)
    forbidden = word_intervals(clip_words)
    for interval in vad_intervals or []:
        if interval.end > interval.start:
            forbidden.append((interval.start, interval.end))

    candidates: list[dict] = []
    for previous, following in zip(clip_words, clip_words[1:]):
        prev_end = previous.end
        next_start = following.start
        gap = next_start - prev_end
        if gap < profile.silence_threshold:
            continue
        diagnostics["candidate_pauses"] += 1

        # A sentence-ending pause often carries meaning. Preserve it unless it
        # is clearly much longer than the profile's natural rhetorical window.
        if _is_sentence_pause(previous) and gap <= profile.rhetorical_ceiling:
            continue

        cut_from = prev_end + profile.post_roll
        cut_to = next_start - profile.pre_roll
        if cut_to - cut_from < profile.min_removed_pause:
            continue

        if energy is not None:
            cut_from = energy.quiet_boundary(cut_from, profile.boundary_radius, forbidden)
            cut_to = energy.quiet_boundary(cut_to, profile.boundary_radius, forbidden)
        if any(a <= cut_from <= b or a <= cut_to <= b for a, b in forbidden):
            # Fail safe: if VAD/word protection covers the proposed join and no
            # quiet alternative exists, preserve the pause.
            continue
        if cut_to - cut_from < profile.min_removed_pause:
            continue

        candidates.append({
            "from": max(start_second, cut_from),
            "to": min(end_second, cut_to),
            "saving": cut_to - cut_from,
        })

    duration = max(0.1, end_second - start_second)
    max_cuts = max(1, math.ceil(duration / 30.0 * profile.max_cuts_per_30s))
    chosen = sorted(candidates, key=lambda c: c["saving"], reverse=True)[:max_cuts]
    chosen.sort(key=lambda c: c["from"])

    if not chosen:
        return [{"start": start_second, "end": end_second, "timeline_order": 0}], diagnostics

    segments: list[dict] = []
    cursor = start_second
    for cut in chosen:
        if cut["from"] > cursor + 0.50:
            segments.append({"start": round(cursor, 3), "end": round(cut["from"], 3)})
        cursor = max(cursor, cut["to"])
    if end_second > cursor + 0.50:
        segments.append({"start": round(cursor, 3), "end": round(end_second, 3)})
    segments = _merge_short_segments(segments)

    if len(segments) < 2:
        return [{"start": start_second, "end": end_second, "timeline_order": 0}], diagnostics

    kept = sum(s["end"] - s["start"] for s in segments)
    saving = duration - kept
    if saving < minimum_total_saving:
        return [{"start": start_second, "end": end_second, "timeline_order": 0}], diagnostics

    diagnostics["cuts"] = len(segments) - 1
    diagnostics["saved_seconds"] = round(saving, 3)
    for index, segment in enumerate(segments):
        segment["timeline_order"] = index
        if index < len(segments) - 1:
            segment["transition"] = {
                "video": "micro_dissolve",
                "audio": "equal_power_crossfade",
                "duration": profile.crossfade,
            }
    return segments, diagnostics


def _forbidden_zones(
    words: list[TranscriptWord], vad_intervals: list[VadInterval] | None, guard: float = 0.035,
) -> list[tuple[float, float]]:
    forbidden = word_intervals(words, guard)
    for interval in vad_intervals or []:
        if interval.end > interval.start:
            forbidden.append((interval.start, interval.end))
    return forbidden


def resolve_jl_cuts(
    segments: list[dict],
    words: list[TranscriptWord],
    vad_intervals: list[VadInterval] | None,
    energy: EnergyTrack | None,
    max_offset: float,
) -> tuple[list[dict], list[dict], dict]:
    """Resolve independent audio cut points (J/L-cuts) at interior boundaries.

    For every boundary between two consecutive segments that already carries
    a transition, search for a quiet, word/VAD-safe point near that
    segment's video out-point within ``max_offset`` seconds — the same
    EnergyTrack.quiet_boundary + forbidden-zone construction used elsewhere
    in this module. The resolved offset shifts *both* the outgoing
    segment's audio_end and the incoming segment's audio_start by the same
    amount, so the audio seconds one segment gains are exactly the seconds
    its neighbor loses (see EditPlanDocument's A/V coverage invariant).

    When no safe point exists in range (dense words/VAD covering the whole
    window), the boundary keeps its original synchronous transition and a
    ``jl_no_safe_boundary`` issue is recorded — never a raised exception,
    consistent with this pipeline's fail-safe/degrade-not-abort policy.
    """
    resolved = [dict(segment) for segment in segments]
    issues: list[dict] = []
    counts = {"j_cuts": 0, "l_cuts": 0, "jl_blocked": 0}

    if max_offset <= 0 or energy is None or len(resolved) < 2:
        for segment in resolved:
            segment.setdefault("audio_start", segment["start"])
            segment.setdefault("audio_end", segment["end"])
        return resolved, issues, counts

    forbidden = _forbidden_zones(words, vad_intervals)

    for index in range(len(resolved) - 1):
        current = resolved[index]
        following = resolved[index + 1]
        transition = current.get("transition")
        if not transition:
            continue

        video_cut = float(current["end"])
        candidate = energy.quiet_boundary(video_cut, max_offset, forbidden)
        safe = (
            word_containing(words, candidate) is None
            and not any(a <= candidate <= b for a, b in forbidden)
        )
        if not safe:
            counts["jl_blocked"] += 1
            issues.append({
                "severity": "info",
                "code": "jl_no_safe_boundary",
                "segment": index,
                "boundary": "out",
                "time": round(video_cut, 3),
            })
            continue

        offset = round(candidate - video_cut, 3)
        offset = max(-max_offset, min(max_offset, offset))
        if abs(offset) < _JL_OFFSET_EPSILON:
            # Already the quietest point on offer, and it coincides with the
            # existing video cut — nothing to gain from a J/L-cut here.
            continue

        new_audio_end = round(video_cut + offset, 3)
        new_audio_start = round(float(following["start"]) + offset, 3)
        current_audio_start = float(current.get("audio_start") or current["start"])
        following_audio_end = float(following.get("audio_end") or following["end"])
        if (
            new_audio_end <= current_audio_start + _JL_OFFSET_EPSILON
            or new_audio_start >= following_audio_end - _JL_OFFSET_EPSILON
        ):
            # The safe point exists but a neighbor segment is too short to
            # absorb the shift without inverting its audio clock.
            counts["jl_blocked"] += 1
            issues.append({
                "severity": "info",
                "code": "jl_no_safe_boundary",
                "segment": index,
                "boundary": "out",
                "time": round(video_cut, 3),
            })
            continue

        current["audio_end"] = new_audio_end
        following["audio_start"] = new_audio_start
        kind = "j_cut" if offset < 0 else "l_cut"
        transition["kind"] = kind
        transition["audio_offset_seconds"] = offset
        counts["j_cuts" if kind == "j_cut" else "l_cuts"] += 1

    for segment in resolved:
        segment.setdefault("audio_start", segment["start"])
        segment.setdefault("audio_end", segment["end"])
    return resolved, issues, counts


def timeline_duration(segments: list[dict], default_crossfade: float = 0.0) -> float:
    total = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in segments)
    if len(segments) < 2:
        return total
    overlap = 0.0
    for segment in segments[:-1]:
        transition = segment.get("transition") or {}
        overlap += max(0.0, float(transition.get("duration", default_crossfade) or 0.0))
    return max(0.0, total - overlap)
