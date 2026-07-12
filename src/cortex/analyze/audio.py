from __future__ import annotations

import json
import math
import re
import subprocess
import wave
from importlib.metadata import version
from pathlib import Path

import numpy as np

from cortex.analyze.schemas import (
    LoudnessMetrics,
    RoomToneSample,
    SpeechDensityWindow,
    TimeInterval,
    VadInterval,
    WaveformResolution,
)

_VAD_PARAMETERS = {
    "threshold": 0.5,
    "min_speech_duration_ms": 120,
    "min_silence_duration_ms": 250,
    "speech_pad_ms": 80,
}


class AnalysisEngineError(RuntimeError):
    pass


def read_normalized_wav(path: Path) -> tuple[np.ndarray, int, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_rate = handle.getframerate()
        sample_width = handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    if channels != 1 or sample_rate != 16_000 or sample_width != 2:
        raise AnalysisEngineError(
            f"WAV normalizado inesperado: {channels} canal(is), {sample_rate} Hz, {sample_width * 8} bit"
        )
    pcm = np.frombuffer(frames, dtype="<i2")
    return pcm.astype(np.float32) / 32768.0, sample_rate, channels


def detect_speech(audio: np.ndarray, sample_rate: int) -> tuple[list[VadInterval], str]:
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        options = VadOptions(**_VAD_PARAMETERS)
        timestamps = get_speech_timestamps(audio, options, sampling_rate=sample_rate)
        vad_version = version("faster-whisper")
    except Exception as exc:  # no implicit heuristic fallback
        raise AnalysisEngineError(f"Silero VAD indisponível: {exc}") from exc
    intervals = []
    for item in timestamps:
        start_sample, end_sample = int(item["start"]), int(item["end"])
        start, end = start_sample / sample_rate, end_sample / sample_rate
        intervals.append(VadInterval(
            start=round(start, 4), end=round(end, 4), duration=round(end - start, 4),
            start_sample=start_sample, end_sample=end_sample,
        ))
    return intervals, vad_version


def waveform_resolutions(audio: np.ndarray, point_counts: tuple[int, ...] = (512, 4096)) -> list[WaveformResolution]:
    result = []
    for requested_points in point_counts:
        points = max(1, min(requested_points, len(audio)))
        frames_per_point = max(1, math.ceil(len(audio) / points))
        padded_size = points * frames_per_point
        padded = np.pad(audio, (0, padded_size - len(audio)))
        blocks = padded.reshape(points, frames_per_point)
        peaks = np.max(np.abs(blocks), axis=1)
        rms = np.sqrt(np.mean(np.square(blocks), axis=1))
        result.append(WaveformResolution(
            points=points,
            frames_per_point=frames_per_point,
            peaks=np.round(peaks, 5).tolist(),
            rms=np.round(rms, 5).tolist(),
        ))
    return result


def pause_intervals(vad: list[VadInterval], duration: float, minimum: float = 0.18) -> list[TimeInterval]:
    pauses: list[TimeInterval] = []
    cursor = 0.0
    for speech in vad:
        if speech.start - cursor >= minimum:
            pauses.append(TimeInterval(start=round(cursor, 4), end=speech.start, duration=round(speech.start - cursor, 4)))
        cursor = max(cursor, speech.end)
    if duration - cursor >= minimum:
        pauses.append(TimeInterval(start=round(cursor, 4), end=round(duration, 4), duration=round(duration - cursor, 4)))
    return pauses


def speech_density(vad: list[VadInterval], duration: float, window_seconds: float = 10.0) -> tuple[list[SpeechDensityWindow], float]:
    if duration <= 0:
        return [], 0.0
    windows = []
    start = 0.0
    while start < duration:
        end = min(duration, start + window_seconds)
        speech = sum(max(0.0, min(end, item.end) - max(start, item.start)) for item in vad)
        windows.append(SpeechDensityWindow(
            start=round(start, 4), end=round(end, 4), duration=round(end - start, 4),
            speech_ratio=round(min(1.0, speech / (end - start)), 5),
        ))
        start = end
    total = sum(item.duration for item in vad)
    return windows, round(min(1.0, total / duration), 5)


def room_tone_samples(audio: np.ndarray, sample_rate: int, pauses: list[TimeInterval], limit: int = 5) -> list[RoomToneSample]:
    candidates = []
    for pause in pauses:
        if pause.duration < 0.2:
            continue
        margin = min(0.05, pause.duration / 5)
        start = int((pause.start + margin) * sample_rate)
        end = int((pause.end - margin) * sample_rate)
        chunk = audio[start:end]
        if not len(chunk):
            continue
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        dbfs = 20 * math.log10(max(rms, 1e-9))
        candidates.append((dbfs, RoomToneSample(
            start=round(start / sample_rate, 4), end=round(end / sample_rate, 4),
            duration=round((end - start) / sample_rate, 4), rms_dbfs=round(dbfs, 3),
        )))
    return [item for _, item in sorted(candidates, key=lambda candidate: candidate[0])[:limit]]


def measure_loudness(ffmpeg: str | Path, audio_path: Path) -> LoudnessMetrics:
    command = [
        str(ffmpeg), "-hide_banner", "-nostats", "-i", str(audio_path),
        "-af", "loudnorm=I=-24:LRA=7:TP=-2:print_format=json", "-f", "null", "-",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AnalysisEngineError(f"FFmpeg loudnorm não pôde ser executado: {exc}") from exc
    if completed.returncode != 0:
        raise AnalysisEngineError(f"FFmpeg loudnorm falhou ({completed.returncode}): {(completed.stderr or '')[-1000:]}")
    matches = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", completed.stderr or "", re.DOTALL)
    if not matches:
        raise AnalysisEngineError("FFmpeg loudnorm não retornou métricas JSON")
    try:
        data = json.loads(matches[-1])

        def metric(name: str) -> float | None:
            value = float(data[name])
            return value if math.isfinite(value) else None

        return LoudnessMetrics(
            integrated_lufs=metric("input_i"), loudness_range_lu=metric("input_lra"),
            true_peak_dbfs=metric("input_tp"), threshold_lufs=metric("input_thresh"),
            engine="ffmpeg-loudnorm-ebur128",
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AnalysisEngineError(f"métricas loudnorm inválidas: {exc}") from exc


def vad_parameters() -> dict[str, float | int]:
    return dict(_VAD_PARAMETERS)
