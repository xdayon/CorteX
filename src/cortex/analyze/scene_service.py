from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from cortex.analyze.scene_detect import (
    SceneDetectionError,
    build_scenes,
    ffmpeg_version,
    parse_scene_cuts,
    scdet_command,
)
from cortex.analyze.scene_schemas import (
    SCENE_INDEX_SCHEMA_VERSION,
    SceneCut,
    SceneIndexDocument,
    SceneIndexEngineInfo,
    SceneSegment,
)
from cortex.config import CortexConfig
from cortex.domain.models import SourceAsset, StageArtifact
from cortex.domain.store import DomainStore
from cortex.ingest.ffprobe import duration_seconds, probe_media
from cortex.paths import scenes_dir

SCENE_ALGORITHM_VERSION = "1.0.0"


class SceneIndexJobCancelled(RuntimeError):
    pass


class SceneIndexPreconditionError(ValueError):
    pass


def _run_scdet(command: list[str], log_path: Path, should_cancel: Callable[[], bool], timeout: int = 3600) -> str:
    with log_path.open("w+b") as log:
        try:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=log)
        except OSError as exc:
            raise SceneDetectionError(f"ffmpeg scdet não pôde ser executado: {exc}") from exc
        started = time.monotonic()
        while process.poll() is None:
            if should_cancel():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise SceneIndexJobCancelled()
            if time.monotonic() - started > timeout:
                process.kill()
                raise SceneDetectionError(f"ffmpeg scdet excedeu o timeout de {timeout}s")
            time.sleep(0.05)
        log.seek(0)
        stderr_text = log.read().decode(errors="replace")
        if process.returncode:
            raise SceneDetectionError(f"ffmpeg scdet falhou ({process.returncode}): {stderr_text[-2000:]}")
    return stderr_text


class SceneIndexService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def run(
        self,
        *,
        source_asset: SourceAsset,
        threshold: float | None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        if should_cancel():
            raise SceneIndexJobCancelled()
        progress_cb(0.0, "Lendo metadados da fonte")
        source_path = self._config.paths.data_dir / source_asset.stored_path
        if not source_path.exists():
            raise SceneIndexPreconditionError("arquivo da fonte não está disponível para detecção de cenas")
        probe = probe_media(self._config.render.ffprobe, source_path)
        duration = duration_seconds(probe)

        requested_threshold = (
            round(float(threshold), 4) if threshold is not None
            else round(self._config.analysis.scene_threshold, 4)
        )
        effective_ffmpeg_version = ffmpeg_version(self._config.render.ffmpeg)

        hash_payload = {
            "algorithm": SCENE_ALGORITHM_VERSION,
            "schema_version": SCENE_INDEX_SCHEMA_VERSION,
            "source_asset_id": source_asset.id,
            "source_sha256": source_asset.sha256,
            "threshold": requested_threshold,
            "filter": "scdet",
        }
        input_hash = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        cached = self._domain.find_cached_stage_artifact(
            project_id=source_asset.project_id, stage="scene_index", input_hash=input_hash,
            schema_version=SCENE_INDEX_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Índice de cenas em cache reutilizado")
            return {
                "cached": True, "scene_index_artifact_id": cached.id,
                "scene_index_path": cached.path, "schema_version": cached.schema_version,
            }

        if should_cancel():
            raise SceneIndexJobCancelled()
        progress_cb(20.0, "Executando ffmpeg scdet")
        out_dir = scenes_dir(self._config, source_asset.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir / f"scene-index-{input_hash[:16]}.ffmpeg.log"
        stderr_text = _run_scdet(
            scdet_command(self._config.render.ffmpeg, source_path, requested_threshold),
            log_path, should_cancel,
        )

        if should_cancel():
            raise SceneIndexJobCancelled()
        progress_cb(70.0, "Consolidando cortes de cena")
        raw_cuts = parse_scene_cuts(stderr_text)
        cuts = [SceneCut(time=t, score=s) for t, s in raw_cuts]
        scenes = [SceneSegment(**scene) for scene in build_scenes(raw_cuts, duration)]

        document = SceneIndexDocument(
            project_id=source_asset.project_id, source_asset_id=source_asset.id,
            source_sha256=source_asset.sha256, input_hash=input_hash,
            duration_seconds=round(duration, 4), cuts=cuts, scenes=scenes, cut_count=len(cuts),
            engine=SceneIndexEngineInfo(
                ffmpeg_path=str(self._config.render.ffmpeg), ffmpeg_version=effective_ffmpeg_version,
                filter="scdet", threshold_requested=requested_threshold,
                threshold_effective=requested_threshold,
            ),
        )

        if should_cancel():
            raise SceneIndexJobCancelled()
        progress_cb(95.0, "Persistindo SceneIndexArtifact")
        output_path = out_dir / f"scene-index-{input_hash[:16]}.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if should_cancel():
            temporary_path.unlink(missing_ok=True)
            raise SceneIndexJobCancelled()
        temporary_path.replace(output_path)
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=source_asset.project_id, stage="scene_index",
            schema_version=SCENE_INDEX_SCHEMA_VERSION, path=str(output_path), input_hash=input_hash,
            metadata={
                "source_asset_id": source_asset.id,
                "requested_threshold": requested_threshold,
                "effective_threshold": requested_threshold,
                "cut_count": len(cuts),
                "ffmpeg_version": effective_ffmpeg_version,
            },
        ))
        progress_cb(100.0, "Índice de cenas concluído")
        return {
            "cached": False, "scene_index_artifact_id": artifact.id,
            "scene_index_path": artifact.path, "schema_version": artifact.schema_version,
        }
