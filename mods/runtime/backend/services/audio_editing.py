"""Professional audio-aware edit planning for spoken-word clips.

The transcription tells us *what* was said.  This module adds the missing
signal-level information needed to decide *where* it is safe to cut.  It keeps
the implementation dependency-free (stdlib + ffmpeg) so it also works in the
small native podcli runtime.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import math
import os
import subprocess
import sys
from typing import Iterable, Optional


@dataclass(frozen=True)
class EditingProfile:
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
        "dynamic", 0.82, 0.10, 0.15, 0.24, 0.045, 1.20, 0.32, 7
    ),
    "balanced": EditingProfile(
        "balanced", 1.10, 0.12, 0.21, 0.30, 0.060, 1.65, 0.42, 5
    ),
    "contemplative": EditingProfile(
        "contemplative", 1.65, 0.16, 0.34, 0.34, 0.080, 2.45, 0.55, 3
    ),
}


@dataclass
class EnergyTrack:
    """RMS energy sampled in fixed-duration frames."""

    origin: float
    frame_seconds: float
    rms: list[float]

    @property
    def end(self) -> float:
        return self.origin + len(self.rms) * self.frame_seconds

    def energy_at(self, timestamp: float) -> float:
        if not self.rms:
            return 1.0
        idx = int((timestamp - self.origin) / self.frame_seconds)
        idx = max(0, min(idx, len(self.rms) - 1))
        return self.rms[idx]

    def quiet_boundary(
        self,
        target: float,
        radius: float,
        forbidden: Iterable[tuple[float, float]],
    ) -> float:
        """Return the best low-energy, non-speech point near ``target``."""
        if not self.rms or radius <= 0:
            return target

        forbidden = list(forbidden)
        lo = max(self.origin, target - radius)
        hi = min(self.end, target + radius)
        lo_idx = max(0, int((lo - self.origin) / self.frame_seconds))
        hi_idx = min(len(self.rms) - 1, int((hi - self.origin) / self.frame_seconds))
        if hi_idx < lo_idx:
            return target

        local = self.rms[lo_idx : hi_idx + 1]
        reference = _percentile(local, 0.90) or max(local, default=1.0) or 1.0
        best_t = target
        best_score = float("inf")

        for idx in range(lo_idx, hi_idx + 1):
            t = self.origin + (idx + 0.5) * self.frame_seconds
            if any(a <= t <= b for a, b in forbidden):
                continue
            energy_score = min(2.0, self.rms[idx] / reference)
            distance_score = abs(t - target) / max(radius, self.frame_seconds)
            score = energy_score + distance_score * 0.32
            if score < best_score:
                best_score = score
                best_t = t

        return round(best_t, 3)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * max(0.0, min(1.0, fraction))))
    return ordered[idx]


def resolve_profile(
    requested: Optional[str],
    words: list[dict],
    start_second: float,
    end_second: float,
) -> EditingProfile:
    name = (requested or "auto").strip().lower()
    aliases = {
        "fast": "dynamic",
        "viral": "dynamic",
        "normal": "balanced",
        "professional": "balanced",
        "deep": "contemplative",
        "reflective": "contemplative",
    }
    name = aliases.get(name, name)
    if name in PROFILES:
        return PROFILES[name]

    clip_words = [
        w for w in words
        if float(w.get("end", 0)) > start_second
        and float(w.get("start", 0)) < end_second
    ]
    duration = max(0.1, end_second - start_second)
    words_per_second = len(clip_words) / duration
    punctuation = "".join(str(w.get("word", ""))[-1:] for w in clip_words)
    question_energy = punctuation.count("?") + punctuation.count("!")

    if words_per_second >= 2.85 or question_energy >= 3:
        return PROFILES["dynamic"]
    if words_per_second <= 1.72 and question_energy == 0:
        return PROFILES["contemplative"]
    return PROFILES["balanced"]


def extract_energy_track(
    media_path: str,
    start_second: float,
    end_second: float,
    ffmpeg: Optional[str] = None,
    sample_rate: int = 16000,
    frame_ms: int = 20,
) -> Optional[EnergyTrack]:
    """Decode only the requested clip window and calculate normalized RMS."""
    if not media_path or not os.path.exists(media_path) or end_second <= start_second:
        return None
    ffmpeg = ffmpeg or os.environ.get("PODCLI_FFMPEG", "ffmpeg")
    duration = end_second - start_second
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, start_second):.3f}",
        "-i", media_path,
        "-t", f"{duration:.3f}",
        "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-f", "s16le", "pipe:1",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=max(30, int(duration * 2)))
        if result.returncode != 0 or not result.stdout:
            return None
        samples = array("h")
        samples.frombytes(result.stdout)
        if sys.byteorder != "little":
            samples.byteswap()
        frame_samples = max(1, int(sample_rate * frame_ms / 1000))
        rms: list[float] = []
        for i in range(0, len(samples), frame_samples):
            chunk = samples[i : i + frame_samples]
            if not chunk:
                continue
            square_mean = sum(float(v) * float(v) for v in chunk) / len(chunk)
            rms.append(math.sqrt(square_mean) / 32768.0)
        return EnergyTrack(start_second, frame_ms / 1000.0, rms)
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return None


def _word_intervals(words: list[dict], guard: float = 0.035) -> list[tuple[float, float]]:
    intervals = []
    for word in words:
        try:
            start = float(word["start"]) - guard
            end = float(word["end"]) + guard
        except (KeyError, TypeError, ValueError):
            continue
        intervals.append((start, end))
    return intervals


def protect_segment_boundaries(
    segments: list[dict],
    words: list[dict],
    profile: EditingProfile,
) -> list[dict]:
    """Extend unsafe in/out points so no estimated word is truncated."""
    protected: list[dict] = []
    ordered_words = sorted(words, key=lambda w: float(w.get("start", 0)))
    for original in segments:
        segment = dict(original)
        start = float(segment["start"])
        end = float(segment["end"])
        overlapping = [
            w for w in ordered_words
            if float(w.get("end", 0)) > start and float(w.get("start", 0)) < end
        ]
        if overlapping:
            first = overlapping[0]
            last = overlapping[-1]
            first_start = float(first.get("start", start))
            last_end = float(last.get("end", end))
            if first_start - start < profile.pre_roll:
                start = max(0.0, min(start, first_start - profile.pre_roll))
            if end - last_end < profile.post_roll:
                end = max(end, last_end + profile.post_roll)
        segment["start"] = round(start, 3)
        segment["end"] = round(end, 3)
        protected.append(segment)
    return protected


def _is_sentence_pause(word: dict) -> bool:
    text = str(word.get("word", "")).strip()
    return text.endswith((".", "!", "?", "…"))


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
    words: list[dict],
    start_second: float,
    end_second: float,
    profile: EditingProfile,
    energy: Optional[EnergyTrack] = None,
    speech_intervals: Optional[list[dict]] = None,
    minimum_total_saving: float = 0.70,
) -> tuple[list[dict], dict]:
    """Build speech-safe keep ranges and return diagnostics for QA/UI."""
    clip_words = sorted(
        [
            w for w in words
            if float(w.get("end", 0)) > start_second
            and float(w.get("start", 0)) < end_second
        ],
        key=lambda w: float(w.get("start", 0)),
    )
    diagnostics = {
        "profile": profile.name,
        "waveform_used": energy is not None,
        "candidate_pauses": 0,
        "cuts": 0,
        "saved_seconds": 0.0,
        "crossfade": profile.crossfade,
    }
    if len(clip_words) < 3:
        return [{"start": start_second, "end": end_second}], diagnostics

    start_second = max(0.0, min(
        start_second,
        float(clip_words[0]["start"]) - profile.pre_roll,
    ))
    end_second = max(end_second, float(clip_words[-1]["end"]) + profile.post_roll)
    forbidden = _word_intervals(clip_words)
    for interval in speech_intervals or []:
        try:
            a = float(interval["start"])
            b = float(interval["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b > a:
            forbidden.append((a, b))
    candidates: list[dict] = []
    for previous, following in zip(clip_words, clip_words[1:]):
        prev_end = float(previous["end"])
        next_start = float(following["start"])
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
            cut_from = energy.quiet_boundary(
                cut_from, profile.boundary_radius, forbidden
            )
            cut_to = energy.quiet_boundary(
                cut_to, profile.boundary_radius, forbidden
            )
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
        return [{"start": start_second, "end": end_second}], diagnostics

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
        return [{"start": start_second, "end": end_second}], diagnostics

    kept = sum(s["end"] - s["start"] for s in segments)
    saving = duration - kept
    if saving < minimum_total_saving:
        return [{"start": start_second, "end": end_second}], diagnostics

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


def build_professional_edit(
    media_path: str,
    words: list[dict],
    start_second: float,
    end_second: float,
    pacing_profile: Optional[str] = "auto",
    speech_intervals: Optional[list[dict]] = None,
) -> tuple[list[dict], dict]:
    profile = resolve_profile(pacing_profile, words, start_second, end_second)
    energy = extract_energy_track(media_path, start_second, end_second)
    segments, diagnostics = plan_safe_segments(
        words,
        start_second,
        end_second,
        profile,
        energy,
        speech_intervals=speech_intervals,
    )
    diagnostics["vad_used"] = bool(speech_intervals)
    return segments, diagnostics


def timeline_duration(segments: list[dict], default_crossfade: float = 0.0) -> float:
    total = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in segments)
    if len(segments) < 2:
        return total
    overlap = 0.0
    for segment in segments[:-1]:
        transition = segment.get("transition") or {}
        overlap += max(0.0, float(transition.get("duration", default_crossfade) or 0.0))
    return max(0.0, total - overlap)
