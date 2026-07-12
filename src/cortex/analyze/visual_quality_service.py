from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np

from cortex.analyze.face_service import _extract_frame_rgb
from cortex.analyze.face_schemas import FaceIndexDocument, FrameFaces
from cortex.analyze.scene_detect import ffmpeg_version
from cortex.analyze.scene_schemas import SceneIndexDocument, SceneSegment
from cortex.analyze.visual_quality import face_touches_edge, frame_metrics, normalized_frame_delta
from cortex.analyze.visual_quality_schemas import (
    VISUAL_QUALITY_SCHEMA_VERSION,
    SceneVisualQuality,
    VisualQualityDocument,
    VisualQualityEngineInfo,
    VisualQualityFrame,
)
from cortex.config import CortexConfig
from cortex.domain.models import SourceAsset, StageArtifact
from cortex.domain.store import DomainStore
from cortex.paths import visual_quality_dir

VISUAL_QUALITY_ALGORITHM_VERSION = "1.0.0"
FRAME_WIDTH = 640
BLACK_LUMA_THRESHOLD = 16.0
BLACK_PIXEL_RATIO_THRESHOLD = 0.98
BLUR_SCORE_THRESHOLD = 20.0
FREEZE_DELTA_THRESHOLD = 0.002
ISSUE_SHARE_THRESHOLD = 0.5
FACE_EDGE_MARGIN = 0.01


class VisualQualityJobCancelled(RuntimeError):
    pass


class VisualQualityPreconditionError(ValueError):
    pass


def _scene_timestamps(scene: SceneSegment, sample_fps: float) -> list[float]:
    duration = scene.end - scene.start
    if duration <= 0:
        return []
    step = 1.0 / sample_fps
    timestamps: list[float] = []
    timestamp = scene.start + min(step / 2.0, duration / 2.0)
    while timestamp < scene.end:
        timestamps.append(round(timestamp, 6))
        timestamp += step
    return timestamps or [round((scene.start + scene.end) / 2.0, 6)]


def _nearest_face_frame(
    frames: list[FrameFaces], timestamp: float, max_distance: float
) -> FrameFaces | None:
    if not frames:
        return None
    nearest = min(frames, key=lambda frame: (abs(frame.time - timestamp), frame.time))
    return nearest if abs(nearest.time - timestamp) <= max_distance else None


def _aggregate_scene(
    scene: SceneSegment, frames: list[VisualQualityFrame]
) -> SceneVisualQuality:
    count = len(frames)
    black_share = sum(frame.is_black for frame in frames) / max(count, 1)
    blurred_share = sum(frame.is_blurred for frame in frames) / max(count, 1)
    delta_frames = [frame for frame in frames if frame.frame_delta is not None]
    frozen_share = sum(frame.is_frozen for frame in delta_frames) / max(len(delta_frames), 1)
    face_frames = [frame for frame in frames if frame.face_edge_occlusion is not None]
    occlusion_share = (
        sum(bool(frame.face_edge_occlusion) for frame in face_frames) / len(face_frames)
        if face_frames else None
    )
    issues: list[str] = []
    if black_share >= ISSUE_SHARE_THRESHOLD:
        issues.append("black")
    if blurred_share >= ISSUE_SHARE_THRESHOLD:
        issues.append("blurred")
    if frozen_share >= ISSUE_SHARE_THRESHOLD:
        issues.append("frozen")
    if occlusion_share is not None and occlusion_share >= ISSUE_SHARE_THRESHOLD:
        issues.append("face_edge_occlusion")
    return SceneVisualQuality(
        scene_index=scene.index,
        start_us=round(scene.start * 1_000_000),
        end_us=round(scene.end * 1_000_000),
        sample_count=count,
        black_share=round(black_share, 6),
        blurred_share=round(blurred_share, 6),
        frozen_share=round(frozen_share, 6),
        face_edge_occlusion_share=(round(occlusion_share, 6) if occlusion_share is not None else None),
        usable=not issues,
        issues=issues,
    )


