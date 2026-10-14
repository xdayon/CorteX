from __future__ import annotations

import math

from cortex.analyze.schemas import VadInterval
from cortex.edit.boundary import EnergyTrack, word_containing
from cortex.edit.profiles import EditingProfile
from cortex.transcribe.schemas import TranscriptWord

_JL_OFFSET_EPSILON = 0.001
# Matches quality.py's segment_too_short error floor — a filler cut that
# would leave less than this on either side is skipped instead of carving
# off an unrenderable sliver (see remove_fillers).
_MIN_KEPT_PIECE_SECONDS = 0.5


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


def build_transition(profile: EditingProfile) -> dict:
    """Build the transition dict for a boundary planned under ``profile``.

    A profile with no crossfade (the dry-cut "dynamic" profile) gets a hard
    video cut instead of the old "micro_dissolve" label, so the render
    filter-graph can pick a clean, click-safe hard-cut join instead of a
    zero-duration dissolve (see render.service, Requirement R6).
    """
    if profile.crossfade <= 0:
        return {"video": "cut", "audio": "hard_concat", "duration": 0.0, "kind": "hard"}
    return {
        "video": "micro_dissolve", "audio": "equal_power_crossfade",
        "duration": profile.crossfade, "kind": "crossfade",
    }


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
    # Forbidden zones for cut placement are guarded word intervals only.
    # VAD used to also veto cuts here, but VAD speech intervals routinely
    # cover a whole window even where word-level gaps show plenty of real
    # pauses (VAD is coarse voice-activity detection, not a per-word
    # timestamp), so it silently blocked almost every cut. VAD stays
    # diagnostic only (``vad_used`` below) — see Requirement R2.
    forbidden = word_intervals(clip_words)

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
            segment["transition"] = build_transition(profile)
    return segments, diagnostics


def _forbidden_zones(
    words: list[TranscriptWord], vad_intervals: list[VadInterval] | None, guard: float = 0.035,
) -> list[tuple[float, float]]:
    # ``vad_intervals`` is accepted for call-site compatibility but no longer
    # contributes to the forbidden set (Requirement R2) — VAD is diagnostic
    # only, guarded word spans are the sole veto on boundary placement.
    del vad_intervals
    return word_intervals(words, guard)


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


def merge_touching_segments(segments: list[dict], gap_threshold: float = 0.05) -> list[dict]:
    """Merge segments that overlap or touch (gap below ``gap_threshold``).

    Used after planning each ``approximate_edl`` semantic range independently
    (Requirement R5): non-overlapping ranges plan into separate segment
    lists that may still abut once padded, and those should read as one
    continuous kept range rather than a spurious zero-content cut.
    """
    if len(segments) < 2:
        return [dict(segment) for segment in segments]
    merged = [dict(segments[0])]
    for segment in segments[1:]:
        gap = float(segment["start"]) - float(merged[-1]["end"])
        if gap < gap_threshold:
            merged[-1]["end"] = segment["end"]
            for key in ("video_end", "audio_end"):
                if key in segment:
                    merged[-1][key] = segment[key]
            merged[-1]["transition"] = segment.get("transition")
            if merged[-1]["transition"] is None:
                merged[-1].pop("transition", None)
        else:
            merged.append(dict(segment))
    return merged


def absorb_short_segments(
    segments: list[dict], minimum: float = 0.6, max_bridge: float = 1.0,
) -> list[dict]:
    """Resolve kept pieces too short to render (quality floor is 0.5 s).

    The AI tighten pass may keep a lone short phrase ("Sim.") between two
    dropped ones. Join it to the nearest neighbour when the gap is at most
    ``max_bridge`` seconds (keeping that small gap); otherwise drop it —
    an isolated sub-second word reads as a glitch, not as speech.
    """
    result = [dict(segment) for segment in segments]
    index = 0
    while index < len(result) and len(result) > 1:
        segment = result[index]
        if float(segment["end"]) - float(segment["start"]) >= minimum:
            index += 1
            continue
        gap_before = (
            float(segment["start"]) - float(result[index - 1]["end"]) if index > 0 else float("inf")
        )
        gap_after = (
            float(result[index + 1]["start"]) - float(segment["end"])
            if index + 1 < len(result) else float("inf")
        )
        if min(gap_before, gap_after) > max_bridge:
            result.pop(index)
            continue
        if gap_before <= gap_after:
            previous = result[index - 1]
            previous["end"] = segment["end"]
            for key in ("video_end", "audio_end"):
                if key in segment:
                    previous[key] = segment[key]
            if segment.get("transition") is not None:
                previous["transition"] = segment["transition"]
            else:
                previous.pop("transition", None)
            result.pop(index)
            index = max(0, index - 1)
        else:
            following = result[index + 1]
            following["start"] = segment["start"]
            for key in ("video_start", "audio_start"):
                if key in segment:
                    following[key] = segment[key]
            result.pop(index)
    return result


