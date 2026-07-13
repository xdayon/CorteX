from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex.analyze.camera_schemas import CameraScene, CameraTimelineDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument
from cortex.analyze.identity_service import IdentityIndexService
from cortex.analyze.reaction_candidate_schemas import ReactionCandidateIndexDocument
from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.multicam_visual_schemas import MulticamVisualIndexDocument
from cortex.analyze.scene_schemas import SceneIndexDocument
from cortex.analyze.visual_quality_schemas import SceneVisualQuality, VisualQualityDocument
from cortex.api import create_app
from cortex.domain.models import StageArtifact
from cortex.edit.camera_plan_schemas import CameraEditPlanDocument
from cortex.edit.camera_plan_service import (
    CameraEditPlanPreconditionError,
    CameraEditPlanService,
)
from cortex.edit.camera_planner import classify_shot_intent
from cortex.edit.schemas import EditPlanDocument
from cortex.jobs import JobStore
from cortex.schemas import JobCreate, JobStatus, JobType
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine
from cortex.domain.store import DomainStore
from test_identity_index import _config as identity_config
from test_identity_index import _fixture as identity_fixture
from test_identity_index import _write_artifact


def test_classify_shot_intent_requires_quality_and_confirmed_speaker() -> None:
    camera = CameraScene(
        scene_index=0, start_us=0, end_us=1_000_000, layout_id="layout-a",
        shot_type="close", role="speaker_close", visible_track_ids=["person"],
        dominant_speaker_track_id="person", speaker_alignment="confirmed",
        speech_coverage=1.0, confidence=0.9, evidence=[],
    )
    usable = SceneVisualQuality(
        scene_index=0, start_us=0, end_us=1_000_000, sample_count=2,
        black_share=0.0, blurred_share=0.0, frozen_share=0.0,
        face_edge_occlusion_share=0.0, usable=True, issues=[],
    )
    unusable = usable.model_copy(update={"usable": False, "issues": ["blurred"]})

    assert classify_shot_intent(camera, usable)[0] == "speaker"
    assert classify_shot_intent(camera, unusable)[0] == "fallback"
    assert classify_shot_intent(camera, None)[0] == "fallback"


