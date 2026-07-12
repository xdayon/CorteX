from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from cortex.analyze.schemas import AnalysisDocument
from cortex.transcribe.schemas import TranscriptWord

_WORD_GUARD = 0.035
_SCENE_SNAP_WORD_GUARD = 0.015
_SCENE_SNAP_MIN_SEGMENT_SECONDS = 0.2


@dataclass
class EnergyTrack:
    """RMS energy sampled in fixed-duration frames.

    Built from the highest-density WaveformResolution already persisted in
    the AnalysisArtifact (see energy_track_from_analysis). This module never
    redecodes audio/ffmpeg on the edit-planning path.
    """

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


def word_containing(
    words: list[TranscriptWord], timestamp: float, guard: float = _SCENE_SNAP_WORD_GUARD
) -> TranscriptWord | None:
    """Return the word whose (guarded) span strictly contains ``timestamp``."""
    for word in words:
        if word.start + guard < timestamp < word.end - guard:
            return word
    return None


def nearest_scene_cut(target: float, cuts: list[float], tolerance: float) -> float | None:
    """Return the closest scene cut to ``target`` within ``tolerance`` seconds, if any."""
    if not cuts or tolerance <= 0:
        return None
    candidates = [cut for cut in cuts if abs(cut - target) <= tolerance]
    if not candidates:
        return None
    return min(candidates, key=lambda cut: abs(cut - target))


def snap_segments_to_scene_cuts(
    segments: list[dict],
    words: list[TranscriptWord],
    vad_intervals: Iterable[tuple[float, float]],
    cuts: list[float],
    tolerance: float,
) -> tuple[list[dict], list[dict]]:
    """Snap EDL video boundaries to nearby scene cuts (see scene_index).

    A boundary is moved to a scene cut only when the cut lies within
    ``tolerance`` seconds AND the move does not land inside a word or a
    protected VAD speech interval — speech/pause safety always outranks
    scene alignment (docs/EDITING_ENGINE.md: "Em baixa confianca, preservar
    fala/pausa"). Returns the (possibly) adjusted segments plus a list of
    ``scene_snapped`` quality-report issue dicts, one per applied snap.
    """
    if not cuts or tolerance <= 0:
        return segments, []

    forbidden: list[tuple[float, float]] = [(w.start - _WORD_GUARD, w.end + _WORD_GUARD) for w in words]
    for start, end in vad_intervals:
        if end > start:
            forbidden.append((start, end))

    snapped = [dict(segment) for segment in segments]
    issues: list[dict] = []
    for index, segment in enumerate(snapped):
        for boundary_key, boundary_name in (("start", "in"), ("end", "out")):
            original = float(segment[boundary_key])
            candidate = nearest_scene_cut(original, cuts, tolerance)
            if candidate is None or round(candidate, 3) == round(original, 3):
                continue
            if word_containing(words, candidate) is not None:
                continue
            if any(a <= candidate <= b for a, b in forbidden):
                continue
            # o snap nunca pode inverter ou esvaziar o segmento (start e end
            # podem ser puxados um contra o outro por cortes vizinhos)
            if boundary_key == "start":
                if candidate > float(segment["end"]) - _SCENE_SNAP_MIN_SEGMENT_SECONDS:
                    continue
            elif candidate < float(segment["start"]) + _SCENE_SNAP_MIN_SEGMENT_SECONDS:
                continue
            segment[boundary_key] = round(candidate, 3)
            issues.append({
                "severity": "info",
                "code": "scene_snapped",
                "segment": index,
                "boundary": boundary_name,
                "snapped_from": round(original, 3),
                "snapped_to": round(candidate, 3),
                "delta_ms": round(abs(candidate - original) * 1000, 1),
            })
    return snapped, issues


def energy_track_from_analysis(analysis: AnalysisDocument) -> EnergyTrack:
    """Derive an EnergyTrack from the AnalysisArtifact's densest waveform.

    WaveformResolution.frames_per_point is the number of normalized-audio
    PCM frames folded into each RMS/peak point (see
    cortex.analyze.audio.waveform_resolutions), so the real-time span of one
    point is frames_per_point / sample_rate seconds. The track starts at
    t=0 of the normalized audio, matching word/VAD timestamps.
    """
    if not analysis.waveform:
        return EnergyTrack(origin=0.0, frame_seconds=1.0, rms=[])
    resolution = max(analysis.waveform, key=lambda item: item.points)
    frame_seconds = resolution.frames_per_point / analysis.sample_rate
    return EnergyTrack(origin=0.0, frame_seconds=frame_seconds, rms=list(resolution.rms))
