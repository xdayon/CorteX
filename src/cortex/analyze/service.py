from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from cortex.analyze.audio import (
    detect_speech,
    measure_loudness,
    pause_intervals,
    read_normalized_wav,
    room_tone_samples,
    speech_density,
    vad_parameters,
    waveform_resolutions,
)
from cortex.analyze.schemas import ANALYSIS_SCHEMA_VERSION, AnalysisDocument, AnalysisEngineInfo
from cortex.config import CortexConfig
from cortex.domain.models import SourceAsset, StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.ingest.normalize import extract_normalized_audio
from cortex.paths import analysis_dir, cache_dir


class AnalysisJobCancelled(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AnalysisService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def run(
        self,
        *,
        source_asset: SourceAsset,
        transcript_artifact: TranscriptArtifact,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        if should_cancel():
            raise AnalysisJobCancelled()
        progress_cb(0.0, "Reutilizando áudio normalizado")
        normalized = extract_normalized_audio(
            self._config.render.ffmpeg, self._config.render.ffprobe,
            self._config.paths.data_dir / source_asset.stored_path,
            source_sha256=source_asset.sha256,
            cache_dir=cache_dir(self._config, source_asset.project_id),
        )
        audio_hash = _sha256(normalized.path)
        transcript_hash = _sha256(Path(transcript_artifact.path))
        hash_payload = {
            "analysis_algorithm": "1.0.1",
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "source_asset_id": source_asset.id,
            "transcript_artifact_id": transcript_artifact.id,
            "audio_sha256": audio_hash,
            "transcript_sha256": transcript_hash,
            "vad": vad_parameters(),
            "waveform_points": [512, 4096],
            "loudness_engine": "ffmpeg-loudnorm-ebur128",
        }
        input_hash = hashlib.sha256(json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        cached = self._domain.find_cached_stage_artifact(
            project_id=source_asset.project_id, stage="analysis", input_hash=input_hash,
            schema_version=ANALYSIS_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Análise em cache reutilizada")
            return {"cached": True, "analysis_artifact_id": cached.id, "analysis_path": cached.path, "schema_version": cached.schema_version}

        progress_cb(10.0, "Lendo PCM normalizado")
        audio, sample_rate, channels = read_normalized_wav(normalized.path)
        duration = len(audio) / sample_rate
        if should_cancel():
            raise AnalysisJobCancelled()
        progress_cb(25.0, "Calculando waveform e RMS")
        waveform = waveform_resolutions(audio)
        progress_cb(45.0, "Executando Silero VAD")
        vad, vad_version = detect_speech(audio, sample_rate)
        pauses = pause_intervals(vad, duration)
        density, overall_ratio = speech_density(vad, duration)
        room_tone = room_tone_samples(audio, sample_rate, pauses)
        if should_cancel():
            raise AnalysisJobCancelled()
        progress_cb(75.0, "Medindo loudness e true peak")
        loudness = measure_loudness(self._config.render.ffmpeg, normalized.path)
        document = AnalysisDocument(
            project_id=source_asset.project_id, source_asset_id=source_asset.id,
            transcript_artifact_id=transcript_artifact.id, input_hash=input_hash,
            normalized_audio_sha256=audio_hash, sample_rate=sample_rate, channels=channels,
            duration_seconds=round(duration, 4),
            engines=AnalysisEngineInfo(
                vad="silero-vad-onnx-via-faster-whisper", vad_version=vad_version,
                vad_parameters=vad_parameters(), waveform="numpy-pcm-s16le",
                loudness="ffmpeg-loudnorm-ebur128",
            ),
            waveform=waveform, vad_intervals=vad, pauses=pauses,
            speech_density=density, overall_speech_ratio=overall_ratio,
            loudness=loudness, room_tone=room_tone,
        )
        if should_cancel():
            raise AnalysisJobCancelled()
        progress_cb(95.0, "Persistindo AnalysisArtifact")
        out_dir = analysis_dir(self._config, source_asset.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"analysis-{input_hash[:16]}.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        if should_cancel():
            temporary_path.unlink(missing_ok=True)
            raise AnalysisJobCancelled()
        temporary_path.replace(output_path)
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=source_asset.project_id, stage="analysis",
            schema_version=ANALYSIS_SCHEMA_VERSION, path=str(output_path), input_hash=input_hash,
            metadata={
                "source_asset_id": source_asset.id,
                "transcript_artifact_id": transcript_artifact.id,
                "normalized_audio_sha256": audio_hash,
                "requested_engines": {"vad": "silero-vad-onnx", "loudness": "ffmpeg-loudnorm"},
                "effective_engines": document.engines.model_dump(mode="json"),
            },
        ))
        progress_cb(100.0, "Análise local concluída")
        return {"cached": False, "analysis_artifact_id": artifact.id, "analysis_path": artifact.path, "schema_version": artifact.schema_version}
