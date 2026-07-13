from __future__ import annotations

from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument
from cortex.render.face_crop import resolve_static_face_crop


def _document(frames: list[dict]) -> FaceIndexDocument:
    landmark = {
        "right_eye": [0.0, 0.0], "left_eye": [0.0, 0.0],
        "nose_tip": [0.0, 0.0], "right_mouth_corner": [0.0, 0.0],
        "left_mouth_corner": [0.0, 0.0],
    }
    for frame in frames:
        for face in frame["faces"]:
            face.setdefault("y", 0.2)
            face.setdefault("height", 0.3)
            face.setdefault("score", 1.0)
            face.setdefault("landmarks", landmark)
    return FaceIndexDocument.model_validate({
        "project_id": "project", "source_asset_id": "source",
        "source_sha256": "sha", "scene_index_artifact_id": "scenes",
        "input_hash": "hash", "duration_seconds": 20,
        "frames": frames, "scenes": [], "frame_count": len(frames),
        "engine": {
            "detector": "test", "model_path": "test", "providers": ["CPU"],
            "score_threshold": 0.5, "nms_threshold": 0.3, "sample_fps": 1,
            "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test",
        },
    })


def _face(x: float, width: float, track: str, **extra: float) -> dict:
    return {"x": x, "width": width, "track_id": track, **extra}


def _resolve(document: FaceIndexDocument, **kwargs):
    return resolve_static_face_crop(
        document, start_seconds=0, end_seconds=10, source_width=1920,
        source_height=1080, canvas_width=1080, canvas_height=1920, **kwargs,
    )


def _identities() -> IdentityIndexDocument:
    return IdentityIndexDocument.model_validate({
        "project_id": "project", "source_asset_id": "source", "source_sha256": "sha",
        "face_index_artifact_id": "faces", "face_index_input_hash": "hash",
        "camera_timeline_artifact_id": "cameras", "camera_timeline_input_hash": "camera-hash",
        "input_hash": "identity-hash", "unresolved_face_count": 0,
        "observations": [{
            "observation_id": "face-0-0", "time_us": 1_000_000, "scene_index": 0,
            "layout_id": "layout-a", "local_track_id": "host", "identity_id": "me",
            "status": "confirmed",
        }],
        "identities": [{
            "identity_id": "me", "status": "confirmed", "observation_ids": ["face-0-0"],
            "layout_ids": ["layout-a", "layout-b"], "scene_indices": [0],
            "local_track_ids": ["host"], "sample_count": 1,
            "evidence": ["cross_layout_continuity_confirmed"],
        }],
        "engine": {
            "algorithm": "test", "algorithm_version": "1", "recognizer": "sface_2021dec",
            "model_path": "model", "model_sha256": "sha", "embedding_dimension": 128,
            "cosine_match_threshold": 0.5, "ambiguity_margin": 0.05,
        },
    })


def test_selects_dominant_track_by_accumulated_area_and_score() -> None:
    document = _document([
        {"time": 1, "shot_type": "medium", "faces": [
            _face(0.05, 0.25, "host"), _face(0.70, 0.1, "guest")],
        },
        {"time": 2, "shot_type": "medium", "faces": [_face(0.10, 0.25, "host")]},
    ])

    result = _resolve(document)

    assert result.provenance["effective_target_track_id"] == "host"
    assert result.provenance["sample_count"] == 2
    assert result.crop_x < 400


def test_explicit_target_track_overrides_dominant_track() -> None:
    document = _document([{"time": 1, "shot_type": "medium", "faces": [
        _face(0.05, 0.35, "host"), _face(0.72, 0.12, "guest"),
    ]}])

    result = _resolve(document, target_track_id="guest")

    assert result.provenance["effective_target_track_id"] == "guest"
    assert result.provenance["target_selection"] == "explicit_track_id"
    assert result.crop_x > 1000


def test_low_weight_outlier_does_not_move_weighted_median() -> None:
    document = _document([
        {"time": 1, "shot_type": "close", "faces": [_face(0.2, 0.3, "host")]},
        {"time": 2, "shot_type": "close", "faces": [_face(0.21, 0.3, "host")]},
        {"time": 3, "shot_type": "close", "faces": [
            _face(0.9, 0.01, "host", height=0.01, score=0.1)]},
    ])

    result = _resolve(document)

    assert result.provenance["resolved_center_x"] < 750


def test_clamps_crop_at_source_edges() -> None:
    left = _resolve(_document([{"time": 1, "shot_type": "close", "faces": [
        _face(0, 0.05, "host")]}]))
    right = _resolve(_document([{"time": 1, "shot_type": "close", "faces": [
        _face(0.95, 0.05, "host")]}]))

    assert left.crop_x == 0
    assert right.crop_x == 1920 - right.crop_width


def test_no_face_or_missing_explicit_target_falls_back_to_center() -> None:
    document = _document([{"time": 1, "shot_type": "wide", "faces": []}])

    result = _resolve(document, target_track_id="missing")

    assert result.provenance["fallback"] == "center_crop"
    assert result.provenance["effective_target_track_id"] is None
    assert result.crop_x == (1920 - result.crop_width) // 2


def test_result_is_one_constant_crop_for_entire_interval() -> None:
    document = _document([
        {"time": 1, "shot_type": "close", "faces": [_face(0.1, 0.2, "host")]},
        {"time": 5, "shot_type": "close", "faces": [_face(0.5, 0.2, "host")]},
        {"time": 9, "shot_type": "close", "faces": [_face(0.8, 0.2, "host")]},
    ])

    result = _resolve(document)

    assert isinstance(result.crop_x, int)
    assert result.provenance["temporal_motion"] is False
    assert "keyframes" not in result.provenance


def test_confirmed_identity_selects_only_the_requested_person() -> None:
    document = _document([{"time": 1, "shot_type": "two_shot", "faces": [
        _face(0.08, 0.2, "host"), _face(0.70, 0.3, "guest"),
    ]}])

    result = _resolve(document, identity_index=_identities(), target_identity_id="me")

    assert result.provenance["target_selection"] == "confirmed_identity"
    assert result.provenance["effective_target_identity_id"] == "me"
    assert result.provenance["temporal_motion"] is False
    assert result.crop_x < 400
