from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from cortex.transcribe.schemas import EngineInfo, TranscriptSegment, TranscriptWord

ProgressCallback = Callable[[float], None]
ShouldCancel = Callable[[], bool]


class TranscriptionEngineError(RuntimeError):
    """Raised when the engine cannot produce a transcript (e.g. CUDA load
    failure with allow_cpu_fallback=False). The worker persists this as the
    job's failure reason — CUDA never silently falls back to CPU."""


class TranscriptionCancelled(RuntimeError):
    """Raised by an engine implementation when should_cancel() reports the
    job was cancelled mid-transcription."""


@dataclass
class EngineTranscriptionResult:
    segments: list[TranscriptSegment]
    language: str | None
    language_probability: float | None
    duration_seconds: float
    engine_info: EngineInfo


class TranscriptionEngine(Protocol):
    def transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
        device: str,
        compute_type: str,
        batch_size: int,
        vad: bool,
        allow_cpu_fallback: bool,
        progress_cb: ProgressCallback | None = None,
        should_cancel: ShouldCancel | None = None,
    ) -> EngineTranscriptionResult: ...

    def unload(self) -> None: ...


class FasterWhisperEngine:
    """Real faster-whisper engine. Keeps the model warm between jobs and
    only reloads WhisperModel when (model, device, compute_type) changes."""

    def __init__(self) -> None:
        self._model = None
        self._batched_pipeline = None
        self._loaded_key: tuple[str, str, str] | None = None

    def unload(self) -> None:
        self._model = None
        self._batched_pipeline = None
        self._loaded_key = None

    def _load_model(self, model: str, device: str, compute_type: str):
        from faster_whisper import WhisperModel

        key = (model, device, compute_type)
        if self._model is not None and self._loaded_key == key:
            return self._model
        loaded = WhisperModel(model, device=device, compute_type=compute_type)
        self._model = loaded
        self._batched_pipeline = None
        self._loaded_key = key
        return loaded

    def transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
        device: str,
        compute_type: str,
        batch_size: int,
        vad: bool,
        allow_cpu_fallback: bool,
        progress_cb: ProgressCallback | None = None,
        should_cancel: ShouldCancel | None = None,
    ) -> EngineTranscriptionResult:
        effective_device, effective_compute_type = device, compute_type
        fallback = False
        try:
            whisper_model = self._load_model(model, device, compute_type)
        except Exception as exc:
            if device == "cuda" and allow_cpu_fallback:
                fallback = True
                effective_device, effective_compute_type = "cpu", compute_type
                try:
                    whisper_model = self._load_model(model, effective_device, effective_compute_type)
                except Exception as cpu_exc:
                    raise TranscriptionEngineError(
                        f"falha ao carregar '{model}' em cuda/{compute_type} ({exc}) e no "
                        f"fallback cpu/{effective_compute_type} ({cpu_exc})"
                    ) from cpu_exc
            else:
                raise TranscriptionEngineError(
                    f"falha ao carregar '{model}' em {device}/{compute_type}: {exc}"
                ) from exc

        common = dict(
            language=language,
            word_timestamps=True,
            vad_filter=vad,
            condition_on_previous_text=False,
        )

        segment_iter = None
        info = None
        if batch_size > 0 and effective_device == "cuda":
            try:
                from faster_whisper import BatchedInferencePipeline

                if self._batched_pipeline is None:
                    self._batched_pipeline = BatchedInferencePipeline(model=whisper_model)
                segment_iter, info = self._batched_pipeline.transcribe(
                    str(audio_path), batch_size=batch_size, **common,
                )
            except Exception:
                segment_iter = None

        if segment_iter is None:
            segment_iter, info = whisper_model.transcribe(str(audio_path), **common)

        duration = float(getattr(info, "duration", 0.0) or 0.0)
        segments: list[TranscriptSegment] = []
        for index, segment in enumerate(segment_iter):
            if should_cancel is not None and should_cancel():
                raise TranscriptionCancelled("transcrição cancelada pelo usuário")
            words = [
                TranscriptWord(
                    start=round(float(word.start), 3),
                    end=round(float(word.end), 3),
                    word=(word.word or "").strip(),
                    probability=round(float(getattr(word, "probability", 0.0) or 0.0), 3),
                )
                for word in (segment.words or [])
                if (word.word or "").strip()
            ]
            segments.append(TranscriptSegment(
                id=index,
                start=round(float(segment.start), 3),
                end=round(float(segment.end), 3),
                text=(segment.text or "").strip(),
                avg_logprob=round(float(segment.avg_logprob), 3),
                no_speech_prob=round(float(segment.no_speech_prob), 3),
                words=words,
            ))
            if progress_cb is not None and duration > 0:
                progress_cb(min(1.0, float(segment.end) / duration))

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
        resolved_duration = duration if duration > 0 else (segments[-1].end if segments else 0.0)
        return EngineTranscriptionResult(
            segments=segments,
            language=getattr(info, "language", None) or language,
            language_probability=getattr(info, "language_probability", None),
            duration_seconds=resolved_duration,
            engine_info=engine_info,
        )
