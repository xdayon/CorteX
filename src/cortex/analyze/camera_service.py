from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from cortex.analyze.camera_schemas import (
    CAMERA_TIMELINE_SCHEMA_VERSION,
    CameraScene,
    CameraTimelineDocument,
    CameraTimelineEngineInfo,
)
from cortex.analyze.face_schemas import FaceIndexDocument, SceneFaceSummary
from cortex.analyze.scene_schemas import SceneIndexDocument, SceneSegment
from cortex.analyze.speaker_schemas import SpeakerSegment, SpeakerTimelineDocument
from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore
from cortex.paths import cameras_dir

CAMERA_ALGORITHM_VERSION = "1.0.0"
DOMINANT_SPEAKER_MIN_SHARE = 0.55


class CameraTimelineJobCancelled(RuntimeError):
    pass


class CameraTimelinePreconditionError(ValueError):
    pass


def _overlap_us(start_us: int, end_us: int, segment: SpeakerSegment) -> int:
    return max(0, min(end_us, segment.end_us) - max(start_us, segment.start_us))


def _layout_id(shot_type: str, track_ids: list[str]) -> str:
    payload = f"{shot_type}|{'|'.join(sorted(track_ids))}"
    return "layout-" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def classify_camera_scene(
    scene: SceneSegment,
    face_summary: SceneFaceSummary | None,
    speaker_segments: list[SpeakerSegment],
) -> CameraScene:
    start_us = round(scene.start * 1_000_000)
    end_us = round(scene.end * 1_000_000)
    duration_us = max(1, end_us - start_us)
    shot_type = face_summary.dominant_shot_type if face_summary else "none"
    visible = sorted(set(face_summary.track_ids_present if face_summary else []))
    speaker_durations: dict[str, int] = {}
    speech_us = 0
    weighted_confidence = 0.0
    for segment in speaker_segments:
        overlap = _overlap_us(start_us, end_us, segment)
        if overlap <= 0 or segment.state == "no_speech":
            continue
        speech_us += overlap
        weighted_confidence += overlap * segment.confidence
        if segment.state == "speaker" and segment.speaker_track_id:
            speaker_durations[segment.speaker_track_id] = (
                speaker_durations.get(segment.speaker_track_id, 0) + overlap
            )

    dominant_track = None
    dominant_share = 0.0
    if speaker_durations and speech_us:
        dominant_track, dominant_duration = max(
            speaker_durations.items(), key=lambda item: (item[1], item[0])
        )
        dominant_share = dominant_duration / speech_us
        if dominant_share < DOMINANT_SPEAKER_MIN_SHARE:
            dominant_track = None

    evidence = [f"shot:{shot_type}", f"visible_tracks:{len(visible)}"]
    if speech_us == 0:
        alignment = "no_speech"
    elif dominant_track is None:
        alignment = "unavailable" if not speaker_durations else "ambiguous"
    elif dominant_track in visible:
        alignment = "confirmed"
        evidence.append("dominant_speaker_visible")
    else:
        alignment = "ambiguous"
        evidence.append("speaker_track_not_confirmed_in_layout")

    if shot_type == "none" or not visible:
        role = "no_face"
    elif shot_type == "two_shot":
        role = "two_shot"
    elif shot_type == "wide":
        role = "wide"
    elif shot_type == "close" and len(visible) == 1 and alignment == "confirmed":
        role = "speaker_close"
    else:
        role = "unknown"

    speech_coverage = min(1.0, speech_us / duration_us)
    speech_confidence = weighted_confidence / speech_us if speech_us else 1.0
    visual_confidence = 1.0 if face_summary and face_summary.sample_count > 0 else 0.0
    confidence = min(1.0, 0.55 * visual_confidence + 0.45 * speech_confidence)
    if role == "unknown":
        confidence = min(confidence, 0.49)
    return CameraScene(
        scene_index=scene.index,
        start_us=start_us,
        end_us=end_us,
        layout_id=_layout_id(shot_type, visible),
        shot_type=shot_type,
        role=role,
        visible_track_ids=visible,
        dominant_speaker_track_id=dominant_track,
        speaker_alignment=alignment,
        speech_coverage=round(speech_coverage, 6),
        confidence=round(confidence, 6),
        evidence=evidence,
    )