def _planner_fixture(tmp_path: Path):
    config, domain, project, face, camera = identity_fixture(tmp_path)
    camera_payload = json.loads(Path(camera.path).read_text(encoding="utf-8"))
    camera_payload["scenes"][0].update({
        "role": "speaker_close", "speaker_alignment": "confirmed",
        "dominant_speaker_track_id": "person_left", "speech_coverage": 1.0,
    })
    camera_payload["scenes"][1].update({
        "role": "two_shot", "shot_type": "two_shot",
        "visible_track_ids": ["person_left", "person_right"],
    })
    camera_payload["scenes"][2].update({"role": "wide", "shot_type": "wide"})
    camera_document = CameraTimelineDocument.model_validate(camera_payload)
    Path(camera.path).write_text(
        json.dumps(camera_document.model_dump(mode="json")), encoding="utf-8"
    )
    identity_result = IdentityIndexService(config, domain).run(
        face_index_artifact=face, camera_timeline_artifact=camera,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    identity = domain.get_stage_artifact(identity_result["identity_index_artifact_id"])
    IdentityIndexDocument.model_validate_json(Path(identity.path).read_text(encoding="utf-8"))

    edit_document = EditPlanDocument.model_validate({
        "project_id": project.id, "source_asset_id": "source-id",
        "transcript_artifact_id": "transcript-id", "analysis_artifact_id": "analysis-id",
        "input_hash": "edit-doc", "clip_start": 0.0, "clip_end": 3.0, "profile": "balanced",
        "segments": [{"start": 0.0, "end": 3.0, "timeline_order": 0}],
        "timeline_duration_seconds": 3.0,
        "diagnostics": {
            "profile": "balanced", "waveform_used": True, "candidate_pauses": 0,
            "cuts": 0, "saved_seconds": 0.0, "crossfade": 0.0, "vad_used": True,
        },
        "quality": {"passed": True, "issues": [], "profile": "balanced", "degraded": False},
    })
    edit_plan = _write_artifact(
        domain, project.id, "edit_plan", tmp_path / "edit.json", edit_document,
    )
    quality_document = VisualQualityDocument.model_validate({
        "project_id": project.id, "source_asset_id": "source-id", "source_sha256": "source-sha",
        "scene_index_artifact_id": "scene-id", "scene_index_input_hash": "scene-hash",
        "face_index_artifact_id": face.id, "face_index_input_hash": face.input_hash,
        "input_hash": "quality-doc", "duration_us": 3_000_000, "frames": [],
        "scenes": [
            {"scene_index": 0, "start_us": 0, "end_us": 1_000_000, "sample_count": 2, "black_share": 0.0, "blurred_share": 0.0, "frozen_share": 0.0, "usable": True, "issues": []},
            {"scene_index": 1, "start_us": 1_000_000, "end_us": 2_000_000, "sample_count": 2, "black_share": 0.0, "blurred_share": 0.0, "frozen_share": 0.0, "usable": True, "issues": []},
            {"scene_index": 2, "start_us": 2_000_000, "end_us": 3_000_000, "sample_count": 2, "black_share": 0.0, "blurred_share": 1.0, "frozen_share": 0.0, "usable": False, "issues": ["blurred"]},
        ],
        "engine": {
            "algorithm": "test", "algorithm_version": "1", "sample_fps_requested": 2.0,
            "sample_fps_effective": 2.0, "frame_width": 640, "black_luma_threshold": 16.0,
            "black_pixel_ratio_threshold": 0.98, "blur_score_threshold": 20.0,
            "freeze_delta_threshold": 0.002, "issue_share_threshold": 0.5,
            "face_edge_margin": 0.01, "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test",
            "decoder_requested": "software", "decoder_effective": "software",
            "device_requested": "cpu", "device_effective": "cpu",
        },
    })
    visual_quality = _write_artifact(
        domain, project.id, "visual_quality_index", tmp_path / "quality.json", quality_document,
    )
    return config, domain, project, edit_plan, camera, identity, visual_quality


def _iso_manifest(tmp_path: Path, domain, project) -> StageArtifact:
    scene_document = SceneIndexDocument.model_validate({
        "project_id": project.id, "source_asset_id": "iso-source", "source_sha256": "iso-sha",
        "input_hash": "iso-scenes", "duration_seconds": 5.0, "cuts": [],
        "scenes": [{"index": 0, "start": 0.0, "end": 5.0}], "cut_count": 0,
        "engine": {"ffmpeg_path": "ffmpeg", "ffmpeg_version": "test", "filter": "scdet",
                   "threshold_requested": 10.0, "threshold_effective": 10.0},
    })
    scene = _write_artifact(
        domain, project.id, "scene_index", tmp_path / "iso-scenes.json", scene_document,
    )
    face_document = FaceIndexDocument.model_validate({
        "project_id": project.id, "source_asset_id": "iso-source", "source_sha256": "iso-sha",
        "scene_index_artifact_id": scene.id, "input_hash": "iso-faces", "duration_seconds": 5.0,
        "frames": [], "frame_count": 0,
        "scenes": [{"scene_index": 0, "dominant_shot_type": "two_shot",
                    "track_ids_present": ["left", "right"], "sample_count": 2}],
        "engine": {"detector": "yunet", "model_path": "yunet.onnx",
                   "providers": ["CPUExecutionProvider"], "score_threshold": 0.8,
                   "nms_threshold": 0.3, "sample_fps": 1.0,
                   "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test"},
    })
    face = _write_artifact(
        domain, project.id, "face_index", tmp_path / "iso-faces.json", face_document,
    )
    quality_document = VisualQualityDocument.model_validate({
        "project_id": project.id, "source_asset_id": "iso-source", "source_sha256": "iso-sha",
        "scene_index_artifact_id": scene.id, "scene_index_input_hash": scene.input_hash,
        "face_index_artifact_id": face.id, "face_index_input_hash": face.input_hash,
        "input_hash": "iso-quality", "duration_us": 5_000_000, "frames": [],
        "scenes": [{"scene_index": 0, "start_us": 0, "end_us": 5_000_000,
                    "sample_count": 5, "black_share": 0.0, "blurred_share": 0.0,
                    "frozen_share": 0.0, "usable": True, "issues": []}],
        "engine": {"algorithm": "test", "algorithm_version": "1",
                   "sample_fps_requested": 2.0, "sample_fps_effective": 2.0,
                   "frame_width": 640, "black_luma_threshold": 16.0,
                   "black_pixel_ratio_threshold": 0.98, "blur_score_threshold": 20.0,
                   "freeze_delta_threshold": 0.002, "issue_share_threshold": 0.5,
                   "face_edge_margin": 0.01, "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test",
                   "decoder_requested": "software", "decoder_effective": "software",
                   "device_requested": "cpu", "device_effective": "cpu"},
    })
    quality = _write_artifact(
        domain, project.id, "visual_quality_index", tmp_path / "iso-quality.json",
        quality_document,
    )
    manifest_document = MulticamVisualIndexDocument.model_validate({
        "project_id": project.id, "primary_source_asset_id": "source-id",
        "multicam_sync_artifact_id": "sync-id", "multicam_sync_input_hash": "sync-hash",
        "input_hash": "manifest", "indexed_camera_count": 1, "rejected_camera_count": 0,
        "cameras": [{"source_asset_id": "iso-source", "source_sha256": "iso-sha",
                     "offset_us": 800_000, "status": "indexed",
                     "scene_index_artifact_id": scene.id, "face_index_artifact_id": face.id,
                     "visual_quality_artifact_id": quality.id}],
        "engine": {"algorithm": "test", "algorithm_version": "1",
                   "stages": ["scene_index", "face_index", "visual_quality_index"],
                   "scene_threshold": 10.0, "face_sample_fps": 1.0,
                   "visual_quality_sample_fps": 2.0},
    })
    return _write_artifact(
        domain, project.id, "multicam_visual_index", tmp_path / "manifest.json",
        manifest_document,
    )


def _reaction_candidates(tmp_path: Path, domain, project, camera, identity, quality) -> StageArtifact:
    document = ReactionCandidateIndexDocument.model_validate({
        "project_id": project.id, "source_asset_id": "source-id", "source_sha256": "source-sha",
        "speaker_timeline_artifact_id": "speaker-id", "speaker_timeline_input_hash": "speaker-hash",
        "camera_timeline_artifact_id": camera.id, "camera_timeline_input_hash": camera.input_hash,
        "identity_index_artifact_id": identity.id, "identity_index_input_hash": identity.input_hash,
        "visual_quality_artifact_id": quality.id, "visual_quality_input_hash": quality.input_hash,
        "interviewer_identity_id": "identity-host", "input_hash": "reaction-doc",
        "candidates": [{
            "candidate_id": "reaction-safe", "source_start_us": 4_000_000,
            "source_end_us": 5_000_000, "duration_us": 1_000_000,
            "scene_index": 3, "layout_id": "host-listening",
            "interviewer_identity_id": "identity-host", "interviewer_track_id": "host-track",
            "speech_context": "adjacent_silence", "reference_speaker_identity_id": "identity-guest",
            "reference_speech_time_us": 3_500_000, "reference_speech_distance_us": 500_000,
            "observation_count": 4, "mean_mouth_motion": 0.01, "max_mouth_motion": 0.02,
            "minimum_speaker_confidence": 0.9, "visual_quality_score": 1.0, "confidence": 0.9,
            "evidence": ["test"],
        }],
        "diagnostics": {"candidate_count": 1, "inspected_observation_count": 4,
                        "qualifying_observation_count": 4, "rejected_observation_counts": {}},
        "engine": {"algorithm": "test", "algorithm_version": "1", "minimum_duration_us": 700_000,
                   "maximum_mouth_motion": 0.08, "maximum_sample_gap_us": 400_000,
                   "maximum_silence_distance_us": 1_000_000,
                   "identity_scope": "cross_layout_face_embedding",
                   "role_assignment": "explicit_interviewer_identity_id",
                   "audio_policy": "mute_reaction_source_preserve_editorial_audio"},
    })
    return _write_artifact(
        domain, project.id, "reaction_candidate_index", tmp_path / "reactions.json", document,
    )


def _monotone_fixture(tmp_path: Path, scene_count: int = 25, other_identity_scene_index: int | None = None):
    config = identity_config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Monotone camera plan")

    camera_scenes = []
    identity_observations = []
    identity_scene_indices = {"identity-host": [], "identity-guest": []}
    quality_scenes = []
    for index in range(scene_count):
        start_us = index * 1_000_000
        end_us = start_us + 1_000_000
        is_other = index == other_identity_scene_index
        track_id = "guest" if is_other else "host"
        identity_id = "identity-guest" if is_other else "identity-host"
        camera_scenes.append({
            "scene_index": index, "start_us": start_us, "end_us": end_us,
            "layout_id": f"layout-{index}", "shot_type": "close", "role": "speaker_close",
            "visible_track_ids": [track_id], "dominant_speaker_track_id": track_id,
            "speaker_alignment": "confirmed", "speech_coverage": 1.0, "confidence": 0.9,
            "evidence": [],
        })
        identity_observations.append({
            "observation_id": f"obs-{index}", "time_us": start_us, "scene_index": index,
            "layout_id": f"layout-{index}", "local_track_id": track_id, "identity_id": identity_id,
            "status": "confirmed",
        })
        identity_scene_indices[identity_id].append(index)
        quality_scenes.append({
            "scene_index": index, "start_us": start_us, "end_us": end_us, "sample_count": 2,
            "black_share": 0.0, "blurred_share": 0.0, "frozen_share": 0.0, "usable": True,
            "issues": [],
        })

    camera_document = CameraTimelineDocument.model_validate({
        "project_id": project.id, "source_asset_id": "source-id", "source_sha256": "source-sha",
        "scene_index_artifact_id": "scene-id", "scene_index_input_hash": "scene-hash",
        "face_index_artifact_id": "face-id", "face_index_input_hash": "face-hash",
        "speaker_timeline_artifact_id": "speaker-id", "speaker_timeline_input_hash": "speaker-hash",
        "input_hash": "camera-doc", "duration_us": scene_count * 1_000_000,
        "scenes": camera_scenes,
        "engine": {"algorithm": "test", "algorithm_version": "1", "dominant_speaker_min_share": 0.55,
                   "identity_scope": "layout_track_only"},
    })
    camera = _write_artifact(domain, project.id, "camera_timeline", tmp_path / "cameras.json", camera_document)

    identities = []
    for identity_id, scene_indices in identity_scene_indices.items():
        if not scene_indices:
            continue
        identities.append({
            "identity_id": identity_id, "status": "confirmed",
            "observation_ids": [f"obs-{index}" for index in scene_indices],
            "layout_ids": [f"layout-{index}" for index in scene_indices],
            "scene_indices": scene_indices,
            "local_track_ids": ["guest" if identity_id == "identity-guest" else "host"],
            "sample_count": len(scene_indices), "evidence": [],
        })
    identity_document = IdentityIndexDocument.model_validate({
        "project_id": project.id, "source_asset_id": "source-id", "source_sha256": "source-sha",
        "face_index_artifact_id": "face-id", "face_index_input_hash": "face-hash",
        "camera_timeline_artifact_id": camera.id, "camera_timeline_input_hash": camera.input_hash,
        "input_hash": "identity-doc", "observations": identity_observations, "identities": identities,
        "unresolved_face_count": 0,
        "engine": {"algorithm": "test", "algorithm_version": "1", "recognizer": "sface_2021dec",
                   "model_path": "sface.onnx", "model_sha256": "sface-sha", "embedding_dimension": 3,
                   "cosine_match_threshold": 0.363, "ambiguity_margin": 0.05},
    })
    identity = _write_artifact(domain, project.id, "identity_index", tmp_path / "identities.json", identity_document)

    quality_document = VisualQualityDocument.model_validate({
        "project_id": project.id, "source_asset_id": "source-id", "source_sha256": "source-sha",
        "scene_index_artifact_id": "scene-id", "scene_index_input_hash": "scene-hash",
        "face_index_artifact_id": "face-id", "face_index_input_hash": "face-hash",
        "input_hash": "quality-doc", "duration_us": scene_count * 1_000_000, "frames": [],
        "scenes": quality_scenes,
        "engine": {
            "algorithm": "test", "algorithm_version": "1", "sample_fps_requested": 2.0,
            "sample_fps_effective": 2.0, "frame_width": 640, "black_luma_threshold": 16.0,
            "black_pixel_ratio_threshold": 0.98, "blur_score_threshold": 20.0,
            "freeze_delta_threshold": 0.002, "issue_share_threshold": 0.5,
            "face_edge_margin": 0.01, "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test",
            "decoder_requested": "software", "decoder_effective": "software",
            "device_requested": "cpu", "device_effective": "cpu",
        },
    })
    quality = _write_artifact(domain, project.id, "visual_quality_index", tmp_path / "quality.json", quality_document)

    edit_document = EditPlanDocument.model_validate({
        "project_id": project.id, "source_asset_id": "source-id",
        "transcript_artifact_id": "transcript-id", "analysis_artifact_id": "analysis-id",
        "input_hash": "edit-doc", "clip_start": 0.0, "clip_end": float(scene_count), "profile": "balanced",
        "segments": [{"start": 0.0, "end": float(scene_count), "timeline_order": 0}],
        "timeline_duration_seconds": float(scene_count),
        "diagnostics": {
            "profile": "balanced", "waveform_used": True, "candidate_pauses": 0,
            "cuts": 0, "saved_seconds": 0.0, "crossfade": 0.0, "vad_used": True,
        },
        "quality": {"passed": True, "issues": [], "profile": "balanced", "degraded": False},
    })
    edit = _write_artifact(domain, project.id, "edit_plan", tmp_path / "edit.json", edit_document)

    return config, domain, project, edit, camera, identity, quality


def test_camera_plan_persists_linked_shots_filters_quality_and_caches(tmp_path: Path) -> None:
    config, domain, project, edit, camera, identity, quality = _planner_fixture(tmp_path)
    service = CameraEditPlanService(config, domain)
    first = service.run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    document = CameraEditPlanDocument.model_validate_json(
        Path(first["camera_edit_plan_path"]).read_text(encoding="utf-8")
    )
    second = service.run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )

    assert [shot.intent for shot in document.shots] == ["speaker", "context", "fallback"]
    assert all(shot.source_start_us == shot.audio_source_start_us for shot in document.shots)
    assert all(shot.source_end_us == shot.audio_source_end_us for shot in document.shots)
    assert document.diagnostics.reaction_shots_enabled is False
    assert "reaction_candidate_index_unavailable" in document.diagnostics.reaction_shots_blocked_by
    assert document.engine.temporal_reuse_allowed is False
    assert second["cached"] is True
    assert len(domain.list_stage_artifacts(project.id, "camera_edit_plan")) == 1


