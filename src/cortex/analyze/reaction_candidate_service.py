from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from cortex.analyze.camera_schemas import CameraTimelineDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument
from cortex.analyze.reaction_candidate_schemas import (
    REACTION_CANDIDATE_INDEX_SCHEMA_VERSION,
    ReactionCandidate,
    ReactionCandidateDiagnostics,
    ReactionCandidateEngineInfo,
    ReactionCandidateIndexDocument,
)
from cortex.analyze.speaker_schemas import SpeakerTimelineDocument
from cortex.analyze.visual_quality_schemas import VisualQualityDocument
from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore
from cortex.paths import reaction_candidates_dir

REACTION_CANDIDATE_ALGORITHM_VERSION = "1.1.0"
DEFAULT_MINIMUM_DURATION_SECONDS = 0.7


class ReactionCandidateJobCancelled(RuntimeError):
    pass


class ReactionCandidatePreconditionError(ValueError):
    pass


def _reject(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


class ReactionCandidateService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def run(
        self,
        *,
        speaker_timeline_artifact: StageArtifact,
        camera_timeline_artifact: StageArtifact,
        identity_index_artifact: StageArtifact,
        visual_quality_artifact: StageArtifact,
        interviewer_identity_id: str,
        min_duration_seconds: float | None = None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        if should_cancel():
            raise ReactionCandidateJobCancelled()
        artifacts = (
            speaker_timeline_artifact,
            camera_timeline_artifact,
            identity_index_artifact,
            visual_quality_artifact,
        )
        paths = [Path(artifact.path) for artifact in artifacts]
        if not all(path.exists() for path in paths):
            raise ReactionCandidatePreconditionError("artifact upstream de reactions ausente")

        progress_cb(0.0, "Validando identidade, fala e qualidade das reactions")
        speakers = SpeakerTimelineDocument.model_validate_json(paths[0].read_text(encoding="utf-8"))
        cameras = CameraTimelineDocument.model_validate_json(paths[1].read_text(encoding="utf-8"))
        identities = IdentityIndexDocument.model_validate_json(paths[2].read_text(encoding="utf-8"))
        quality = VisualQualityDocument.model_validate_json(paths[3].read_text(encoding="utf-8"))
        if len({artifact.project_id for artifact in artifacts}) != 1 or any(
            artifact.project_id != speakers.project_id for artifact in artifacts
        ):
            raise ReactionCandidatePreconditionError("artifacts de reaction não pertencem ao projeto")
        source_keys = {
            (document.source_asset_id, document.source_sha256)
            for document in (speakers, cameras, identities, quality)
        }
        if len(source_keys) != 1:
            raise ReactionCandidatePreconditionError("artifacts de reaction não correspondem à fonte")
        if (
            cameras.speaker_timeline_artifact_id != speaker_timeline_artifact.id
            or identities.camera_timeline_artifact_id != camera_timeline_artifact.id
            or speakers.scene_index_artifact_id != cameras.scene_index_artifact_id
            or speakers.face_index_artifact_id != cameras.face_index_artifact_id
            or quality.scene_index_artifact_id != cameras.scene_index_artifact_id
            or quality.face_index_artifact_id != cameras.face_index_artifact_id
        ):
            raise ReactionCandidatePreconditionError("cadeia visual de reaction inconsistente")

        interviewer = next(
            (item for item in identities.identities if item.identity_id == interviewer_identity_id),
            None,
        )
        if interviewer is None or interviewer.status != "confirmed":
            raise ReactionCandidatePreconditionError(
                "interviewer_identity_id deve referenciar identidade confirmada"
            )

        effective_minimum_seconds = (
            DEFAULT_MINIMUM_DURATION_SECONDS
            if min_duration_seconds is None
            else min_duration_seconds
        )
        minimum_duration_us = round(effective_minimum_seconds * 1_000_000)
        if minimum_duration_us <= 0:
            raise ReactionCandidatePreconditionError("duração mínima de reaction deve ser positiva")
        sample_step_us = max(1, round(1_000_000 / speakers.engine.sample_fps_effective))
        maximum_sample_gap_us = round(sample_step_us * 1.6)
        maximum_silence_distance_us = round(speakers.engine.vad_padding_seconds * 1_000_000)
        maximum_mouth_motion = speakers.engine.motion_threshold
        hash_payload = {
            "algorithm": REACTION_CANDIDATE_ALGORITHM_VERSION,
            "schema_version": REACTION_CANDIDATE_INDEX_SCHEMA_VERSION,
            "speaker_timeline": [speaker_timeline_artifact.id, speaker_timeline_artifact.input_hash],
            "camera_timeline": [camera_timeline_artifact.id, camera_timeline_artifact.input_hash],
            "identity_index": [identity_index_artifact.id, identity_index_artifact.input_hash],
            "visual_quality": [visual_quality_artifact.id, visual_quality_artifact.input_hash],
            "interviewer_identity_id": interviewer_identity_id,
            "minimum_duration_us": minimum_duration_us,
            "maximum_mouth_motion": maximum_mouth_motion,
            "maximum_silence_distance_us": maximum_silence_distance_us,
        }
        input_hash = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cached = self._domain.find_cached_stage_artifact(
            project_id=speakers.project_id,
            stage="reaction_candidate_index",
            input_hash=input_hash,
            schema_version=REACTION_CANDIDATE_INDEX_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Banco de reactions em cache reutilizado")
            return {
                "cached": True,
                "reaction_candidate_artifact_id": cached.id,
                "reaction_candidate_path": cached.path,
                "schema_version": cached.schema_version,
            }

        confirmed_by_track: dict[tuple[int, str], str] = {}
        ambiguous_tracks: set[tuple[int, str]] = set()
        for observation in identities.observations:
            key = (observation.scene_index, observation.local_track_id)
            if observation.status != "confirmed":
                continue
            previous = confirmed_by_track.get(key)
            if previous is not None and previous != observation.identity_id:
                ambiguous_tracks.add(key)
            else:
                confirmed_by_track[key] = observation.identity_id
        for key in ambiguous_tracks:
            confirmed_by_track.pop(key, None)

        camera_by_scene = {scene.scene_index: scene for scene in cameras.scenes}
        quality_by_scene = {scene.scene_index: scene for scene in quality.scenes}
        rejected: dict[str, int] = {}
        qualifying: list[dict] = []
        ordered_observations = sorted(
            speakers.observations, key=lambda item: (item.time_us, item.scene_index)
        )
        reference_speech_samples: list[tuple[int, str]] = []
        for observation in ordered_observations:
            if observation.state != "speaker" or observation.speaker_track_id is None:
                continue
            identity_id = confirmed_by_track.get(
                (observation.scene_index, observation.speaker_track_id)
            )
            if identity_id is not None and identity_id != interviewer_identity_id:
                reference_speech_samples.append((observation.time_us, identity_id))
        for position, observation in enumerate(ordered_observations):
            if should_cancel():
                raise ReactionCandidateJobCancelled()
            camera = camera_by_scene.get(observation.scene_index)
            scene_quality = quality_by_scene.get(observation.scene_index)
            interviewer_tracks = sorted(
                track_id
                for track_id in (camera.visible_track_ids if camera is not None else [])
                if confirmed_by_track.get((observation.scene_index, track_id))
                == interviewer_identity_id
            )
            if camera is None or scene_quality is None:
                _reject(rejected, "scene_evidence_unavailable")
                continue
            if not scene_quality.usable:
                _reject(rejected, "visual_quality_unusable")
                continue
            if len(interviewer_tracks) != 1:
                _reject(rejected, "interviewer_track_unconfirmed")
                continue
            interviewer_track = interviewer_tracks[0]
            speaker_identity_id = None
            speaker_track_id = None
            if observation.speech_active:
                if observation.state != "speaker" or observation.speaker_track_id is None:
                    _reject(rejected, "concurrent_speaker_unconfirmed")
                    continue
                speaker_track_id = observation.speaker_track_id
                speaker_identity_id = confirmed_by_track.get(
                    (observation.scene_index, speaker_track_id)
                )
                if speaker_identity_id is None or speaker_identity_id == interviewer_identity_id:
                    _reject(rejected, "speaker_identity_not_distinct")
                    continue
                speech_context = "concurrent_speech"
                reference_time_us = observation.time_us
                reference_identity_id = speaker_identity_id
            elif observation.state == "no_speech":
                speech_context = "adjacent_silence"
                reference = min(
                    reference_speech_samples,
                    key=lambda item: (abs(item[0] - observation.time_us), item[0], item[1]),
                    default=None,
                )
                if reference is None:
                    _reject(rejected, "adjacent_speech_reference_unavailable")
                    continue
                reference_time_us, reference_identity_id = reference
                if abs(reference_time_us - observation.time_us) > maximum_silence_distance_us:
                    _reject(rejected, "adjacent_speech_too_distant")
                    continue
            else:
                _reject(rejected, "silence_state_unconfirmed")
                continue
            score = next(
                (item for item in observation.track_scores if item.track_id == interviewer_track),
                None,
            )
            if score is None:
                _reject(rejected, "interviewer_mouth_motion_unavailable")
                continue
            if score.mouth_motion_score > maximum_mouth_motion:
                _reject(rejected, "visible_speech_detected")
                continue
            qualifying.append({
                "time_us": observation.time_us,
                "scene": camera,
                "quality": scene_quality,
                "interviewer_track": interviewer_track,
                "speech_context": speech_context,
                "speaker_track": speaker_track_id,
                "speaker_identity": speaker_identity_id,
                "reference_identity": reference_identity_id,
                "reference_time_us": reference_time_us,
                "reference_distance_us": abs(reference_time_us - observation.time_us),
                "mouth_motion": score.mouth_motion_score,
                "speaker_confidence": observation.confidence,
            })
            progress_cb(
                5.0 + 70.0 * (position + 1) / max(len(ordered_observations), 1),
                "Classificando aparições silenciosas do entrevistador",
            )

        groups: list[list[dict]] = []
        for sample in qualifying:
            if groups:
                previous = groups[-1][-1]
                same_window = (
                    sample["scene"].scene_index == previous["scene"].scene_index
                    and sample["interviewer_track"] == previous["interviewer_track"]
                    and sample["speaker_identity"] == previous["speaker_identity"]
                    and sample["speaker_track"] == previous["speaker_track"]
                    and sample["speech_context"] == previous["speech_context"]
                    and sample["reference_identity"] == previous["reference_identity"]
                    and sample["time_us"] - previous["time_us"] <= maximum_sample_gap_us
                )
                if same_window:
                    groups[-1].append(sample)
                    continue
            groups.append([sample])

        candidates: list[ReactionCandidate] = []
        half_step = sample_step_us // 2
        for group in groups:
            first, last = group[0], group[-1]
            start_us = max(first["scene"].start_us, first["time_us"] - half_step)
            end_us = min(first["scene"].end_us, last["time_us"] + half_step)
            duration_us = end_us - start_us
            if duration_us < minimum_duration_us:
                _reject(rejected, "duration_below_minimum")
                continue
            mouths = [sample["mouth_motion"] for sample in group]
            speaker_confidences = [sample["speaker_confidence"] for sample in group]
            scene_quality = first["quality"]
            quality_penalty = (
                scene_quality.black_share
                + scene_quality.blurred_share
                + scene_quality.frozen_share
                + (scene_quality.face_edge_occlusion_share or 0.0)
            ) / 4.0
            visual_quality_score = max(0.0, min(1.0, 1.0 - quality_penalty))
            lip_safety = 1.0 - min(1.0, max(mouths) / max(maximum_mouth_motion, 1e-9))
            confidence = min(min(speaker_confidences), visual_quality_score, 0.5 + lip_safety / 2.0)
            reference_sample = min(
                group, key=lambda item: (item["reference_distance_us"], item["time_us"])
            )
            candidate_key = (
                f"{speakers.source_asset_id}:{start_us}:{end_us}:"
                f"{interviewer_identity_id}:{first['speech_context']}:"
                f"{first['speaker_identity']}"
            )
            candidates.append(ReactionCandidate(
                candidate_id="reaction-" + hashlib.sha256(candidate_key.encode()).hexdigest()[:16],
                source_start_us=start_us,
                source_end_us=end_us,
                duration_us=duration_us,
                scene_index=first["scene"].scene_index,
                layout_id=first["scene"].layout_id,
                interviewer_identity_id=interviewer_identity_id,
                interviewer_track_id=first["interviewer_track"],
                speech_context=first["speech_context"],
                concurrent_speaker_identity_id=first["speaker_identity"],
                concurrent_speaker_track_id=first["speaker_track"],
                reference_speaker_identity_id=reference_sample["reference_identity"],
                reference_speech_time_us=reference_sample["reference_time_us"],
                reference_speech_distance_us=reference_sample["reference_distance_us"],
                observation_count=len(group),
                mean_mouth_motion=round(sum(mouths) / len(mouths), 6),
                max_mouth_motion=round(max(mouths), 6),
                minimum_speaker_confidence=round(min(speaker_confidences), 6),
                visual_quality_score=round(visual_quality_score, 6),
                confidence=round(confidence, 6),
                evidence=[
                    "explicit_interviewer_identity_confirmed",
                    (
                        "concurrent_speaker_identity_confirmed_and_distinct"
                        if first["speech_context"] == "concurrent_speech"
                        else "global_vad_confirmed_adjacent_silence_with_reference"
                    ),
                    f"reference_speech_distance_us:{reference_sample['reference_distance_us']}",
                    "mouth_motion_below_speaker_threshold",
                    "visual_quality_usable",
                    "single_master_source_time",
                    "reaction_source_audio_must_be_muted",
                ],
            ))

        candidates.sort(key=lambda item: (-item.confidence, item.source_start_us, item.candidate_id))
        document = ReactionCandidateIndexDocument(
            project_id=speakers.project_id,
            source_asset_id=speakers.source_asset_id,
            source_sha256=speakers.source_sha256,
            speaker_timeline_artifact_id=speaker_timeline_artifact.id,
            speaker_timeline_input_hash=speaker_timeline_artifact.input_hash,
            camera_timeline_artifact_id=camera_timeline_artifact.id,
            camera_timeline_input_hash=camera_timeline_artifact.input_hash,
            identity_index_artifact_id=identity_index_artifact.id,
            identity_index_input_hash=identity_index_artifact.input_hash,
            visual_quality_artifact_id=visual_quality_artifact.id,
            visual_quality_input_hash=visual_quality_artifact.input_hash,
            interviewer_identity_id=interviewer_identity_id,
            input_hash=input_hash,
            candidates=candidates,
            diagnostics=ReactionCandidateDiagnostics(
                candidate_count=len(candidates),
                inspected_observation_count=len(speakers.observations),
                qualifying_observation_count=len(qualifying),
                rejected_observation_counts=dict(sorted(rejected.items())),
            ),
            engine=ReactionCandidateEngineInfo(
                algorithm="confirmed_identity_vad_gated_listener_windows",
                algorithm_version=REACTION_CANDIDATE_ALGORITHM_VERSION,
                minimum_duration_us=minimum_duration_us,
                maximum_mouth_motion=maximum_mouth_motion,
                maximum_sample_gap_us=maximum_sample_gap_us,
                maximum_silence_distance_us=maximum_silence_distance_us,
                identity_scope="cross_layout_face_embedding",
                role_assignment="explicit_interviewer_identity_id",
                audio_policy="mute_reaction_source_preserve_editorial_audio",
            ),
        )
        progress_cb(90.0, "Persistindo banco de reaction candidates")
        out_dir = reaction_candidates_dir(self._config, speakers.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"reaction-candidates-{input_hash[:16]}.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if should_cancel():
            temporary_path.unlink(missing_ok=True)
            raise ReactionCandidateJobCancelled()
        temporary_path.replace(output_path)
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=speakers.project_id,
            stage="reaction_candidate_index",
            schema_version=REACTION_CANDIDATE_INDEX_SCHEMA_VERSION,
            path=str(output_path),
            input_hash=input_hash,
            metadata={
                "source_asset_id": speakers.source_asset_id,
                "interviewer_identity_id": interviewer_identity_id,
                "speaker_timeline_artifact_id": speaker_timeline_artifact.id,
                "camera_timeline_artifact_id": camera_timeline_artifact.id,
                "identity_index_artifact_id": identity_index_artifact.id,
                "visual_quality_artifact_id": visual_quality_artifact.id,
                "candidate_count": len(candidates),
                "audio_policy": "mute_reaction_source_preserve_editorial_audio",
            },
        ))
        progress_cb(100.0, "Banco de reaction candidates concluído")
        return {
            "cached": False,
            "reaction_candidate_artifact_id": artifact.id,
            "reaction_candidate_path": artifact.path,
            "schema_version": artifact.schema_version,
        }