class CameraTimelineService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def run(
        self,
        *,
        scene_index_artifact: StageArtifact,
        face_index_artifact: StageArtifact,
        speaker_timeline_artifact: StageArtifact,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        if should_cancel():
            raise CameraTimelineJobCancelled()
        paths = [
            Path(scene_index_artifact.path),
            Path(face_index_artifact.path),
            Path(speaker_timeline_artifact.path),
        ]
        if not all(path.exists() for path in paths):
            raise CameraTimelinePreconditionError("artifact upstream da timeline de cameras ausente")
        progress_cb(0.0, "Validando cenas, faces e speakers")
        scenes = SceneIndexDocument.model_validate_json(paths[0].read_text(encoding="utf-8"))
        faces = FaceIndexDocument.model_validate_json(paths[1].read_text(encoding="utf-8"))
        speakers = SpeakerTimelineDocument.model_validate_json(paths[2].read_text(encoding="utf-8"))
        if (
            faces.project_id != scenes.project_id
            or faces.source_asset_id != scenes.source_asset_id
            or faces.source_sha256 != scenes.source_sha256
            or faces.scene_index_artifact_id != scene_index_artifact.id
        ):
            raise CameraTimelinePreconditionError("face_index não corresponde ao scene_index")
        if (
            speakers.project_id != scenes.project_id
            or speakers.source_asset_id != scenes.source_asset_id
            or speakers.source_sha256 != scenes.source_sha256
            or speakers.scene_index_artifact_id != scene_index_artifact.id
            or speakers.face_index_artifact_id != face_index_artifact.id
        ):
            raise CameraTimelinePreconditionError("speaker_timeline não corresponde à cadeia visual")

        hash_payload = {
            "algorithm": CAMERA_ALGORITHM_VERSION,
            "schema_version": CAMERA_TIMELINE_SCHEMA_VERSION,
            "scene_index": [scene_index_artifact.id, scene_index_artifact.input_hash],
            "face_index": [face_index_artifact.id, face_index_artifact.input_hash],
            "speaker_timeline": [speaker_timeline_artifact.id, speaker_timeline_artifact.input_hash],
            "dominant_speaker_min_share": DOMINANT_SPEAKER_MIN_SHARE,
            "identity_scope": "layout_track_only",
        }
        input_hash = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cached = self._domain.find_cached_stage_artifact(
            project_id=scenes.project_id,
            stage="camera_timeline",
            input_hash=input_hash,
            schema_version=CAMERA_TIMELINE_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Timeline de cameras em cache reutilizada")
            return {
                "cached": True,
                "camera_timeline_artifact_id": cached.id,
                "camera_timeline_path": cached.path,
                "schema_version": cached.schema_version,
            }

        summaries = {summary.scene_index: summary for summary in faces.scenes}
        camera_scenes: list[CameraScene] = []
        for index, scene in enumerate(scenes.scenes):
            if should_cancel():
                raise CameraTimelineJobCancelled()
            camera_scenes.append(classify_camera_scene(
                scene, summaries.get(scene.index), speakers.segments
            ))
            progress_cb(
                10.0 + 75.0 * (index + 1) / max(len(scenes.scenes), 1),
                f"Classificando camera da cena {scene.index}",
            )

        document = CameraTimelineDocument(
            project_id=scenes.project_id,
            source_asset_id=scenes.source_asset_id,
            source_sha256=scenes.source_sha256,
            scene_index_artifact_id=scene_index_artifact.id,
            scene_index_input_hash=scene_index_artifact.input_hash,
            face_index_artifact_id=face_index_artifact.id,
            face_index_input_hash=face_index_artifact.input_hash,
            speaker_timeline_artifact_id=speaker_timeline_artifact.id,
            speaker_timeline_input_hash=speaker_timeline_artifact.input_hash,
            input_hash=input_hash,
            duration_us=round(scenes.duration_seconds * 1_000_000),
            scenes=camera_scenes,
            engine=CameraTimelineEngineInfo(
                algorithm="scene_face_speaker_alignment",
                algorithm_version=CAMERA_ALGORITHM_VERSION,
                dominant_speaker_min_share=DOMINANT_SPEAKER_MIN_SHARE,
            ),
        )
        progress_cb(90.0, "Persistindo CameraTimelineArtifact")
        out_dir = cameras_dir(self._config, scenes.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"camera-timeline-{input_hash[:16]}.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if should_cancel():
            temporary_path.unlink(missing_ok=True)
            raise CameraTimelineJobCancelled()
        temporary_path.replace(output_path)
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=scenes.project_id,
            stage="camera_timeline",
            schema_version=CAMERA_TIMELINE_SCHEMA_VERSION,
            path=str(output_path),
            input_hash=input_hash,
            metadata={
                "scene_index_artifact_id": scene_index_artifact.id,
                "face_index_artifact_id": face_index_artifact.id,
                "speaker_timeline_artifact_id": speaker_timeline_artifact.id,
                "scene_count": len(camera_scenes),
                "identity_scope": "layout_track_only",
            },
        ))
        progress_cb(100.0, "Timeline de cameras concluida")
        return {
            "cached": False,
            "camera_timeline_artifact_id": artifact.id,
            "camera_timeline_path": artifact.path,
            "schema_version": artifact.schema_version,
        }