def test_camera_plan_v2_replaces_only_fallback_with_synchronized_iso_context(tmp_path: Path) -> None:
    config, domain, project, edit, camera, identity, quality = _planner_fixture(tmp_path)
    manifest = _iso_manifest(tmp_path, domain, project)
    result = CameraEditPlanService(config, domain).run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        multicam_visual_artifact=manifest,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    document = CameraEditPlanDocument.model_validate_json(
        Path(result["camera_edit_plan_path"]).read_text(encoding="utf-8")
    )

    assert [shot.intent for shot in document.shots] == ["speaker", "context", "context"]
    assert document.shots[0].video_source_asset_id == "source-id"
    iso_shot = document.shots[2]
    assert iso_shot.video_source_asset_id == "iso-source"
    assert iso_shot.audio_source_asset_id == "source-id"
    assert (iso_shot.source_start_us, iso_shot.source_end_us) == (2_800_000, 3_800_000)
    assert (iso_shot.audio_source_start_us, iso_shot.audio_source_end_us) == (2_000_000, 3_000_000)
    assert iso_shot.sync_offset_us == 800_000
    assert document.diagnostics.iso_context_shot_count == 1
    assert "reaction_candidate_index_unavailable" in document.diagnostics.reaction_shots_blocked_by
    assert document.engine.audio_continuity_mode == "primary_source_continuous"


