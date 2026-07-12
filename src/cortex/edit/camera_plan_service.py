from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from cortex.analyze.camera_schemas import CameraTimelineDocument
from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument
from cortex.analyze.multicam_visual_schemas import MulticamVisualIndexDocument
from cortex.analyze.scene_schemas import SceneIndexDocument
from cortex.analyze.visual_quality_schemas import VisualQualityDocument
from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore, StageArtifactNotFoundError
from cortex.edit.camera_plan_schemas import (
    CAMERA_EDIT_PLAN_SCHEMA_VERSION,
    CameraEditDiagnostics,
    CameraEditEngineInfo,
    CameraEditPlanDocument,
    CameraEditShot,
)
from cortex.edit.camera_planner import classify_shot_intent
from cortex.edit.schemas import EditPlanDocument
from cortex.paths import camera_plans_dir

CAMERA_PLAN_ALGORITHM_VERSION = "2.0.0"
BASE_REACTION_BLOCKERS = [
    "acoustic_speaker_identity_unavailable",
    "listening_posture_unavailable",
]


class CameraEditPlanJobCancelled(RuntimeError):
    pass


class CameraEditPlanPreconditionError(ValueError):
    pass


class CameraEditPlanService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def _load_iso_cameras(
        self, multicam: MulticamVisualIndexDocument
    ) -> list[dict]:
        output: list[dict] = []
        for camera in multicam.cameras:
            if camera.status != "indexed":
                continue
            artifact_specs = (
                (camera.scene_index_artifact_id, "scene_index"),
                (camera.face_index_artifact_id, "face_index"),
                (camera.visual_quality_artifact_id, "visual_quality_index"),
            )
            artifacts = []
            for artifact_id, expected_stage in artifact_specs:
                try:
                    artifact = self._domain.get_stage_artifact(str(artifact_id))
                except StageArtifactNotFoundError as exc:
                    raise CameraEditPlanPreconditionError(
                        f"artifact ISO {artifact_id} não encontrado"
                    ) from exc
                if artifact.project_id != multicam.project_id or artifact.stage != expected_stage:
                    raise CameraEditPlanPreconditionError(
                        f"artifact ISO {artifact_id} não corresponde ao manifest"
                    )
                if not Path(artifact.path).exists():
                    raise CameraEditPlanPreconditionError(
                        f"arquivo do artifact ISO {artifact_id} não está disponível"
                    )
                artifacts.append(artifact)
            scenes = SceneIndexDocument.model_validate_json(
                Path(artifacts[0].path).read_text(encoding="utf-8")
            )
            faces = FaceIndexDocument.model_validate_json(
                Path(artifacts[1].path).read_text(encoding="utf-8")
            )
            quality = VisualQualityDocument.model_validate_json(
                Path(artifacts[2].path).read_text(encoding="utf-8")
            )
            if (
                scenes.source_asset_id != camera.source_asset_id
                or scenes.source_sha256 != camera.source_sha256
                or faces.source_asset_id != camera.source_asset_id
                or faces.scene_index_artifact_id != artifacts[0].id
                or quality.source_asset_id != camera.source_asset_id
                or quality.scene_index_artifact_id != artifacts[0].id
                or quality.face_index_artifact_id != artifacts[1].id
            ):
                raise CameraEditPlanPreconditionError(
                    f"cadeia visual ISO {camera.source_asset_id} inconsistente"
                )
            output.append({
                "source_asset_id": camera.source_asset_id,
                "offset_us": camera.offset_us,
                "scenes": scenes,
                "face_summaries": {item.scene_index: item for item in faces.scenes},
                "quality": {item.scene_index: item for item in quality.scenes},
            })
        return output

    @staticmethod
    def _select_iso_context(primary_shot: CameraEditShot, iso_cameras: list[dict]) -> CameraEditShot:
        candidates: list[tuple[int, float, str, dict, object, object]] = []
        for iso in iso_cameras:
            video_start = primary_shot.audio_source_start_us + iso["offset_us"]
            video_end = primary_shot.audio_source_end_us + iso["offset_us"]
            if video_start < 0:
                continue
            scene = next((
                item for item in iso["scenes"].scenes
                if round(item.start * 1_000_000) <= video_start
                and round(item.end * 1_000_000) >= video_end
            ), None)
            if scene is None:
                continue
            summary = iso["face_summaries"].get(scene.index)
            quality = iso["quality"].get(scene.index)
            if summary is None or quality is None or not quality.usable:
                continue
            if summary.dominant_shot_type not in {"two_shot", "wide"}:
                continue
            priority = 2 if summary.dominant_shot_type == "two_shot" else 1
            penalty = (
                quality.black_share + quality.blurred_share + quality.frozen_share
                + (quality.face_edge_occlusion_share or 0.0)
            )
            candidates.append((
                -priority, penalty, iso["source_asset_id"], iso, scene, summary,
            ))
        if not candidates:
            return primary_shot
        _priority, _penalty, source_id, iso, scene, summary = min(candidates)
        video_start = primary_shot.audio_source_start_us + iso["offset_us"]
        video_end = primary_shot.audio_source_end_us + iso["offset_us"]
        return primary_shot.model_copy(update={
            "video_source_asset_id": source_id,
            "source_start_us": video_start,
            "source_end_us": video_end,
            "sync_offset_us": iso["offset_us"],
            "scene_index": scene.index,
            "layout_id": f"iso-{source_id[:8]}-scene-{scene.index}",
            "camera_role": summary.dominant_shot_type,
            "intent": "context",
            "confirmed_identity_ids": [],
            "visual_quality_usable": True,
            "evidence": [
                f"iso_context:{summary.dominant_shot_type}",
                "multicam_sync_confirmed",
                f"sync_offset_us:{iso['offset_us']}",
                "same_global_time",
                "visual_quality_usable",
                "primary_audio_continuous",
                "temporal_reuse_forbidden",
            ],
        })

    def run(
        self,
        *,
        edit_plan_artifact: StageArtifact,
        camera_timeline_artifact: StageArtifact,
        identity_index_artifact: StageArtifact,
        visual_quality_artifact: StageArtifact,
        multicam_visual_artifact: StageArtifact | None = None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        if should_cancel():
            raise CameraEditPlanJobCancelled()
        artifacts = [
            edit_plan_artifact, camera_timeline_artifact,
            identity_index_artifact, visual_quality_artifact,
        ]
        if multicam_visual_artifact is not None:
            artifacts.append(multicam_visual_artifact)
        paths = [Path(artifact.path) for artifact in artifacts]
        if not all(path.exists() for path in paths):
            raise CameraEditPlanPreconditionError("artifact upstream do camera plan ausente")
        progress_cb(0.0, "Validando EDL, cameras, identidades e qualidade")
        edit_plan = EditPlanDocument.model_validate_json(paths[0].read_text(encoding="utf-8"))
        cameras = CameraTimelineDocument.model_validate_json(paths[1].read_text(encoding="utf-8"))
        identities = IdentityIndexDocument.model_validate_json(paths[2].read_text(encoding="utf-8"))
        quality = VisualQualityDocument.model_validate_json(paths[3].read_text(encoding="utf-8"))
        multicam = (
            MulticamVisualIndexDocument.model_validate_json(paths[4].read_text(encoding="utf-8"))
            if multicam_visual_artifact is not None else None
        )
        if any(artifact.project_id != edit_plan.project_id for artifact in artifacts):
            raise CameraEditPlanPreconditionError("artifacts do camera plan não pertencem ao projeto")
        if (
            cameras.source_asset_id != edit_plan.source_asset_id
            or identities.source_asset_id != edit_plan.source_asset_id
            or quality.source_asset_id != edit_plan.source_asset_id
        ):
            raise CameraEditPlanPreconditionError("artifacts do camera plan não correspondem à fonte")
        if identities.camera_timeline_artifact_id != camera_timeline_artifact.id:
            raise CameraEditPlanPreconditionError("identity_index não corresponde à camera_timeline")
        if (
            quality.scene_index_artifact_id != cameras.scene_index_artifact_id
            or quality.face_index_artifact_id != cameras.face_index_artifact_id
        ):
            raise CameraEditPlanPreconditionError("visual_quality não corresponde à cadeia visual")
        if multicam is not None and (
            multicam.project_id != edit_plan.project_id
            or multicam.primary_source_asset_id != edit_plan.source_asset_id
        ):
            raise CameraEditPlanPreconditionError("multicam_visual não corresponde à EDL primária")

        iso_cameras = self._load_iso_cameras(multicam) if multicam is not None else []
        reaction_blockers = list(BASE_REACTION_BLOCKERS)
        if not iso_cameras:
            reaction_blockers.append("alternate_camera_source_unavailable")

        hash_payload = {
            "algorithm": CAMERA_PLAN_ALGORITHM_VERSION,
            "schema_version": CAMERA_EDIT_PLAN_SCHEMA_VERSION,
            "edit_plan": [edit_plan_artifact.id, edit_plan_artifact.input_hash],
            "camera_timeline": [camera_timeline_artifact.id, camera_timeline_artifact.input_hash],
            "identity_index": [identity_index_artifact.id, identity_index_artifact.input_hash],
            "visual_quality": [visual_quality_artifact.id, visual_quality_artifact.input_hash],
            "audio_continuity_mode": "primary_source_continuous",
            "temporal_reuse_allowed": False,
            "reaction_blockers": reaction_blockers,
        }
        if multicam_visual_artifact is not None:
            hash_payload["multicam_visual"] = [
                multicam_visual_artifact.id, multicam_visual_artifact.input_hash,
            ]
        input_hash = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cached = self._domain.find_cached_stage_artifact(
            project_id=edit_plan.project_id,
            stage="camera_edit_plan",
            input_hash=input_hash,
            schema_version=CAMERA_EDIT_PLAN_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Camera edit plan em cache reutilizado")
            return {
                "cached": True,
                "camera_edit_plan_artifact_id": cached.id,
                "camera_edit_plan_path": cached.path,
                "schema_version": cached.schema_version,
            }

        quality_by_scene = {scene.scene_index: scene for scene in quality.scenes}
        confirmed_by_scene: dict[int, set[str]] = {}
        for observation in identities.observations:
            if observation.status == "confirmed":
                confirmed_by_scene.setdefault(observation.scene_index, set()).add(
                    observation.identity_id
                )

        shots: list[CameraEditShot] = []
        total_segments = max(len(edit_plan.segments), 1)
        for segment_position, segment in enumerate(edit_plan.segments):
            segment_start = round(segment.start * 1_000_000)
            segment_end = round(segment.end * 1_000_000)
            for camera in cameras.scenes:
                start_us = max(segment_start, camera.start_us)
                end_us = min(segment_end, camera.end_us)
                if end_us <= start_us:
                    continue
                intent, evidence = classify_shot_intent(camera, quality_by_scene.get(camera.scene_index))
                primary_shot = CameraEditShot(
                    edit_segment_order=segment.timeline_order,
                    video_source_asset_id=edit_plan.source_asset_id,
                    audio_source_asset_id=edit_plan.source_asset_id,
                    source_start_us=start_us,
                    source_end_us=end_us,
                    audio_source_start_us=start_us,
                    audio_source_end_us=end_us,
                    scene_index=camera.scene_index,
                    layout_id=camera.layout_id,
                    camera_role=camera.role,
                    intent=intent,
                    confirmed_identity_ids=sorted(confirmed_by_scene.get(camera.scene_index, set())),
                    visual_quality_usable=bool(
                        quality_by_scene.get(camera.scene_index)
                        and quality_by_scene[camera.scene_index].usable
                    ),
                    evidence=[*evidence, "audio_video_linked", "temporal_reuse_forbidden"],
                )
                shots.append(
                    self._select_iso_context(primary_shot, iso_cameras)
                    if primary_shot.intent == "fallback" else primary_shot
                )
            progress_cb(
                10.0 + 75.0 * (segment_position + 1) / total_segments,
                f"Planejando cameras do segmento {segment.timeline_order}",
            )

        if not shots and edit_plan.segments:
            raise CameraEditPlanPreconditionError("camera_timeline não cobre os segmentos da EDL")
        diagnostics = CameraEditDiagnostics(
            shot_count=len(shots),
            speaker_shot_count=sum(shot.intent == "speaker" for shot in shots),
            context_shot_count=sum(shot.intent == "context" for shot in shots),
            fallback_shot_count=sum(shot.intent == "fallback" for shot in shots),
            unusable_scene_count=len({
                shot.scene_index for shot in shots if not shot.visual_quality_usable
            }),
            iso_context_shot_count=sum(
                shot.video_source_asset_id != edit_plan.source_asset_id for shot in shots
            ),
            indexed_iso_camera_count=len(iso_cameras),
            reaction_shots_blocked_by=reaction_blockers,
        )
        document = CameraEditPlanDocument(
            project_id=edit_plan.project_id,
            source_asset_id=edit_plan.source_asset_id,
            edit_plan_artifact_id=edit_plan_artifact.id,
            edit_plan_input_hash=edit_plan_artifact.input_hash,
            camera_timeline_artifact_id=camera_timeline_artifact.id,
            camera_timeline_input_hash=camera_timeline_artifact.input_hash,
            identity_index_artifact_id=identity_index_artifact.id,
            identity_index_input_hash=identity_index_artifact.input_hash,
            visual_quality_artifact_id=visual_quality_artifact.id,
            visual_quality_input_hash=visual_quality_artifact.input_hash,
            multicam_visual_artifact_id=(
                multicam_visual_artifact.id if multicam_visual_artifact is not None else None
            ),
            multicam_visual_input_hash=(
                multicam_visual_artifact.input_hash
                if multicam_visual_artifact is not None else None
            ),
            input_hash=input_hash,
            shots=shots,
            diagnostics=diagnostics,
            engine=CameraEditEngineInfo(
                algorithm="synchronized_iso_context_selection",
                algorithm_version=CAMERA_PLAN_ALGORITHM_VERSION,
            ),
        )
        progress_cb(90.0, "Persistindo CameraEditPlanArtifact")
        out_dir = camera_plans_dir(self._config, edit_plan.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"camera-edit-plan-{input_hash[:16]}.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if should_cancel():
            temporary_path.unlink(missing_ok=True)
            raise CameraEditPlanJobCancelled()
        temporary_path.replace(output_path)
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=edit_plan.project_id,
            stage="camera_edit_plan",
            schema_version=CAMERA_EDIT_PLAN_SCHEMA_VERSION,
            path=str(output_path),
            input_hash=input_hash,
            metadata={
                "edit_plan_artifact_id": edit_plan_artifact.id,
                "camera_timeline_artifact_id": camera_timeline_artifact.id,
                "identity_index_artifact_id": identity_index_artifact.id,
                "visual_quality_artifact_id": visual_quality_artifact.id,
                "multicam_visual_artifact_id": (
                    multicam_visual_artifact.id if multicam_visual_artifact is not None else None
                ),
                "shot_count": len(shots),
                "iso_context_shot_count": diagnostics.iso_context_shot_count,
                "reaction_shots_enabled": False,
                "audio_continuity_mode": "primary_source_continuous",
            },
        ))
        progress_cb(100.0, "Camera edit plan concluído")
        return {
            "cached": False,
            "camera_edit_plan_artifact_id": artifact.id,
            "camera_edit_plan_path": artifact.path,
            "schema_version": artifact.schema_version,
        }
