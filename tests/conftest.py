from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cortex.transcribe.engine import (  # noqa: E402
    EngineTranscriptionResult,
    TranscriptionCancelled,
    TranscriptionEngineError,
)
from cortex.transcribe.schemas import EngineInfo, TranscriptSegment, TranscriptWord  # noqa: E402


class FakeTranscriptionEngine:
    """In-memory stand-in for FasterWhisperEngine, injectable into
    TranscribeService/worker so tests never need a GPU or the real model.

    - `fail_on_cuda`: simulate a CUDA load failure; honors allow_cpu_fallback
      exactly like the real engine (fail-hard by default).
    - `progress_steps`: how many progress_cb calls to emit before returning,
      checking should_cancel() before each one (cooperative cancellation).
    """

    def __init__(
        self,
        *,
        segments: list[TranscriptSegment] | None = None,
        language: str = "pt",
        language_probability: float = 0.97,
        duration_seconds: float = 2.0,
        fail_on_cuda: bool = False,
        progress_steps: int = 3,
    ) -> None:
        self.segments = segments if segments is not None else [
            TranscriptSegment(
                id=0,
                start=0.0,
                end=duration_seconds,
                text="ola mundo",
                avg_logprob=-0.15,
                no_speech_prob=0.02,
                words=[
                    TranscriptWord(start=0.0, end=0.4, word="ola", probability=0.95),
                    TranscriptWord(start=0.4, end=0.9, word="mundo", probability=0.9),
                ],
            )
        ]
        self.language = language
        self.language_probability = language_probability
        self.duration_seconds = duration_seconds
        self.fail_on_cuda = fail_on_cuda
        self.progress_steps = progress_steps
        self.unloaded = False
        self.calls: list[dict] = []

    def transcribe(
        self,
        audio_path,
        *,
        model,
        language,
        device,
        compute_type,
        batch_size,
        vad,
        allow_cpu_fallback,
        progress_cb=None,
        should_cancel=None,
    ) -> EngineTranscriptionResult:
        self.calls.append({"device": device, "compute_type": compute_type})
        effective_device, effective_compute_type, fallback = device, compute_type, False

        if self.fail_on_cuda and device == "cuda":
            if not allow_cpu_fallback:
                raise TranscriptionEngineError("CUDA indisponível (simulado nos testes)")
            effective_device, effective_compute_type, fallback = "cpu", "int8", True

        for step in range(self.progress_steps):
            if should_cancel is not None and should_cancel():
                raise TranscriptionCancelled("cancelado durante a transcrição (simulado)")
            if progress_cb is not None:
                progress_cb((step + 1) / self.progress_steps)

        engine_info = EngineInfo(
            model=model,
            requested_device=device,
            effective_device=effective_device,
            requested_compute_type=compute_type,
            effective_compute_type=effective_compute_type,
            batch_size=batch_size,
            vad=vad,
            fallback=fallback,
        )
        return EngineTranscriptionResult(
            segments=self.segments,
            language=language or self.language,
            language_probability=self.language_probability,
            duration_seconds=self.duration_seconds,
            engine_info=engine_info,
        )

    def unload(self) -> None:
        self.unloaded = True


@pytest.fixture
def short_wav(tmp_path: Path) -> Path:
    """2s sine-wave mono WAV generated with ffmpeg (no network, no fixtures on disk)."""
    out = tmp_path / "tone.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-ar", "44100", "-ac", "1", str(out),
        ],
        capture_output=True, check=True, timeout=30,
    )
    return out


@pytest.fixture
def short_mp4(tmp_path: Path) -> Path:
    """2s mp4 (testsrc video + sine audio) generated with ffmpeg."""
    out = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            str(out),
        ],
        capture_output=True, check=True, timeout=30,
    )
    return out
