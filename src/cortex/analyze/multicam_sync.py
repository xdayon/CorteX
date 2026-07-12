from __future__ import annotations

import numpy as np

ENVELOPE_HZ = 100


def audio_envelope(audio: np.ndarray, sample_rate: int, envelope_hz: int = ENVELOPE_HZ) -> np.ndarray:
    block = max(1, round(sample_rate / envelope_hz))
    usable = len(audio) // block * block
    if usable == 0:
        return np.empty(0, dtype=np.float64)
    frames = audio[:usable].astype(np.float64).reshape(-1, block)
    envelope = np.sqrt(np.mean(np.square(frames), axis=1))
    median = float(np.median(envelope))
    centered = envelope - median
    scale = float(np.sqrt(np.mean(np.square(centered))))
    if scale <= 1e-9:
        return np.zeros_like(centered)
    return centered / scale


def _fft_correlation(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    output_size = len(first) + len(second) - 1
    fft_size = 1 << max(1, output_size - 1).bit_length()
    convolution = np.fft.irfft(
        np.fft.rfft(first, fft_size) * np.fft.rfft(second[::-1], fft_size),
        fft_size,
    )[:output_size]
    return convolution


def estimate_audio_offset(
    primary: np.ndarray,
    alternate: np.ndarray,
    *,
    envelope_hz: int = ENVELOPE_HZ,
    max_offset_seconds: float,
    minimum_overlap_seconds: float,
) -> tuple[float, float, float, float]:
    """Return alternate_time - primary_time, correlation, peak margin and overlap."""
    if len(primary) == 0 or len(alternate) == 0:
        return 0.0, 0.0, 0.0, 0.0
    raw = _fft_correlation(alternate, primary)
    lags = np.arange(-(len(primary) - 1), len(alternate), dtype=int)
    max_lag = round(max_offset_seconds * envelope_hz)
    minimum_overlap = round(minimum_overlap_seconds * envelope_hz)
    valid = np.abs(lags) <= max_lag
    valid_indices = np.flatnonzero(valid)
    scores = np.full(len(valid_indices), -1.0, dtype=np.float64)
    overlaps = np.zeros(len(valid_indices), dtype=int)
    primary_sq = np.r_[0.0, np.cumsum(np.square(primary))]
    alternate_sq = np.r_[0.0, np.cumsum(np.square(alternate))]
    for output_index, correlation_index in enumerate(valid_indices):
        lag = int(lags[correlation_index])
        alternate_start = max(lag, 0)
        primary_start = max(-lag, 0)
        overlap = min(len(alternate) - alternate_start, len(primary) - primary_start)
        overlaps[output_index] = max(0, overlap)
        if overlap < minimum_overlap:
            continue
        alternate_energy = alternate_sq[alternate_start + overlap] - alternate_sq[alternate_start]
        primary_energy = primary_sq[primary_start + overlap] - primary_sq[primary_start]
        denominator = float(np.sqrt(max(0.0, alternate_energy * primary_energy)))
        if denominator > 1e-12:
            scores[output_index] = float(raw[correlation_index] / denominator)
    best_index = int(np.argmax(scores))
    best_score = float(np.clip(scores[best_index], -1.0, 1.0))
    best_lag = int(lags[valid_indices[best_index]])
    exclusion = max(1, round(0.5 * envelope_hz))
    competing = scores.copy()
    competing[np.abs(lags[valid_indices] - best_lag) <= exclusion] = -1.0
    second_score = float(np.max(competing)) if len(competing) else -1.0
    peak_margin = max(0.0, best_score - second_score)
    return (
        best_lag / envelope_hz,
        best_score,
        peak_margin,
        float(overlaps[best_index] / envelope_hz),
    )