class VisualQualityService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def run(
        self,
        *,
        source_asset: SourceAsset,
        scene_index_artifact: StageArtifact,
        face_index_artifact: StageArtifact,
        sample_fps: float | None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        if should_cancel():
            raise VisualQualityJobCancelled()
        source_path = self._config.paths.data_dir / source_asset.stored_path
        scene_path = Path(scene_index_artifact.path)
        face_path = Path(face_index_artifact.path)
        if not source_path.exists():
            raise VisualQualityPreconditionError("arquivo da fonte não está disponível")
        if not scene_path.exists() or not face_path.exists():
            raise VisualQualityPreconditionError("artifact upstream de qualidade visual ausente")

        progress_cb(0.0, "Validando fonte, cenas e faces")
        scenes = SceneIndexDocument.model_validate_json(scene_path.read_text(encoding="utf-8"))
        faces = FaceIndexDocument.model_validate_json(face_path.read_text(encoding="utf-8"))
        if (
            scenes.project_id != source_asset.project_id
            or scenes.source_asset_id != source_asset.id
            or scenes.source_sha256 != source_asset.sha256
        ):
            raise VisualQualityPreconditionError("scene_index não corresponde à fonte")
        if (
            faces.project_id != source_asset.project_id
            or faces.source_asset_id != source_asset.id
            or faces.source_sha256 != source_asset.sha256
            or faces.scene_index_artifact_id != scene_index_artifact.id
        ):
            raise VisualQualityPreconditionError("face_index não corresponde à cadeia visual")

        effective_sample_fps = round(
            float(sample_fps if sample_fps is not None else self._config.analysis.visual_quality_sample_fps),
            4,
        )
        effective_ffmpeg_version = ffmpeg_version(self._config.render.ffmpeg)
        hash_payload = {
            "algorithm": VISUAL_QUALITY_ALGORITHM_VERSION,
            "schema_version": VISUAL_QUALITY_SCHEMA_VERSION,
            "source_asset": [source_asset.id, source_asset.sha256],
            "scene_index": [scene_index_artifact.id, scene_index_artifact.input_hash],
            "face_index": [face_index_artifact.id, face_index_artifact.input_hash],
            "sample_fps": effective_sample_fps,
            "frame_width": FRAME_WIDTH,
            "thresholds": [
                BLACK_LUMA_THRESHOLD, BLACK_PIXEL_RATIO_THRESHOLD, BLUR_SCORE_THRESHOLD,
                FREEZE_DELTA_THRESHOLD, ISSUE_SHARE_THRESHOLD, FACE_EDGE_MARGIN,
            ],
            "ffmpeg_version": effective_ffmpeg_version,
            "decoder": "software",
        }
        input_hash = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cached = self._domain.find_cached_stage_artifact(
            project_id=source_asset.project_id,
            stage="visual_quality_index",
            input_hash=input_hash,
            schema_version=VISUAL_QUALITY_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Índice de qualidade visual em cache reutilizado")
            return {
                "cached": True,
                "visual_quality_artifact_id": cached.id,
                "visual_quality_path": cached.path,
                "schema_version": cached.schema_version,
            }

        timestamps = [
            (scene, timestamp)
            for scene in scenes.scenes
            for timestamp in _scene_timestamps(scene, effective_sample_fps)
        ]
        quality_frames: list[VisualQualityFrame] = []
        previous_by_scene: dict[int, np.ndarray] = {}
        face_max_distance = max(0.05, 0.55 / faces.engine.sample_fps)
        for position, (scene, timestamp) in enumerate(timestamps):
            if should_cancel():
                raise VisualQualityJobCancelled()
            frame = _extract_frame_rgb(self._config.render.ffmpeg, source_path, timestamp)
            mean_luma, black_ratio, blur_score = frame_metrics(frame)
            previous = previous_by_scene.get(scene.index)
            delta = normalized_frame_delta(previous, frame) if previous is not None else None
            previous_by_scene[scene.index] = frame
            face_frame = _nearest_face_frame(faces.frames, timestamp, face_max_distance)
            edge_occlusion = None
            if face_frame is not None and face_frame.faces:
                edge_occlusion = any(face_touches_edge(face, FACE_EDGE_MARGIN) for face in face_frame.faces)
            quality_frames.append(VisualQualityFrame(
                scene_index=scene.index,
                time_us=round(timestamp * 1_000_000),
                mean_luma=round(mean_luma, 6),
                black_pixel_ratio=round(black_ratio, 6),
                blur_score=round(blur_score, 6),
                frame_delta=(round(delta, 8) if delta is not None else None),
                is_black=mean_luma <= BLACK_LUMA_THRESHOLD and black_ratio >= BLACK_PIXEL_RATIO_THRESHOLD,
                is_blurred=blur_score <= BLUR_SCORE_THRESHOLD,
                is_frozen=delta is not None and delta <= FREEZE_DELTA_THRESHOLD,
                face_edge_occlusion=edge_occlusion,
            ))
            progress_cb(
                5.0 + 80.0 * (position + 1) / max(len(timestamps), 1),
                f"Medindo qualidade visual em {timestamp:.2f}s",
            )

        frames_by_scene = {
            scene.index: [frame for frame in quality_frames if frame.scene_index == scene.index]
            for scene in scenes.scenes
        }
        scene_quality = [_aggregate_scene(scene, frames_by_scene[scene.index]) for scene in scenes.scenes]
        document = VisualQualityDocument(
            project_id=source_asset.project_id,
            source_asset_id=source_asset.id,
            source_sha256=source_asset.sha256,
            scene_index_artifact_id=scene_index_artifact.id,
            scene_index_input_hash=scene_index_artifact.input_hash,
            face_index_artifact_id=face_index_artifact.id,
            face_index_input_hash=face_index_artifact.input_hash,
            input_hash=input_hash,
            duration_us=round(scenes.duration_seconds * 1_000_000),
            frames=quality_frames,
            scenes=scene_quality,
            engine=VisualQualityEngineInfo(
                algorithm="sampled_luma_laplacian_delta_face_edge",
                algorithm_version=VISUAL_QUALITY_ALGORITHM_VERSION,
                sample_fps_requested=effective_sample_fps,
                sample_fps_effective=effective_sample_fps,
                frame_width=FRAME_WIDTH,
                black_luma_threshold=BLACK_LUMA_THRESHOLD,
                black_pixel_ratio_threshold=BLACK_PIXEL_RATIO_THRESHOLD,
                blur_score_threshold=BLUR_SCORE_THRESHOLD,
                freeze_delta_threshold=FREEZE_DELTA_THRESHOLD,
                issue_share_threshold=ISSUE_SHARE_THRESHOLD,
                face_edge_margin=FACE_EDGE_MARGIN,
                ffmpeg_path=str(self._config.render.ffmpeg),
                ffmpeg_version=effective_ffmpeg_version,
                decoder_requested="software",
                decoder_effective="software",
                device_requested="cpu",
                device_effective="cpu",
            ),
        )
        progress_cb(90.0, "Persistindo VisualQualityArtifact")
        out_dir = visual_quality_dir(self._config, source_asset.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"visual-quality-{input_hash[:16]}.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if should_cancel():
            temporary_path.unlink(missing_ok=True)
            raise VisualQualityJobCancelled()
        temporary_path.replace(output_path)
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=source_asset.project_id,
            stage="visual_quality_index",
            schema_version=VISUAL_QUALITY_SCHEMA_VERSION,
            path=str(output_path),
            input_hash=input_hash,
            metadata={
                "source_asset_id": source_asset.id,
                "scene_index_artifact_id": scene_index_artifact.id,
                "face_index_artifact_id": face_index_artifact.id,
                "sample_fps": effective_sample_fps,
                "scene_count": len(scene_quality),
                "unusable_scene_count": sum(not scene.usable for scene in scene_quality),
            },
        ))
        progress_cb(100.0, "Índice de qualidade visual concluído")
        return {
            "cached": False,
            "visual_quality_artifact_id": artifact.id,
            "visual_quality_path": artifact.path,
            "schema_version": artifact.schema_version,
        }
