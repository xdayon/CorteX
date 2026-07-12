from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex.analyze.camera_schemas import CameraScene, CameraTimelineDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument
from cortex.analyze.identity_service import IdentityIndexService
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
    assert "alternate_camera_source_unavailable" in document.diagnostics.reaction_shots_blocked_by
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
    assert "alternate_camera_source_unavailable" not in document.diagnostics.reaction_shots_blocked_by
    assert document.engine.audio_continuity_mode == "primary_source_continuous"


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