def _widen_edit_plan_timeline(edit: StageArtifact, end_seconds: float) -> None:
    payload = json.loads(Path(edit.path).read_text(encoding="utf-8"))
    payload["segments"][0]["end"] = end_seconds
    payload["clip_end"] = end_seconds
    payload["timeline_duration_seconds"] = end_seconds
    Path(edit.path).write_text(json.dumps(payload), encoding="utf-8")


def test_camera_plan_reuses_safe_single_master_reaction_with_primary_audio(tmp_path: Path) -> None:
    config, domain, project, edit, camera, identity, quality = _planner_fixture(tmp_path)
    # Widen the editorial timeline so the 1s reaction stays within the 20%
    # maximum_reaction_share policy (the camera timeline itself still only
    # covers 0-3s, so no additional shots are produced).
    _widen_edit_plan_timeline(edit, 6.0)
    reactions = _reaction_candidates(tmp_path, domain, project, camera, identity, quality)
    result = CameraEditPlanService(config, domain).run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        reaction_candidate_artifact=reactions,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    document = CameraEditPlanDocument.model_validate_json(
        Path(result["camera_edit_plan_path"]).read_text(encoding="utf-8")
    )
    reaction = next(shot for shot in document.shots if shot.intent == "reaction")

    assert reaction.video_source_asset_id == reaction.audio_source_asset_id == "source-id"
    assert (reaction.source_start_us, reaction.source_end_us) == (4_000_000, 5_000_000)
    assert (reaction.audio_source_start_us, reaction.audio_source_end_us) == (2_000_000, 3_000_000)
    assert reaction.visual_origin == "reaction_reuse"
    assert reaction.reaction_candidate_id == "reaction-safe"
    assert reaction.reaction_blocked_by == []
    assert document.diagnostics.reaction_shots_enabled is True
    assert document.diagnostics.reused_candidate_ids == ["reaction-safe"]


