from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex.analyze.camera_schemas import CameraTimelineDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument
from cortex.analyze.reaction_candidate_schemas import ReactionCandidateIndexDocument
from cortex.analyze.reaction_candidate_service import (
    ReactionCandidatePreconditionError,
    ReactionCandidateService,
)
from cortex.analyze.speaker_schemas import SpeakerTimelineDocument
from cortex.analyze.visual_quality_schemas import VisualQualityDocument
from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import JobCreate, JobStatus, JobType
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine


def _config(tmp_path: Path) -> CortexConfig:
    base = load_config()
    return base.model_copy(update={
        "paths": base.paths.model_copy(update={
            "data_dir": tmp_path,
            "projects_dir": tmp_path / "projects",
            "cache_dir": tmp_path / "cache",
            "output_dir": tmp_path / "output",
            "database": tmp_path / "cortex.sqlite3",
        }),
    })


def _write_artifact(
    domain: DomainStore,
    project_id: str,
    stage: str,
    path: Path,
    document: object,
) -> StageArtifact:
    path.write_text(
        json.dumps(document.model_dump(mode="json")),  # type: ignore[attr-defined]
        encoding="utf-8",
    )
    return domain.create_stage_artifact(StageArtifact(
        project_id=project_id,
        stage=stage,
        path=str(path),
        input_hash=f"{stage}-hash",
    ))


