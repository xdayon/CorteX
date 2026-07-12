from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from cortex.analyze.schemas import AnalysisDocument


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