def test_camera_plan_rejects_low_confidence_reaction_candidate(tmp_path: Path) -> None:
    config, domain, project, edit, camera, identity, quality = _planner_fixture(tmp_path)
    _widen_edit_plan_timeline(edit, 6.0)
    reactions = _reaction_candidates(tmp_path, domain, project, camera, identity, quality)
    payload = json.loads(Path(reactions.path).read_text(encoding="utf-8"))
    payload["candidates"][0]["confidence"] = 0.4
    Path(reactions.path).write_text(json.dumps(payload), encoding="utf-8")

    result = CameraEditPlanService(config, domain).run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        reaction_candidate_artifact=reactions,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    document = CameraEditPlanDocument.model_validate_json(
        Path(result["camera_edit_plan_path"]).read_text(encoding="utf-8")
    )

    assert all(shot.intent != "reaction" for shot in document.shots)
    fallback_shot = next(shot for shot in document.shots if shot.intent == "fallback")
    assert "low_confidence_candidates" in fallback_shot.reaction_blocked_by
    assert "low_confidence_candidates" in document.diagnostics.reaction_shots_blocked_by
    assert document.diagnostics.reaction_shots_enabled is False


def test_camera_plan_blocks_second_reaction_over_share_limit(tmp_path: Path) -> None:
    config, domain, project, edit, camera, identity, quality = _planner_fixture(tmp_path)
    # 1s reaction on a 3s timeline is 33% > the 20% maximum_reaction_share,
    # so the very first (and only) fallback candidate must be blocked.
    reactions = _reaction_candidates(tmp_path, domain, project, camera, identity, quality)

    result = CameraEditPlanService(config, domain).run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        reaction_candidate_artifact=reactions,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    document = CameraEditPlanDocument.model_validate_json(
        Path(result["camera_edit_plan_path"]).read_text(encoding="utf-8")
    )

    assert all(shot.intent != "reaction" for shot in document.shots)
    fallback_shot = next(shot for shot in document.shots if shot.intent == "fallback")
    assert "reaction_share_limit_reached" in fallback_shot.reaction_blocked_by
    assert "reaction_share_limit_reached" in document.diagnostics.reaction_shots_blocked_by


