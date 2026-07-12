from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from cortex.analyze.audio import read_normalized_wav
from cortex.analyze.multicam_sync import ENVELOPE_HZ, audio_envelope, estimate_audio_offset
from cortex.analyze.multicam_sync_schemas import (
    MULTICAM_SYNC_SCHEMA_VERSION,
    CameraSyncResult,
    MulticamSyncDocument,
    MulticamSyncEngineInfo,
)
from cortex.config import CortexConfig
from cortex.domain.models import SourceAsset, StageArtifact
from cortex.domain.store import DomainStore
from cortex.ingest.normalize import extract_normalized_audio
from cortex.paths import cache_dir, multicam_sync_dir

MULTICAM_SYNC_ALGORITHM_VERSION = "1.0.0"
CORRELATION_THRESHOLD = 0.55
PEAK_MARGIN_THRESHOLD = 0.05
MINIMUM_OVERLAP_SECONDS = 5.0


class MulticamSyncJobCancelled(RuntimeError):
    pass


class MulticamSyncPreconditionError(ValueError):
    pass


class MulticamSyncService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def _normalized_envelope(self, asset: SourceAsset, analysis_seconds: float):
        source_path = self._config.paths.data_dir / asset.stored_path
        if not source_path.exists():
            raise MulticamSyncPreconditionError(
                f"arquivo da fonte {asset.id} não está disponível para sincronização"
            )
        normalized = extract_normalized_audio(
            self._config.render.ffmpeg,
            self._config.render.ffprobe,
            source_path,
            source_sha256=asset.sha256,
            cache_dir=cache_dir(self._config, asset.project_id),
        )
        audio, sample_rate, _channels = read_normalized_wav(normalized.path)
        limited = audio[:round(analysis_seconds * sample_rate)]
        return audio_envelope(limited, sample_rate), sample_rate

    def run(
        self,
        *,
        primary_source: SourceAsset,
        alternate_sources: list[SourceAsset],
        max_offset_seconds: float | None,
        analysis_seconds: float | None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        if should_cancel():
            raise MulticamSyncJobCancelled()
        if not alternate_sources:
            raise MulticamSyncPreconditionError("ao menos uma fonte alternativa é obrigatória")
        if len({asset.id for asset in alternate_sources}) != len(alternate_sources):
            raise MulticamSyncPreconditionError("fontes alternativas duplicadas")
        if primary_source.id in {asset.id for asset in alternate_sources}:
            raise MulticamSyncPreconditionError("fonte primária não pode ser alternativa")
        if any(asset.project_id != primary_source.project_id for asset in alternate_sources):
            raise MulticamSyncPreconditionError("fontes multicamera não pertencem ao mesmo projeto")
        effective_max_offset = round(float(
            max_offset_seconds
            if max_offset_seconds is not None
            else self._config.analysis.multicam_max_offset_seconds
        ), 4)
        effective_analysis_seconds = round(float(
            analysis_seconds
            if analysis_seconds is not None
            else self._config.analysis.multicam_analysis_seconds
        ), 4)
        hash_payload = {
            "algorithm": MULTICAM_SYNC_ALGORITHM_VERSION,
            "schema_version": MULTICAM_SYNC_SCHEMA_VERSION,
            "primary": [primary_source.id, primary_source.sha256],
            "alternates": sorted((asset.id, asset.sha256) for asset in alternate_sources),
            "max_offset_seconds": effective_max_offset,
            "analysis_seconds": effective_analysis_seconds,
            "envelope_hz": ENVELOPE_HZ,
            "correlation_threshold": CORRELATION_THRESHOLD,
            "peak_margin_threshold": PEAK_MARGIN_THRESHOLD,
            "minimum_overlap_seconds": MINIMUM_OVERLAP_SECONDS,
            "ffmpeg": str(self._config.render.ffmpeg),
            "ffprobe": str(self._config.render.ffprobe),
        }
        input_hash = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cached = self._domain.find_cached_stage_artifact(
            project_id=primary_source.project_id,
            stage="multicam_sync",
            input_hash=input_hash,
            schema_version=MULTICAM_SYNC_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Sincronização multicamera em cache reutilizada")
            return {
                "cached": True,
                "multicam_sync_artifact_id": cached.id,
                "multicam_sync_path": cached.path,
                "schema_version": cached.schema_version,
            }

        progress_cb(0.0, "Normalizando áudio da fonte primária")
        primary_envelope, sample_rate = self._normalized_envelope(
            primary_source, effective_analysis_seconds
        )
        results: list[CameraSyncResult] = []
        for index, alternate in enumerate(alternate_sources):
            if should_cancel():
                raise MulticamSyncJobCancelled()
            progress_cb(
                10.0 + 70.0 * index / len(alternate_sources),
                f"Correlacionando camera {index + 1} de {len(alternate_sources)}",
            )
            alternate_envelope, alternate_rate = self._normalized_envelope(
                alternate, effective_analysis_seconds
            )
            if alternate_rate != sample_rate:
                raise MulticamSyncPreconditionError("áudios normalizados com sample rates distintos")
            offset, correlation, margin, overlap = estimate_audio_offset(
                primary_envelope,
                alternate_envelope,
                envelope_hz=ENVELOPE_HZ,
                max_offset_seconds=effective_max_offset,
                minimum_overlap_seconds=MINIMUM_OVERLAP_SECONDS,
            )
            reasons = []
            if correlation < CORRELATION_THRESHOLD:
                reasons.append("correlation_below_threshold")
            if margin < PEAK_MARGIN_THRESHOLD:
                reasons.append("ambiguous_peak")
            if overlap < MINIMUM_OVERLAP_SECONDS:
                reasons.append("insufficient_overlap")
            results.append(CameraSyncResult(
                source_asset_id=alternate.id,
                source_sha256=alternate.sha256,
                status="rejected" if reasons else "synced",
                offset_us=round(offset * 1_000_000),
                correlation=round(correlation, 6),
                peak_margin=round(margin, 6),
                overlap_seconds=round(overlap, 4),
                reason=",".join(reasons) or None,
            ))

        document = MulticamSyncDocument(
            project_id=primary_source.project_id,
            primary_source_asset_id=primary_source.id,
            primary_source_sha256=primary_source.sha256,
            input_hash=input_hash,
            cameras=results,
            synced_camera_count=sum(item.status == "synced" for item in results),
            rejected_camera_count=sum(item.status == "rejected" for item in results),
            engine=MulticamSyncEngineInfo(
                algorithm="normalized_audio_envelope_fft_correlation",
                algorithm_version=MULTICAM_SYNC_ALGORITHM_VERSION,
                sample_rate=sample_rate,
                envelope_hz=ENVELOPE_HZ,
                max_offset_seconds_requested=effective_max_offset,
                max_offset_seconds_effective=effective_max_offset,
                analysis_seconds_requested=effective_analysis_seconds,
                correlation_threshold=CORRELATION_THRESHOLD,
                peak_margin_threshold=PEAK_MARGIN_THRESHOLD,
                minimum_overlap_seconds=MINIMUM_OVERLAP_SECONDS,
                ffmpeg_path=str(self._config.render.ffmpeg),
                ffprobe_path=str(self._config.render.ffprobe),
            ),
        )
        progress_cb(90.0, "Persistindo MulticamSyncArtifact")
        out_dir = multicam_sync_dir(self._config, primary_source.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"multicam-sync-{input_hash[:16]}.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if should_cancel():
            temporary_path.unlink(missing_ok=True)
            raise MulticamSyncJobCancelled()
        temporary_path.replace(output_path)
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=primary_source.project_id,
            stage="multicam_sync",
            schema_version=MULTICAM_SYNC_SCHEMA_VERSION,
            path=str(output_path),
            input_hash=input_hash,
            metadata={
                "primary_source_asset_id": primary_source.id,
                "alternate_source_asset_ids": [asset.id for asset in alternate_sources],
                "synced_camera_count": document.synced_camera_count,
                "rejected_camera_count": document.rejected_camera_count,
            },
        ))
        progress_cb(100.0, "Sincronização multicamera concluída")
        return {
            "cached": False,
            "multicam_sync_artifact_id": artifact.id,
            "multicam_sync_path": artifact.path,
            "schema_version": artifact.schema_version,
        }
