from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from cortex.analyze.face_service import FaceIndexJobCancelled, FaceIndexService
from cortex.analyze.multicam_sync_schemas import MulticamSyncDocument
from cortex.analyze.multicam_visual_schemas import (
    MULTICAM_VISUAL_SCHEMA_VERSION,
    IsoVisualIndex,
    MulticamVisualEngineInfo,
    MulticamVisualIndexDocument,
)
from cortex.analyze.scene_service import SceneIndexJobCancelled, SceneIndexService
from cortex.analyze.visual_quality_service import VisualQualityJobCancelled, VisualQualityService
from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore, SourceAssetNotFoundError
from cortex.paths import multicam_visual_dir

MULTICAM_VISUAL_ALGORITHM_VERSION = "1.0.0"


class MulticamVisualJobCancelled(RuntimeError):
    pass


class MulticamVisualPreconditionError(ValueError):
    pass


class MulticamVisualIndexService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def run(
        self,
        *,
        multicam_sync_artifact: StageArtifact,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        if should_cancel():
            raise MulticamVisualJobCancelled()
        sync_path = Path(multicam_sync_artifact.path)
        if not sync_path.exists():
            raise MulticamVisualPreconditionError("arquivo do multicam_sync não está disponível")
        sync = MulticamSyncDocument.model_validate_json(sync_path.read_text(encoding="utf-8"))
        if sync.project_id != multicam_sync_artifact.project_id:
            raise MulticamVisualPreconditionError("multicam_sync não corresponde ao projeto")
        hash_payload = {
            "algorithm": MULTICAM_VISUAL_ALGORITHM_VERSION,
            "schema_version": MULTICAM_VISUAL_SCHEMA_VERSION,
            "multicam_sync": [multicam_sync_artifact.id, multicam_sync_artifact.input_hash],
            "scene_threshold": self._config.analysis.scene_threshold,
            "face_sample_fps": self._config.analysis.face_sample_fps,
            "visual_quality_sample_fps": self._config.analysis.visual_quality_sample_fps,
        }
        input_hash = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cached = self._domain.find_cached_stage_artifact(
            project_id=sync.project_id,
            stage="multicam_visual_index",
            input_hash=input_hash,
            schema_version=MULTICAM_VISUAL_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Índices visuais multicamera em cache reutilizados")
            return {
                "cached": True,
                "multicam_visual_artifact_id": cached.id,
                "multicam_visual_path": cached.path,
                "schema_version": cached.schema_version,
            }

        scene_service = SceneIndexService(self._config, self._domain)
        face_service = FaceIndexService(self._config, self._domain)
        quality_service = VisualQualityService(self._config, self._domain)
        entries: list[IsoVisualIndex] = []
        total = max(len(sync.cameras), 1)
        for camera_index, camera in enumerate(sync.cameras):
            if should_cancel():
                raise MulticamVisualJobCancelled()
            if camera.status != "synced":
                entries.append(IsoVisualIndex(
                    source_asset_id=camera.source_asset_id,
                    source_sha256=camera.source_sha256,
                    offset_us=camera.offset_us,
                    status="rejected",
                    reason=camera.reason or "multicam_sync_rejected",
                ))
                continue
            try:
                source = self._domain.get_source_asset(camera.source_asset_id)
            except SourceAssetNotFoundError as exc:
                raise MulticamVisualPreconditionError(
                    f"fonte ISO {camera.source_asset_id} não encontrada"
                ) from exc
            if (
                source.project_id != sync.project_id
                or source.sha256 != camera.source_sha256
                or not (self._config.paths.data_dir / source.stored_path).exists()
            ):
                raise MulticamVisualPreconditionError(
                    f"fonte ISO {camera.source_asset_id} não corresponde ao sync"
                )
            base = 100.0 * camera_index / total
            span = 100.0 / total

            def stage_progress(start: float, width: float):
                return lambda value, message: progress_cb(
                    min(94.0, base + span * (start + width * value / 100.0)), message
                )

            try:
                scene_result = scene_service.run(
                    source_asset=source,
                    threshold=self._config.analysis.scene_threshold,
                    progress_cb=stage_progress(0.0, 0.2),
                    should_cancel=should_cancel,
                )
                scene_artifact = self._domain.get_stage_artifact(
                    scene_result["scene_index_artifact_id"]
                )
                face_result = face_service.run(
                    source_asset=source,
                    scene_index_artifact=scene_artifact,
                    sample_fps=self._config.analysis.face_sample_fps,
                    progress_cb=stage_progress(0.2, 0.55),
                    should_cancel=should_cancel,
                )
                face_artifact = self._domain.get_stage_artifact(
                    face_result["face_index_artifact_id"]
                )
                quality_result = quality_service.run(
                    source_asset=source,
                    scene_index_artifact=scene_artifact,
                    face_index_artifact=face_artifact,
                    sample_fps=self._config.analysis.visual_quality_sample_fps,
                    progress_cb=stage_progress(0.75, 0.2),
                    should_cancel=should_cancel,
                )
            except (SceneIndexJobCancelled, FaceIndexJobCancelled, VisualQualityJobCancelled):
                raise MulticamVisualJobCancelled() from None
            entries.append(IsoVisualIndex(
                source_asset_id=source.id,
                source_sha256=source.sha256,
                offset_us=camera.offset_us,
                status="indexed",
                scene_index_artifact_id=scene_artifact.id,
                face_index_artifact_id=face_artifact.id,
                visual_quality_artifact_id=quality_result["visual_quality_artifact_id"],
            ))

        document = MulticamVisualIndexDocument(
            project_id=sync.project_id,
            primary_source_asset_id=sync.primary_source_asset_id,
            multicam_sync_artifact_id=multicam_sync_artifact.id,
            multicam_sync_input_hash=multicam_sync_artifact.input_hash,
            input_hash=input_hash,
            cameras=entries,
            indexed_camera_count=sum(item.status == "indexed" for item in entries),
            rejected_camera_count=sum(item.status == "rejected" for item in entries),
            engine=MulticamVisualEngineInfo(
                algorithm="sync_gated_iso_visual_pipeline",
                algorithm_version=MULTICAM_VISUAL_ALGORITHM_VERSION,
                stages=["scene_index", "face_index", "visual_quality_index"],
                scene_threshold=self._config.analysis.scene_threshold,
                face_sample_fps=self._config.analysis.face_sample_fps,
                visual_quality_sample_fps=self._config.analysis.visual_quality_sample_fps,
            ),
        )
        progress_cb(95.0, "Persistindo MulticamVisualIndexArtifact")
        out_dir = multicam_visual_dir(self._config, sync.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"multicam-visual-{input_hash[:16]}.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if should_cancel():
            temporary_path.unlink(missing_ok=True)
            raise MulticamVisualJobCancelled()
        temporary_path.replace(output_path)
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=sync.project_id,
            stage="multicam_visual_index",
            schema_version=MULTICAM_VISUAL_SCHEMA_VERSION,
            path=str(output_path),
            input_hash=input_hash,
            metadata={
                "multicam_sync_artifact_id": multicam_sync_artifact.id,
                "indexed_camera_count": document.indexed_camera_count,
                "rejected_camera_count": document.rejected_camera_count,
            },
        ))
        progress_cb(100.0, "Índices visuais multicamera concluídos")
        return {
            "cached": False,
            "multicam_visual_artifact_id": artifact.id,
            "multicam_visual_path": artifact.path,
            "schema_version": artifact.schema_version,
        }