def test_camera_plan_monotony_without_candidate_reports_dominance(tmp_path: Path) -> None:
    config, domain, project, edit, camera, identity, quality = _monotone_fixture(tmp_path, scene_count=25)
    # No reaction_candidate_artifact is supplied: monotony detection must still
    # measure dominance correctly and keep the single-source plan unchanged.
    result = CameraEditPlanService(config, domain).run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    document = CameraEditPlanDocument.model_validate_json(
        Path(result["camera_edit_plan_path"]).read_text(encoding="utf-8")
    )

    assert all(shot.intent != "reaction" for shot in document.shots)
    assert document.diagnostics.dominant_identity_id == "identity-host"
    assert document.diagnostics.dominant_identity_share == 1.0
    assert document.diagnostics.seconds_by_identity == {"identity-host": 25.0}
    assert "reaction_candidate_index_unavailable" in document.diagnostics.reaction_shots_blocked_by


def test_camera_plan_monotony_with_empty_index_reports_no_safe_candidates(tmp_path: Path) -> None:
    config, domain, project, edit, camera, identity, quality = _monotone_fixture(tmp_path, scene_count=25)
    reactions = _reaction_candidates(tmp_path, domain, project, camera, identity, quality)
    payload = json.loads(Path(reactions.path).read_text(encoding="utf-8"))
    payload["candidates"] = []
    payload["diagnostics"]["candidate_count"] = 0
    Path(reactions.path).write_text(json.dumps(payload), encoding="utf-8")

    result = CameraEditPlanService(config, domain).run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        reaction_candidate_artifact=reactions,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    document = CameraEditPlanDocument.model_validate_json(
        Path(result["camera_edit_plan_path"]).read_text(encoding="utf-8")
    )

    assert all(shot.intent != "reaction" for shot in document.shots)
    assert document.diagnostics.dominant_identity_id == "identity-host"
    assert document.diagnostics.dominant_identity_share == 1.0
    assert "no_safe_reaction_candidates" in document.diagnostics.reaction_shots_blocked_by