def _fixture(tmp_path: Path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Reaction candidates")
    source_id = "source-id"
    source_sha = "source-sha"

    speaker_document = SpeakerTimelineDocument.model_validate({
        "project_id": project.id,
        "source_asset_id": source_id,
        "source_sha256": source_sha,
        "scene_index_artifact_id": "scene-id",
        "scene_index_input_hash": "scene-hash",
        "face_index_artifact_id": "face-id",
        "face_index_input_hash": "face-hash",
        "analysis_artifact_id": "analysis-id",
        "analysis_input_hash": "analysis-hash",
        "input_hash": "speaker-doc",
        "duration_us": 6_000_000,
        "observations": [
            {
                "time_us": time_us,
                "scene_index": scene_index,
                "speech_active": True,
                "state": "speaker",
                "speaker_track_id": "guest-a",
                "confidence": 0.94,
                "track_scores": [
                    {
                        "track_id": "guest-a",
                        "visible": False,
                        "mouth_motion_score": 0.72,
                        "speaking_score": 0.91,
                        "confidence": 0.94,
                        "evidence": "mouth_motion",
                    },
                    {
                        "track_id": interviewer_track,
                        "visible": True,
                        "mouth_motion_score": 0.02,
                        "speaking_score": 0.03,
                        "confidence": 0.93,
                        "evidence": "mouth_motion",
                    },
                ],
            }
            for time_us, scene_index, interviewer_track in (
                (2_500_000, 1, "host-a"),
                (3_500_000, 1, "host-a"),
                (4_500_000, 2, "host-b"),
                (5_500_000, 2, "host-b"),
            )
        ],
        "segments": [{
            "start_us": 2_000_000,
            "end_us": 6_000_000,
            "state": "speaker",
            "speaker_track_id": "guest-a",
            "confidence": 0.94,
            "evidence": "visual_mouth_motion_confirmed",
        }],
        "engine": {
            "algorithm": "test",
            "algorithm_version": "1",
            "sample_fps_requested": 1.0,
            "sample_fps_effective": 1.0,
            "frame_width_requested": 320,
            "frame_width_effective": 320,
            "vad_padding_seconds": 0.15,
            "motion_threshold": 0.08,
            "winner_margin": 0.03,
            "ffmpeg_path": "ffmpeg",
            "ffmpeg_version": "test",
            "decoder_requested": "software",
            "decoder_effective": "software",
            "device_requested": "cpu",
            "device_effective": "cpu",
        },
    })
    speaker = _write_artifact(
        domain, project.id, "speaker_timeline", tmp_path / "speakers.json", speaker_document,
    )

    camera_document = CameraTimelineDocument.model_validate({
        "project_id": project.id,
        "source_asset_id": source_id,
        "source_sha256": source_sha,
        "scene_index_artifact_id": "scene-id",
        "scene_index_input_hash": "scene-hash",
        "face_index_artifact_id": "face-id",
        "face_index_input_hash": "face-hash",
        "speaker_timeline_artifact_id": speaker.id,
        "speaker_timeline_input_hash": speaker.input_hash,
        "input_hash": "camera-doc",
        "duration_us": 6_000_000,
        "scenes": [
            {
                "scene_index": index,
                "start_us": start,
                "end_us": end,
                "layout_id": layout,
                "shot_type": "close",
                "role": "unknown",
                "visible_track_ids": [track],
                "dominant_speaker_track_id": "guest-a",
                "speaker_alignment": "ambiguous",
                "speech_coverage": 1.0,
                "confidence": 0.9,
                "evidence": ["visible_person_is_not_confirmed_speaker"],
            }
            for index, start, end, layout, track in (
                (1, 2_000_000, 4_000_000, "host-close", "host-a"),
                (2, 4_000_000, 6_000_000, "host-medium", "host-b"),
            )
        ],
        "engine": {
            "algorithm": "test",
            "algorithm_version": "1",
            "dominant_speaker_min_share": 0.55,
            "identity_scope": "layout_track_only",
        },
    })
    camera = _write_artifact(
        domain, project.id, "camera_timeline", tmp_path / "cameras.json", camera_document,
    )

    identity_document = IdentityIndexDocument.model_validate({
        "project_id": project.id,
        "source_asset_id": source_id,
        "source_sha256": source_sha,
        "face_index_artifact_id": "face-id",
        "face_index_input_hash": "face-hash",
        "camera_timeline_artifact_id": camera.id,
        "camera_timeline_input_hash": camera.input_hash,
        "input_hash": "identity-doc",
        "observations": [
            {
                "observation_id": "host-observation-a",
                "time_us": 2_500_000,
                "scene_index": 1,
                "layout_id": "host-close",
                "local_track_id": "host-a",
                "identity_id": "identity-host",
                "status": "confirmed",
                "assignment_similarity": 0.99,
            },
            {
                "observation_id": "host-observation-b",
                "time_us": 4_500_000,
                "scene_index": 2,
                "layout_id": "host-medium",
                "local_track_id": "host-b",
                "identity_id": "identity-host",
                "status": "confirmed",
                "assignment_similarity": 0.98,
            },
            {
                "observation_id": "guest-observation-a",
                "time_us": 1_000_000,
                "scene_index": 0,
                "layout_id": "guest-close",
                "local_track_id": "guest-a",
                "identity_id": "identity-guest",
                "status": "confirmed",
                "assignment_similarity": 0.99,
            },
            {
                "observation_id": "guest-observation-b",
                "time_us": 2_500_000,
                "scene_index": 1,
                "layout_id": "host-close",
                "local_track_id": "guest-a",
                "identity_id": "identity-guest",
                "status": "confirmed",
                "assignment_similarity": 0.98,
            },
            {
                "observation_id": "guest-observation-c",
                "time_us": 4_500_000,
                "scene_index": 2,
                "layout_id": "host-medium",
                "local_track_id": "guest-a",
                "identity_id": "identity-guest",
                "status": "confirmed",
                "assignment_similarity": 0.98,
            },
        ],
        "identities": [
            {
                "identity_id": "identity-host",
                "status": "confirmed",
                "observation_ids": ["host-observation-a", "host-observation-b"],
                "layout_ids": ["host-close", "host-medium"],
                "scene_indices": [1, 2],
                "local_track_ids": ["host-a", "host-b"],
                "sample_count": 2,
                "minimum_pair_similarity": 0.98,
                "evidence": ["cross_layout_face_embedding"],
            },
            {
                "identity_id": "identity-guest",
                "status": "confirmed",
                "observation_ids": [
                    "guest-observation-a", "guest-observation-b", "guest-observation-c",
                ],
                "layout_ids": ["guest-close", "host-close", "host-medium"],
                "scene_indices": [0, 1, 2],
                "local_track_ids": ["guest-a"],
                "sample_count": 3,
                "minimum_pair_similarity": 0.98,
                "evidence": ["cross_layout_face_embedding"],
            },
        ],
        "unresolved_face_count": 0,
        "engine": {
            "algorithm": "complete_link",
            "algorithm_version": "1",
            "recognizer": "sface_2021dec",
            "model_path": "sface.onnx",
            "model_sha256": "sface-sha",
            "embedding_dimension": 128,
            "cosine_match_threshold": 0.363,
            "ambiguity_margin": 0.08,
            "provider": "CPUExecutionProvider",
            "identity_scope": "cross_layout_face_embedding",
        },
    })
    identity = _write_artifact(
        domain, project.id, "identity_index", tmp_path / "identities.json", identity_document,
    )

    quality_document = VisualQualityDocument.model_validate({
        "project_id": project.id,
        "source_asset_id": source_id,
        "source_sha256": source_sha,
        "scene_index_artifact_id": "scene-id",
        "scene_index_input_hash": "scene-hash",
        "face_index_artifact_id": "face-id",
        "face_index_input_hash": "face-hash",
        "input_hash": "quality-doc",
        "duration_us": 6_000_000,
        "frames": [],
        "scenes": [
            {
                "scene_index": index,
                "start_us": start,
                "end_us": end,
                "sample_count": 4,
                "black_share": 0.0,
                "blurred_share": 0.0,
                "frozen_share": 0.0,
                "face_edge_occlusion_share": 0.0,
                "usable": True,
                "issues": [],
            }
            for index, start, end in (
                (1, 2_000_000, 4_000_000),
                (2, 4_000_000, 6_000_000),
            )
        ],
        "engine": {
            "algorithm": "test",
            "algorithm_version": "1",
            "sample_fps_requested": 2.0,
            "sample_fps_effective": 2.0,
            "frame_width": 320,
            "black_luma_threshold": 16.0,
            "black_pixel_ratio_threshold": 0.98,
            "blur_score_threshold": 20.0,
            "freeze_delta_threshold": 0.01,
            "issue_share_threshold": 0.5,
            "face_edge_margin": 0.01,
            "ffmpeg_path": "ffmpeg",
            "ffmpeg_version": "test",
            "decoder_requested": "software",
            "decoder_effective": "software",
            "device_requested": "cpu",
            "device_effective": "cpu",
        },
    })
    quality = _write_artifact(
        domain, project.id, "visual_quality_index", tmp_path / "quality.json", quality_document,
    )
    return config, domain, project, speaker, camera, identity, quality


def _run_service(fixture: tuple, *, min_duration_seconds: float = 1.0):
    config, domain, _project, speaker, camera, identity, quality = fixture
    return ReactionCandidateService(config, domain).run(
        speaker_timeline_artifact=speaker,
        camera_timeline_artifact=camera,
        identity_index_artifact=identity,
        visual_quality_artifact=quality,
        interviewer_identity_id="identity-host",
        min_duration_seconds=min_duration_seconds,
        progress_cb=lambda *_args: None,
        should_cancel=lambda: False,
    )


def test_service_persists_safe_auditable_single_source_candidate(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _config_value, domain, project, *_artifacts = fixture
    result = _run_service(fixture)
    artifact = domain.get_stage_artifact(result["reaction_candidate_artifact_id"])
    document = ReactionCandidateIndexDocument.model_validate_json(
        Path(artifact.path).read_text(encoding="utf-8")
    )

    assert artifact.stage == "reaction_candidate_index"
    assert document.interviewer_identity_id == "identity-host"
    assert len(document.candidates) == 2
    candidate = document.candidates[0]
    assert candidate.interviewer_identity_id == "identity-host"
    assert candidate.source_start_us == 2_000_000
    assert candidate.source_end_us == 4_000_000
    assert candidate.duration_us == 2_000_000
    assert candidate.concurrent_speaker_identity_id == "identity-guest"
    assert candidate.concurrent_speaker_identity_id != candidate.interviewer_identity_id
    assert candidate.max_mouth_motion <= 0.08
    assert candidate.visual_quality_score == 1.0
    assert candidate.confidence > 0
    assert candidate.evidence
    assert len(domain.list_stage_artifacts(project.id, "reaction_candidate_index")) == 1


@pytest.mark.parametrize("broken", ["identity", "chain"])
def test_service_fails_closed_for_unconfirmed_identity_or_divergent_chain(
    tmp_path: Path, broken: str,
) -> None:
    fixture = _fixture(tmp_path)
    _config_value, _domain, _project, speaker, camera, identity, _quality = fixture
    if broken == "identity":
        payload = json.loads(Path(identity.path).read_text(encoding="utf-8"))
        payload["identities"][0]["status"] = "single_layout"
        Path(identity.path).write_text(json.dumps(payload), encoding="utf-8")
    else:
        payload = json.loads(Path(camera.path).read_text(encoding="utf-8"))
        payload["speaker_timeline_artifact_id"] = "other-speaker-artifact"
        Path(camera.path).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReactionCandidatePreconditionError):
        _run_service(fixture)


def test_service_reuses_cached_artifact(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _config_value, domain, project, *_artifacts = fixture
    first = _run_service(fixture)
    second = _run_service(fixture)

    assert second["cached"] is True
    assert second["reaction_candidate_artifact_id"] == first[
        "reaction_candidate_artifact_id"
    ]
    assert len(domain.list_stage_artifacts(project.id, "reaction_candidate_index")) == 1


def test_service_accepts_adjacent_silence_with_distinct_reference_speech(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _config_value, _domain, _project, speaker, _camera, _identity, _quality = fixture
    payload = json.loads(Path(speaker.path).read_text(encoding="utf-8"))
    payload["engine"]["vad_padding_seconds"] = 1.0
    for observation in payload["observations"][:2]:
        observation.update({
            "speech_active": False,
            "state": "no_speech",
            "speaker_track_id": None,
            "confidence": 1.0,
        })
    Path(speaker.path).write_text(json.dumps(payload), encoding="utf-8")

    result = _run_service(fixture)
    document = ReactionCandidateIndexDocument.model_validate_json(
        Path(result["reaction_candidate_path"]).read_text(encoding="utf-8")
    )
    adjacent = next(
        candidate for candidate in document.candidates
        if candidate.speech_context == "adjacent_silence"
    )

    assert adjacent.concurrent_speaker_identity_id is None
    assert adjacent.reference_speaker_identity_id == "identity-guest"
    assert adjacent.reference_speech_distance_us == 1_000_000
    assert "global_vad_confirmed_adjacent_silence_with_reference" in adjacent.evidence


def test_api_worker_and_get_persist_real_candidate_artifact(tmp_path: Path) -> None:
    config, domain, project, speaker, camera, identity, quality = _fixture(tmp_path)
    app = create_app(config)
    jobs = JobStore(config.paths.database)
    queued = jobs.create(JobCreate(
        type=JobType.REACTION_CANDIDATE_ANALYSIS,
        project_id=project.id,
        payload={
            "speaker_timeline_artifact_id": speaker.id,
            "camera_timeline_artifact_id": camera.id,
            "identity_index_artifact_id": identity.id,
            "visual_quality_artifact_id": quality.id,
            "interviewer_identity_id": "identity-host",
            "min_duration_seconds": 1.0,
        },
    ))
    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued.id)
    assert final.status == JobStatus.SUCCEEDED, final.error
    assert final.result is not None
    artifact_id = final.result["reaction_candidate_artifact_id"]
    artifact = domain.get_stage_artifact(artifact_id)
    assert Path(artifact.path).exists()

    route = next(
        route
        for route in app.routes
        if getattr(route, "path", None)
        == "/api/v1/projects/{project_id}/reaction-candidates/{artifact_id}"
        and "GET" in getattr(route, "methods", set())
    )
    fetched = route.endpoint(project.id, artifact_id)
    document = ReactionCandidateIndexDocument.model_validate(fetched["document"])
    assert document.candidates[0].interviewer_identity_id == "identity-host"


def test_reaction_candidate_routes_are_exposed_in_openapi(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    paths = create_app(config).openapi()["paths"]
    collection = f"{config.app.api_prefix}/projects/{{project_id}}/reaction-candidates"

    assert "post" in paths[collection]
    assert "get" in paths[collection + "/{artifact_id}"]
