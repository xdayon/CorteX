from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from cortex.analyze.camera_schemas import CameraTimelineDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument
from cortex.analyze.reaction_candidate_schemas import ReactionCandidateIndexDocument
from cortex.analyze.visual_quality_schemas import VisualQualityDocument
from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore
from cortex.edit.camera_plan_schemas import (
    CAMERA_EDIT_PLAN_SCHEMA_VERSION,
    CameraEditDiagnostics,
    CameraEditEngineInfo,
    CameraEditPlanDocument,
    CameraEditShot,
)
from cortex.edit.camera_planner import classify_shot_intent
from cortex.edit.diversity_policy import (
    DIVERSITY_POLICY_VERSION,
    MAXIMUM_REACTION_DURATION_US,
    MAXIMUM_REACTION_SHARE,
    MINIMUM_GAP_BETWEEN_REACTIONS_US,
    MINIMUM_REACTION_CONFIDENCE,
    MINIMUM_REACTION_DURATION_US,
    MONOTONY_MINIMUM_DURATION_US,
    MONOTONY_THRESHOLD,
)
from cortex.edit.schemas import EditPlanDocument
from cortex.paths import camera_plans_dir

CAMERA_PLAN_ALGORITHM_VERSION = "4.1.0"

REACTION_BLOCKER_ACOUSTIC_IDENTITY_UNAVAILABLE = "acoustic_speaker_identity_unavailable"
REACTION_BLOCKER_LISTENING_POSTURE_UNAVAILABLE = "listening_posture_unavailable"
REACTION_BLOCKER_INDEX_UNAVAILABLE = "reaction_candidate_index_unavailable"
REACTION_BLOCKER_NO_SAFE_CANDIDATES = "no_safe_reaction_candidates"
REACTION_BLOCKER_LOW_CONFIDENCE = "low_confidence_candidates"
REACTION_BLOCKER_SHARE_LIMIT = "reaction_share_limit_reached"
REACTION_BLOCKER_SPACING_LIMIT = "spacing_limit"
REACTION_BLOCKER_NO_TEMPORAL_FIT = "no_temporal_fit"
REACTION_BLOCKER_DURATION_LIMIT = "reaction_duration_out_of_bounds"

# Canonical set of reaction-blocker reasons. Diagnostics and per-shot
# `reaction_blocked_by` values are always drawn from this list instead of
# loose string literals.
BASE_REACTION_BLOCKERS = [
    REACTION_BLOCKER_ACOUSTIC_IDENTITY_UNAVAILABLE,
    REACTION_BLOCKER_LISTENING_POSTURE_UNAVAILABLE,
    REACTION_BLOCKER_INDEX_UNAVAILABLE,
    REACTION_BLOCKER_NO_SAFE_CANDIDATES,
    REACTION_BLOCKER_LOW_CONFIDENCE,
    REACTION_BLOCKER_SHARE_LIMIT,
    REACTION_BLOCKER_SPACING_LIMIT,
    REACTION_BLOCKER_NO_TEMPORAL_FIT,
    REACTION_BLOCKER_DURATION_LIMIT,
]


