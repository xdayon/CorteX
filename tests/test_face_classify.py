from __future__ import annotations

import pytest

from cortex.analyze.face_classify import (
    SHOT_CLOSE,
    SHOT_NONE,
    SHOT_TWO_SHOT,
    SHOT_WIDE,
    aggregate_scene_summaries,
    assign_track_id,
    classify_frame_shot,
    cluster_identity_slots,
    iou,
    slot_label,
)


def _face(x: float, y: float, w: float, h: float) -> dict:
    return {"x": x, "y": y, "width": w, "height": h}


# -- classify_frame_shot ------------------------------------------------------

def test_classify_no_faces_is_none() -> None:
    assert classify_frame_shot([]) == SHOT_NONE


def test_classify_single_large_centered_face_is_close() -> None:
    faces = [_face(0.35, 0.1, 0.4, 0.4)]  # area 0.16, centroid x = 0.55
    assert classify_frame_shot(faces) == SHOT_CLOSE


def test_classify_single_small_face_is_wide() -> None:
    faces = [_face(0.45, 0.45, 0.05, 0.05)]  # area 0.0025
    assert classify_frame_shot(faces) == SHOT_WIDE


def test_classify_off_center_large_face_is_wide_not_close() -> None:
    # Large enough area but far from center -> not a "close" talking-head shot.
    faces = [_face(0.0, 0.0, 0.3, 0.3)]  # centroid x = 0.15, far from 0.5
    assert classify_frame_shot(faces) == SHOT_WIDE


def test_classify_two_comparable_faces_is_two_shot() -> None:
    faces = [_face(0.05, 0.1, 0.3, 0.3), _face(0.65, 0.1, 0.28, 0.28)]
    assert classify_frame_shot(faces) == SHOT_TWO_SHOT


def test_classify_two_faces_very_different_size_is_not_two_shot() -> None:
    # A large dominant face plus a tiny background face shouldn't count as
    # a two-shot; falls back to single-face classification of the larger one.
    faces = [_face(0.3, 0.1, 0.4, 0.4), _face(0.02, 0.02, 0.03, 0.03)]
    assert classify_frame_shot(faces) == SHOT_CLOSE


# -- cluster_identity_slots / slot_label / assign_track_id --------------------

def test_cluster_identity_slots_empty() -> None:
    assert cluster_identity_slots([]) == []


def test_cluster_identity_slots_single_cluster_when_tightly_grouped() -> None:
    centroids = [0.48, 0.5, 0.52, 0.49, 0.51]
    slots = cluster_identity_slots(centroids)
    assert len(slots) == 1


def test_cluster_identity_slots_splits_two_well_separated_groups() -> None:
    left = [0.15, 0.18, 0.16, 0.17, 0.14]
    right = [0.82, 0.85, 0.83, 0.80, 0.84]
    slots = cluster_identity_slots(left + right)
    assert len(slots) == 2
    assert slots[0] < 0.3
    assert slots[1] > 0.7


def test_slot_label_single_slot_is_person() -> None:
    assert slot_label(0, 1) == "person"


def test_slot_label_two_slots_left_right() -> None:
    assert slot_label(0, 2) == "person_left"
    assert slot_label(1, 2) == "person_right"


def test_assign_track_id_nearest_slot() -> None:
    slots = [0.2, 0.8]
    assert assign_track_id(0.22, slots) == "person_left"
    assert assign_track_id(0.79, slots) == "person_right"


def test_assign_track_id_no_slots_returns_person() -> None:
    assert assign_track_id(0.5, []) == "person"


# -- iou -----------------------------------------------------------------

def test_iou_identical_boxes_is_one() -> None:
    box = _face(0.1, 0.1, 0.2, 0.2)
    assert iou(box, box) == pytest.approx(1.0)


def test_iou_disjoint_boxes_is_zero() -> None:
    a = _face(0.0, 0.0, 0.1, 0.1)
    b = _face(0.5, 0.5, 0.1, 0.1)
    assert iou(a, b) == 0.0


# -- aggregate_scene_summaries -------------------------------------------------

def test_aggregate_scene_summaries_picks_dominant_shot_and_tracks() -> None:
    scenes = [{"index": 0, "start": 0.0, "end": 2.0}, {"index": 1, "start": 2.0, "end": 4.0}]
    frames = [
        {"time": 0.5, "shot_type": SHOT_CLOSE, "faces": [{"track_id": "person_left"}]},
        {"time": 1.5, "shot_type": SHOT_CLOSE, "faces": [{"track_id": "person_left"}]},
        {"time": 1.8, "shot_type": SHOT_TWO_SHOT, "faces": [{"track_id": "person_left"}, {"track_id": "person_right"}]},
        {"time": 3.0, "shot_type": SHOT_WIDE, "faces": []},
    ]
    summaries = aggregate_scene_summaries(frames, scenes)
    assert summaries[0]["dominant_shot_type"] == SHOT_CLOSE
    assert summaries[0]["track_ids_present"] == ["person_left", "person_right"]
    assert summaries[0]["sample_count"] == 3
    assert summaries[1]["dominant_shot_type"] == SHOT_WIDE
    assert summaries[1]["track_ids_present"] == []
    assert summaries[1]["sample_count"] == 1


def test_aggregate_scene_summaries_empty_scene_is_none() -> None:
    scenes = [{"index": 0, "start": 0.0, "end": 2.0}]
    summaries = aggregate_scene_summaries([], scenes)
    assert summaries[0]["dominant_shot_type"] == SHOT_NONE
    assert summaries[0]["sample_count"] == 0


def test_tracks_do_not_collapse_when_other_cameras_bridge_positions():
    from cortex.analyze.face_classify import assign_scene_tracks
    frames = [{"time": 0, "faces": [{"x": .12, "width": .1}, {"x": .72, "width": .1}]}]
    frames += [{"time": i + 1, "faces": [{"x": .2 + i * .05, "width": .1}]} for i in range(10)]
    assign_scene_tracks(frames, [{"index": 0, "start": 0, "end": 1},
                                {"index": 1, "start": 1, "end": 12}])
    assert [face["track_id"] for face in frames[0]["faces"]] == ["person_left", "person_right"]


def test_ambiguous_simultaneous_faces_never_share_a_track():
    from cortex.analyze.face_classify import assign_scene_tracks
    frames = [{"time": 0, "faces": [{"x": .4, "width": .1}, {"x": .41, "width": .1}]}]
    assign_scene_tracks(frames, [{"index": 0, "start": 0, "end": 1}])
    assert [face["track_id"] for face in frames[0]["faces"]] == [None, None]