def test_camera_plan_never_replaces_shot_showing_other_identity(tmp_path: Path) -> None:
    # Scene 12 naturally shows a distinct identity ("identity-guest"); the plan
    # remains monotonous (24/25 identity-host) but that shot must never be
    # chosen as a reaction substitution target.
    config, domain, project, edit, camera, identity, quality = _monotone_fixture(
        tmp_path, scene_count=25, other_identity_scene_index=12,
    )
    reactions = _reaction_candidates(tmp_path, domain, project, camera, identity, quality)
    payload = json.loads(Path(reactions.path).read_text(encoding="utf-8"))
    payload["candidates"][0]["source_start_us"] = 30_000_000
    payload["candidates"][0]["source_end_us"] = 31_000_000
    payload["candidates"][0]["reference_speech_time_us"] = 0
    Path(reactions.path).write_text(json.dumps(payload), encoding="utf-8")

    result = CameraEditPlanService(config, domain).run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        reaction_candidate_artifact=reactions,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    document = CameraEditPlanDocument.model_validate_json(
        Path(result["camera_edit_plan_path"]).read_text(encoding="utf-8")
    )
    other_identity_shot = next(shot for shot in document.shots if shot.scene_index == 12)
    assert other_identity_shot.intent != "reaction"
    assert other_identity_shot.confirmed_identity_ids == ["identity-guest"]
    assert document.diagnostics.reaction_shot_count <= 1