def _interval_gap_us(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    if a_end <= b_start:
        return b_start - a_end
    if b_end <= a_start:
        return a_start - b_end
    return 0


def _validate_complete_coverage(
    edit_plan: EditPlanDocument,
    shots: list[CameraEditShot],
) -> None:
    """Reject camera plans that cannot cover every editorial audio instant."""
    shots_by_segment: dict[int, list[CameraEditShot]] = {}
    for shot in shots:
        shots_by_segment.setdefault(shot.edit_segment_order, []).append(shot)

    for segment in edit_plan.segments:
        expected_start_us = round(segment.start * 1_000_000)
        expected_end_us = round(segment.end * 1_000_000)
        cursor_us = expected_start_us
        segment_shots = sorted(
            shots_by_segment.get(segment.timeline_order, []),
            key=lambda shot: (shot.audio_source_start_us, shot.audio_source_end_us),
        )
        for shot in segment_shots:
            if shot.audio_source_start_us != cursor_us:
                raise CameraEditPlanPreconditionError(
                    "camera_timeline não cobre integralmente os segmentos da EDL; "
                    f"refaça a análise de câmeras para o segmento {segment.timeline_order}"
                )
            cursor_us = shot.audio_source_end_us
        if cursor_us != expected_end_us:
            raise CameraEditPlanPreconditionError(
                "camera_timeline não cobre integralmente os segmentos da EDL; "
                f"refaça a análise de câmeras para o segmento {segment.timeline_order}"
            )


def _reaction_policy_blockers(
    *,
    shot_start_us: int,
    shot_end_us: int,
    total_duration_us: int,
    total_reaction_duration_us: int,
    reaction_positions: list[tuple[int, int]],
) -> set[str]:
    blockers: set[str] = set()
    shot_duration_us = shot_end_us - shot_start_us
    if total_duration_us > 0:
        projected_share = (total_reaction_duration_us + shot_duration_us) / total_duration_us
        if projected_share > MAXIMUM_REACTION_SHARE:
            blockers.add(REACTION_BLOCKER_SHARE_LIMIT)
    for start, end in reaction_positions:
        gap = _interval_gap_us(shot_start_us, shot_end_us, start, end)
        if gap < MINIMUM_GAP_BETWEEN_REACTIONS_US:
            blockers.add(REACTION_BLOCKER_SPACING_LIMIT)
            break
    return blockers


def _select_reaction_candidate(
    shot: CameraEditShot,
    reaction_index: ReactionCandidateIndexDocument,
    used_candidate_ids: set[str],
):
    duration_us = shot.audio_source_end_us - shot.audio_source_start_us
    if not MINIMUM_REACTION_DURATION_US <= duration_us <= MAXIMUM_REACTION_DURATION_US:
        return None, {REACTION_BLOCKER_DURATION_LIMIT}
    eligible = [
        candidate for candidate in reaction_index.candidates
        if candidate.candidate_id not in used_candidate_ids
    ]
    if not eligible:
        return None, set()
    temporal_fit = [
        candidate for candidate in eligible
        if candidate.duration_us >= duration_us
        and (candidate.source_end_us <= shot.audio_source_start_us
             or candidate.source_start_us >= shot.audio_source_end_us)
    ]
    if not temporal_fit:
        return None, {REACTION_BLOCKER_NO_TEMPORAL_FIT}
    confident = [
        candidate for candidate in temporal_fit
        if candidate.confidence >= MINIMUM_REACTION_CONFIDENCE
    ]
    if not confident:
        return None, {REACTION_BLOCKER_LOW_CONFIDENCE}
    candidate = min(confident, key=lambda item: (
        abs(item.reference_speech_time_us - shot.audio_source_start_us),
        -item.confidence, item.candidate_id,
    ))
    return candidate, set()


def _build_reaction_shot(
    primary_shot: CameraEditShot, candidate, reaction_candidate_artifact: StageArtifact,
) -> CameraEditShot:
    duration_us = primary_shot.audio_source_end_us - primary_shot.audio_source_start_us
    return primary_shot.model_copy(update={
        "video_source_asset_id": primary_shot.audio_source_asset_id,
        "sync_offset_us": 0,
        "source_start_us": candidate.source_start_us,
        "source_end_us": candidate.source_start_us + duration_us,
        "camera_role": "interviewer_reaction",
        "intent": "reaction",
        "confirmed_identity_ids": [candidate.interviewer_identity_id],
        "visual_origin": "reaction_reuse",
        "reaction_candidate_id": candidate.candidate_id,
        "reaction_candidate_artifact_id": reaction_candidate_artifact.id,
        "reaction_candidate_input_hash": reaction_candidate_artifact.input_hash,
        "interviewer_identity_id": candidate.interviewer_identity_id,
        "selection_score": candidate.confidence,
        "reaction_blocked_by": [],
        "evidence": [
            *primary_shot.evidence,
            "single_master_video_reused",
            "primary_audio_continuous",
            f"reaction_candidate:{candidate.candidate_id}",
            f"reaction_source_start_us:{candidate.source_start_us}",
        ],
    })


def _accumulate_seconds(
    shots: list[CameraEditShot],
) -> tuple[dict[str, float], dict[str, float]]:
    seconds_by_identity: dict[str, float] = {}
    seconds_by_role: dict[str, float] = {}
    for shot in shots:
        duration_seconds = (shot.audio_source_end_us - shot.audio_source_start_us) / 1_000_000
        for identity_id in shot.confirmed_identity_ids:
            seconds_by_identity[identity_id] = round(
                seconds_by_identity.get(identity_id, 0.0) + duration_seconds, 6
            )
        seconds_by_role[shot.camera_role] = round(
            seconds_by_role.get(shot.camera_role, 0.0) + duration_seconds, 6
        )
    return seconds_by_identity, seconds_by_role


def _dominant_identity(
    seconds_by_identity: dict[str, float], total_duration_seconds: float,
) -> tuple[str | None, float]:
    if not seconds_by_identity or total_duration_seconds <= 0:
        return None, 0.0
    identity_id, seconds = min(seconds_by_identity.items(), key=lambda item: (-item[1], item[0]))
    share = min(1.0, seconds / total_duration_seconds)
    return identity_id, round(share, 6)


class CameraEditPlanJobCancelled(RuntimeError):
    pass


class CameraEditPlanPreconditionError(ValueError):
    pass


class CameraEditPlanService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def run(
        self,
        *,
        edit_plan_artifact: StageArtifact,
        camera_timeline_artifact: StageArtifact,
        identity_index_artifact: StageArtifact,
        visual_quality_artifact: StageArtifact,
        reaction_candidate_artifact: StageArtifact | None = None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        if should_cancel():
            raise CameraEditPlanJobCancelled()
        artifacts = [
            edit_plan_artifact, camera_timeline_artifact,
            identity_index_artifact, visual_quality_artifact,
        ]
        if reaction_candidate_artifact is not None:
            artifacts.append(reaction_candidate_artifact)
        paths = [Path(artifact.path) for artifact in artifacts]
        if not all(path.exists() for path in paths):
            raise CameraEditPlanPreconditionError("artifact upstream do camera plan ausente")
        progress_cb(0.0, "Validando EDL, cameras, identidades e qualidade")
        edit_plan = EditPlanDocument.model_validate_json(paths[0].read_text(encoding="utf-8"))
        cameras = CameraTimelineDocument.model_validate_json(paths[1].read_text(encoding="utf-8"))
        identities = IdentityIndexDocument.model_validate_json(paths[2].read_text(encoding="utf-8"))
        quality = VisualQualityDocument.model_validate_json(paths[3].read_text(encoding="utf-8"))
        reaction_index = (
            ReactionCandidateIndexDocument.model_validate_json(paths[4].read_text(encoding="utf-8"))
            if reaction_candidate_artifact is not None else None
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
        if reaction_index is not None and (
            reaction_index.project_id != edit_plan.project_id
            or reaction_index.source_asset_id != edit_plan.source_asset_id
            or reaction_index.camera_timeline_artifact_id != camera_timeline_artifact.id
            or reaction_index.identity_index_artifact_id != identity_index_artifact.id
            or reaction_index.visual_quality_artifact_id != visual_quality_artifact.id
        ):
            raise CameraEditPlanPreconditionError("reaction_candidate_index não corresponde à cadeia visual")
        availability_blockers: list[str] = (
            [] if reaction_index is not None else [REACTION_BLOCKER_INDEX_UNAVAILABLE]
        )
        if reaction_index is not None and not reaction_index.candidates:
            availability_blockers.append(REACTION_BLOCKER_NO_SAFE_CANDIDATES)

        hash_payload = {
            "algorithm": CAMERA_PLAN_ALGORITHM_VERSION,
            "schema_version": CAMERA_EDIT_PLAN_SCHEMA_VERSION,
            "diversity_policy_version": DIVERSITY_POLICY_VERSION,
            "edit_plan": [edit_plan_artifact.id, edit_plan_artifact.input_hash],
            "camera_timeline": [camera_timeline_artifact.id, camera_timeline_artifact.input_hash],
            "identity_index": [identity_index_artifact.id, identity_index_artifact.input_hash],
            "visual_quality": [visual_quality_artifact.id, visual_quality_artifact.input_hash],
            "reaction_candidate_index": (
                [reaction_candidate_artifact.id, reaction_candidate_artifact.input_hash]
                if reaction_candidate_artifact is not None else None
            ),
            "audio_continuity_mode": "primary_source_continuous",
            "temporal_reuse_allowed": reaction_index is not None,
            "reaction_blockers": availability_blockers,
        }
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
        used_candidate_ids: set[str] = set()
        reaction_blocker_reasons: set[str] = set(availability_blockers)
        total_reaction_duration_us = 0
        reaction_positions: list[tuple[int, int]] = []
        total_duration_us = sum(
            round(segment.end * 1_000_000) - round(segment.start * 1_000_000)
            for segment in edit_plan.segments
        )
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
                    evidence=[*evidence, "audio_video_linked"],
                )
                selected_shot = primary_shot
                if primary_shot.intent == "fallback" and reaction_index is not None:
                    policy_blockers = _reaction_policy_blockers(
                        shot_start_us=primary_shot.audio_source_start_us,
                        shot_end_us=primary_shot.audio_source_end_us,
                        total_duration_us=total_duration_us,
                        total_reaction_duration_us=total_reaction_duration_us,
                        reaction_positions=reaction_positions,
                    )
                    candidate = None
                    if policy_blockers:
                        blockers = policy_blockers
                    else:
                        candidate, blockers = _select_reaction_candidate(
                            primary_shot, reaction_index, used_candidate_ids,
                        )
                    if candidate is not None:
                        selected_shot = _build_reaction_shot(
                            primary_shot, candidate, reaction_candidate_artifact,
                        )
                        used_candidate_ids.add(candidate.candidate_id)
                        total_reaction_duration_us += (
                            primary_shot.audio_source_end_us - primary_shot.audio_source_start_us
                        )
                        reaction_positions.append(
                            (primary_shot.audio_source_start_us, primary_shot.audio_source_end_us)
                        )
                    elif blockers:
                        reaction_blocker_reasons.update(blockers)
                        selected_shot = primary_shot.model_copy(update={
                            "reaction_blocked_by": sorted(blockers),
                        })
                shots.append(selected_shot)
            progress_cb(
                10.0 + 75.0 * (segment_position + 1) / total_segments,
                f"Planejando cameras do segmento {segment.timeline_order}",
            )

        _validate_complete_coverage(edit_plan, shots)

        seconds_by_identity, _ = _accumulate_seconds(shots)
        total_duration_seconds = total_duration_us / 1_000_000
        dominant_identity_id, dominant_identity_share = _dominant_identity(
            seconds_by_identity, total_duration_seconds,
        )
        if (
            reaction_index is not None
            and dominant_identity_id is not None
            and total_duration_us >= MONOTONY_MINIMUM_DURATION_US
            and dominant_identity_share >= MONOTONY_THRESHOLD
        ):
            other_identity_shots = [
                shot for shot in shots
                if any(
                    identity_id != dominant_identity_id
                    for identity_id in shot.confirmed_identity_ids
                )
            ]
            eligible_indices = [
                index for index, shot in enumerate(shots)
                if shot.intent != "reaction"
                and all(
                    identity_id == dominant_identity_id
                    for identity_id in shot.confirmed_identity_ids
                )
            ]
            if eligible_indices:
                def _distance(index: int) -> int:
                    shot = shots[index]
                    if not other_identity_shots:
                        return total_duration_us
                    return min(
                        _interval_gap_us(
                            shot.audio_source_start_us, shot.audio_source_end_us,
                            other.audio_source_start_us, other.audio_source_end_us,
                        )
                        for other in other_identity_shots
                    )
                target_index = max(
                    eligible_indices,
                    key=lambda index: (
                        _distance(index),
                        shots[index].audio_source_end_us - shots[index].audio_source_start_us,
                        -shots[index].audio_source_start_us,
                    ),
                )
                target = shots[target_index]
                policy_blockers = _reaction_policy_blockers(
                    shot_start_us=target.audio_source_start_us,
                    shot_end_us=target.audio_source_end_us,
                    total_duration_us=total_duration_us,
                    total_reaction_duration_us=total_reaction_duration_us,
                    reaction_positions=reaction_positions,
                )
                candidate = None
                if policy_blockers:
                    blockers = policy_blockers
                else:
                    candidate, blockers = _select_reaction_candidate(
                        target, reaction_index, used_candidate_ids,
                    )
                if candidate is not None:
                    shots[target_index] = _build_reaction_shot(
                        target, candidate, reaction_candidate_artifact,
                    )
                    used_candidate_ids.add(candidate.candidate_id)
                    total_reaction_duration_us += (
                        target.audio_source_end_us - target.audio_source_start_us
                    )
                    reaction_positions.append(
                        (target.audio_source_start_us, target.audio_source_end_us)
                    )
                elif blockers:
                    reaction_blocker_reasons.update(blockers)
                    shots[target_index] = target.model_copy(update={
                        "reaction_blocked_by": sorted(set(target.reaction_blocked_by) | blockers),
                    })
            seconds_by_identity, _ = _accumulate_seconds(shots)
            dominant_identity_id, dominant_identity_share = _dominant_identity(
                seconds_by_identity, total_duration_seconds,
            )

        seconds_by_identity, seconds_by_role = _accumulate_seconds(shots)
        diagnostics = CameraEditDiagnostics(
            shot_count=len(shots),
            speaker_shot_count=sum(shot.intent == "speaker" for shot in shots),
            context_shot_count=sum(shot.intent == "context" for shot in shots),
            fallback_shot_count=sum(shot.intent == "fallback" for shot in shots),
            unusable_scene_count=len({
                shot.scene_index for shot in shots if not shot.visual_quality_usable
            }),
            reaction_shots_enabled=bool(used_candidate_ids),
            reaction_shot_count=sum(shot.intent == "reaction" for shot in shots),
            reused_candidate_ids=sorted(used_candidate_ids),
            reaction_shots_blocked_by=sorted(reaction_blocker_reasons),
            seconds_by_identity=seconds_by_identity,
            seconds_by_role=seconds_by_role,
            dominant_identity_id=dominant_identity_id,
            dominant_identity_share=dominant_identity_share,
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
            reaction_candidate_artifact_id=(
                reaction_candidate_artifact.id if reaction_candidate_artifact is not None else None
            ),
            reaction_candidate_input_hash=(
                reaction_candidate_artifact.input_hash
                if reaction_candidate_artifact is not None else None
            ),
            input_hash=input_hash,
            shots=shots,
            diagnostics=diagnostics,
            engine=CameraEditEngineInfo(
                algorithm="single_master_reaction_selection",
                algorithm_version=CAMERA_PLAN_ALGORITHM_VERSION,
                temporal_reuse_allowed=reaction_index is not None,
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
                "reaction_candidate_artifact_id": (
                    reaction_candidate_artifact.id if reaction_candidate_artifact is not None else None
                ),
                "shot_count": len(shots),
                "reaction_shots_enabled": bool(used_candidate_ids),
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
