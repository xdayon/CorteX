from __future__ import annotations

import json
from typing import Callable

from cortex.config import CortexConfig
from cortex.domain.models import SourceAsset, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.ingest.normalize import extract_normalized_audio
from cortex.paths import cache_dir, transcripts_dir
from cortex.transcribe.engine import TranscriptionCancelled, TranscriptionEngine
from cortex.transcribe.schemas import TRANSCRIPT_SCHEMA_VERSION, TranscriptDocument

# Progress is split into three ranges so the SSE stream shows real motion
# during audio extraction, decoding, and artifact persistence.
_EXTRACT_RANGE = (0.0, 10.0)
_TRANSCRIBE_RANGE = (10.0, 95.0)
_PERSIST_RANGE = (95.0, 100.0)


class TranscribeJobCancelled(RuntimeError):
    pass


def _scale(fraction: float, low: float, high: float) -> float:
    return low + max(0.0, min(1.0, fraction)) * (high - low)


class TranscribeService:
    def __init__(
        self,
        config: CortexConfig,
        domain: DomainStore,
        engine: TranscriptionEngine,
    ) -> None:
        self._config = config
        self._domain = domain
        self._engine = engine

    def run(
        self,
        *,
        source_asset: SourceAsset,
        overrides: dict,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        """Transcribe `source_asset`, returning a job.result-shaped dict.

        Reuses a cached TranscriptArtifact when one exists for the same
        (audio hash, model, device, compute, language, vad, schema_version).
        """
        cfg = self._config.transcription
        model = overrides.get("model") or cfg.model
        language = overrides.get("language", cfg.language)
        batch_size = overrides.get("batch_size", cfg.batch_size)
        vad = overrides.get("vad", cfg.vad)
        device = cfg.device
        compute_type = cfg.compute_type
        allow_cpu_fallback = cfg.allow_cpu_fallback

        if should_cancel():
            raise TranscribeJobCancelled()

        progress_cb(0.0, "Extraindo áudio normalizado")
        normalized = extract_normalized_audio(
            self._config.render.ffmpeg,
            self._config.render.ffprobe,
            self._config.paths.data_dir / source_asset.stored_path,
            source_sha256=source_asset.sha256,
            cache_dir=cache_dir(self._config, source_asset.project_id),
        )
        progress_cb(_EXTRACT_RANGE[1], "Áudio normalizado pronto")

        if should_cancel():
            raise TranscribeJobCancelled()

        cached = self._domain.find_cached_transcript_artifact(
            source_asset_id=source_asset.id,
            audio_sha256=source_asset.sha256,
            model=model,
            device=device,
            compute_type=compute_type,
            language=language,
            vad=vad,
            schema_version=TRANSCRIPT_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Transcript em cache reutilizado")
            return {
                "cached": True,
                "transcript_artifact_id": cached.id,
                "transcript_path": cached.path,
                "language": cached.language,
                "duration_seconds": cached.duration_seconds,
            }

        def _engine_progress(fraction: float) -> None:
            progress_cb(_scale(fraction, *_TRANSCRIBE_RANGE), "Transcrevendo")

        try:
            result = self._engine.transcribe(
                normalized.path,
                model=model,
                language=language,
                device=device,
                compute_type=compute_type,
                batch_size=batch_size,
                vad=vad,
                allow_cpu_fallback=allow_cpu_fallback,
                progress_cb=_engine_progress,
                should_cancel=should_cancel,
            )
        except TranscriptionCancelled as exc:
            raise TranscribeJobCancelled() from exc

        progress_cb(_TRANSCRIBE_RANGE[1], "Transcrição concluída, salvando artefato")

        document = TranscriptDocument(
            language=result.language,
            language_probability=result.language_probability,
            duration_seconds=result.duration_seconds,
            engine=result.engine_info,
            segments=result.segments,
        )

        out_dir = transcripts_dir(self._config, source_asset.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = out_dir / f"transcript-{source_asset.sha256[:12]}.json"
        transcript_path.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        artifact = TranscriptArtifact(
            project_id=source_asset.project_id,
            source_asset_id=source_asset.id,
            schema_version=TRANSCRIPT_SCHEMA_VERSION,
            path=str(transcript_path),
            audio_sha256=source_asset.sha256,
            engine=result.engine_info.name,
            model=model,
            device=result.engine_info.effective_device,
            compute_type=result.engine_info.effective_compute_type,
            language=result.language,
            vad=vad,
            batch_size=batch_size,
            duration_seconds=result.duration_seconds,
        )
        self._domain.create_transcript_artifact(artifact)

        progress_cb(100.0, "Transcrição concluída")
        return {
            "cached": False,
            "transcript_artifact_id": artifact.id,
            "transcript_path": artifact.path,
            "language": result.language,
            "duration_seconds": result.duration_seconds,
            "engine": result.engine_info.model_dump(mode="json"),
        }