def remove_fillers(
    segments: list[dict],
    words: list[TranscriptWord],
    fillers: list,
    profile: EditingProfile,
    energy: EnergyTrack | None,
    min_confidence: float = 0.8,
) -> tuple[list[dict], int]:
    """Cut out high-confidence fillers (dynamic profile only, Requirement R4).

    A filler is removed only when both its in- and out-cut points can be
    snapped to a quiet, word-safe point (``EnergyTrack.quiet_boundary``)
    outside the guarded intervals of the *neighbouring* words — the filler's
    own word span is excluded from the forbidden set so the cut can land
    inside it — and the kept material left on either side of the cut is at
    least ``_MIN_KEPT_PIECE_SECONDS`` long, matching quality.py's
    ``segment_too_short`` floor: a filler right next to a segment edge would
    otherwise carve off an unrenderable sliver. Otherwise the filler is left
    untouched. This runs after ``_merge_short_segments`` has already
    stabilized the segment list, so it is the last split performed —
    nothing downstream merges segments back together and accidentally
    re-inserts the removed audio.
    """
    if not fillers or energy is None:
        return segments, 0
    ordered_words = sorted(words, key=lambda w: w.start)
    eligible = sorted(
        (f for f in fillers if f.confidence >= min_confidence),
        key=lambda f: f.start,
    )
    removed = 0
    result: list[dict] = []
    for segment in segments:
        seg_start = float(segment["start"])
        seg_end = float(segment["end"])
        candidates = [f for f in eligible if seg_start < f.start and f.end < seg_end]
        cursor = seg_start
        pieces: list[dict] = []
        carried = {"piece_index": segment["piece_index"]} if "piece_index" in segment else {}
        for filler in candidates:
            if filler.start <= cursor:
                continue
            other_words = [w for w in ordered_words if w.end <= filler.start or w.start >= filler.end]
            forbidden = word_intervals(other_words)
            cut_from = energy.quiet_boundary(filler.start, profile.boundary_radius, forbidden)
            cut_to = energy.quiet_boundary(filler.end, profile.boundary_radius, forbidden)
            if (
                cut_to - cut_from < 0.02
                or cut_from - cursor < _MIN_KEPT_PIECE_SECONDS
                or seg_end - cut_to < _MIN_KEPT_PIECE_SECONDS
                or any(a <= cut_from <= b or a <= cut_to <= b for a, b in forbidden)
            ):
                continue
            pieces.append({**carried, "start": round(cursor, 3), "end": round(cut_from, 3)})
            cursor = round(cut_to, 3)
            removed += 1
        if not pieces and cursor == seg_start:
            result.append(dict(segment))
            continue
        pieces.append({**carried, "start": round(cursor, 3), "end": round(seg_end, 3)})
        for piece in pieces[:-1]:
            piece["transition"] = build_transition(profile)
        if segment.get("transition") is not None:
            pieces[-1]["transition"] = segment["transition"]
        result.extend(pieces)
    return result, removed


def timeline_duration(segments: list[dict], default_crossfade: float = 0.0) -> float:
    total = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in segments)
    if len(segments) < 2:
        return total
    overlap = 0.0
    for segment in segments[:-1]:
        transition = segment.get("transition") or {}
        overlap += max(0.0, float(transition.get("duration", default_crossfade) or 0.0))
    return max(0.0, total - overlap)


def limit_timeline_duration(
    segments: list[dict], words: list[TranscriptWord], maximum_seconds: float,
    profile: EditingProfile, vad_intervals: list[VadInterval] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Keep a prefix within the ceiling, ending outside padded word/VAD spans.

    This runs after scene/boundary repairs and before J/L offsets. It never
    relies on the model's estimated duration, nor on the source envelope length
    when silence removal has already made the actual timeline short enough.
    """
    if not math.isfinite(maximum_seconds) or maximum_seconds <= 0:
        raise ValueError("maximum_seconds deve ser positivo e finito")
    if timeline_duration(segments) <= maximum_seconds:
        return segments, []
    forbidden = _forbidden_zones(words, vad_intervals)
    kept: list[dict] = []
    for original in segments:
        segment = dict(original)
        if timeline_duration([*kept, segment]) <= maximum_seconds:
            kept.append(segment)
            continue
        start = float(segment["start"])
        # Include the incoming transition's overlap in the remaining budget.
        prefix = timeline_duration([*kept, {"start": start, "end": start}])
        ceiling = min(float(segment["end"]), start + maximum_seconds - prefix)
        candidates = [ceiling]
        candidates.extend(word.end + profile.post_roll for word in words
                          if start < word.end <= ceiling)
        candidates.extend(interval.end + profile.post_roll for interval in vad_intervals or []
                          if start < interval.end <= ceiling)
        safe = [math.floor(value * 10000) / 10000 for value in candidates
                if start + .5 <= value <= ceiling]
        safe = [value for value in safe if not any(left < value < right for left, right in forbidden)]
        if safe:
            segment["end"] = max(safe)
            if "video_end" in segment:
                segment["video_end"] = segment["end"]
            if "audio_end" in segment:
                segment["audio_end"] = segment["end"]
            kept.append(segment)
        break
    if not kept:
        raise ValueError(
            f"Não há final de fala seguro dentro de {maximum_seconds:g}s. "
            "Escolha um trecho menor ou gere outra sugestão; nenhuma palavra foi truncada."
        )
    kept[-1].pop("transition", None)
    for index, segment in enumerate(kept):
        segment["timeline_order"] = index
    if timeline_duration(kept) > maximum_seconds + 1e-6:
        raise ValueError("plano excedeu maximum_seconds após limitar a duração")
    return kept, [{
        "severity": "warning", "code": "maximum_duration_trimmed",
        "segment": len(kept) - 1, "boundary": "out",
        "snapped_from": float(segments[-1]["end"]), "snapped_to": float(kept[-1]["end"]),
        "delta_ms": round((float(kept[-1]["end"]) - float(segments[-1]["end"])) * 1000, 3),
    }]