def test_camera_plan_diversity_policy_version_invalidates_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cortex.edit.camera_plan_service as camera_plan_service_module

    config, domain, project, edit, camera, identity, quality = _planner_fixture(tmp_path)
    service = CameraEditPlanService(config, domain)
    first = service.run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    monkeypatch.setattr(camera_plan_service_module, "DIVERSITY_POLICY_VERSION", "9.9.9")
    second = service.run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )

    assert second["cached"] is False
    assert second["camera_edit_plan_artifact_id"] != first["camera_edit_plan_artifact_id"]
    assert len(domain.list_stage_artifacts(project.id, "camera_edit_plan")) == 2


def test_camera_plan_deterministic_for_same_inputs(tmp_path: Path) -> None:
    config, domain, project, edit, camera, identity, quality = _planner_fixture(tmp_path)
    _widen_edit_plan_timeline(edit, 6.0)
    reactions = _reaction_candidates(tmp_path, domain, project, camera, identity, quality)

    first = CameraEditPlanService(config, domain).run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        reaction_candidate_artifact=reactions,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )
    # Same inputs, run again through a fresh service instance: must hit the
    # exact same content-addressed artifact (cached) rather than recompute.
    second = CameraEditPlanService(config, domain).run(
        edit_plan_artifact=edit, camera_timeline_artifact=camera,
        identity_index_artifact=identity, visual_quality_artifact=quality,
        reaction_candidate_artifact=reactions,
        progress_cb=lambda *_args: None, should_cancel=lambda: False,
    )

    assert first["camera_edit_plan_path"] == second["camera_edit_plan_path"]
    assert second["cached"] is True


def test_camera_plan_rejects_mismatched_visual_chain(tmp_path: Path) -> None:
    config, domain, _project, edit, camera, identity, quality = _planner_fixture(tmp_path)
    payload = json.loads(Path(quality.path).read_text(encoding="utf-8"))
    payload["face_index_artifact_id"] = "other-face-index"
    Path(quality.path).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CameraEditPlanPreconditionError, match="cadeia visual"):
        CameraEditPlanService(config, domain).run(
            edit_plan_artifact=edit, camera_timeline_artifact=camera,
            identity_index_artifact=identity, visual_quality_artifact=quality,
            progress_cb=lambda *_args: None, should_cancel=lambda: False,
        )


def test_camera_plan_worker_persists_artifact(tmp_path: Path) -> None:
    config, domain, project, edit, camera, identity, quality = _planner_fixture(tmp_path)
    manifest = _iso_manifest(tmp_path, domain, project)
    jobs = JobStore(config.paths.database)
    queued = jobs.create(JobCreate(
        type=JobType.CAMERA_PLANNING, project_id=project.id,
        payload={
            "edit_plan_artifact_id": edit.id,
            "camera_timeline_artifact_id": camera.id,
            "identity_index_artifact_id": identity.id,
            "visual_quality_artifact_id": quality.id,
            "multicam_visual_artifact_id": manifest.id,
        },
    ))

    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued.id)
    assert final.status == JobStatus.SUCCEEDED, final.error
    assert final.result is not None
    artifact = domain.get_stage_artifact(final.result["camera_edit_plan_artifact_id"])
    assert artifact.stage == "camera_edit_plan" and Path(artifact.path).exists()
    document = CameraEditPlanDocument.model_validate_json(Path(artifact.path).read_text())
    assert document.diagnostics.iso_context_shot_count == 1


def test_camera_plan_routes_are_exposed_in_openapi(tmp_path: Path) -> None:
    config, _domain, _project, _edit, _camera, _identity, _quality = _planner_fixture(tmp_path)
    paths = create_app(config).openapi()["paths"]
    collection = f"{config.app.api_prefix}/projects/{{project_id}}/camera-plans"

    assert "post" in paths[collection]
    assert "get" in paths[collection + "/{artifact_id}"]
