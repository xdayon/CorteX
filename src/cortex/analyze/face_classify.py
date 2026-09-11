"""Heuristic shot-type classification and local visual slotting.

Everything here operates on plain dicts/floats (normalized 0-1 bbox
coordinates and centroids), so it's testable against synthetic detections
without loading the ONNX model. All thresholds are named constants, meant
to be easy to recalibrate against real footage.

The slot IDs in this module remain a per-episode heuristic, not face recognition:
with a fixed camera, a person's horizontal position stays roughly constant,
so we cluster the x-centroids of all detections in the episode into stable
"slots" (1-D split on the largest gap) and label each detection by its nearest
slot. SFace embeddings are generated separately by ``face_service`` and resolved
across layouts by ``identity_service``; they never change these local slot IDs.
"""

from __future__ import annotations

from collections import Counter

SHOT_NONE = "none"
SHOT_CLOSE = "close"
SHOT_TWO_SHOT = "two_shot"
SHOT_WIDE = "wide"

# -- Shot-type classification thresholds -----------------------------------

# Two faces are considered "comparable in size" (a two-shot) when the
# smaller one's bbox area is at least this fraction of the larger one's.
TWO_SHOT_SIZE_RATIO_MIN = 0.5
# A single dominant face with bbox area >= this fraction of the frame area
# (and roughly centered) reads as a close-up.
CLOSE_AREA_RATIO_MIN = 0.12
# A dominant face with bbox area <= this fraction of the frame area reads
# as a wide/establishing shot.
WIDE_AREA_RATIO_MAX = 0.02
# How far (normalized, from the horizontal center 0.5) a face's centroid
# may sit and still count as "centered" for the close-up heuristic.
CENTER_TOLERANCE_X = 0.3

# -- Identity clustering thresholds ----------------------------------------

# Split the sorted episode centroids into two slots at the largest gap only
# when that gap is at least this fraction of the total centroid spread;
# otherwise treat the episode as having a single slot.
CLUSTER_GAP_RATIO_MIN = 0.3


def classify_frame_shot(faces: list[dict]) -> str:
    """Classify one frame's detections into none/close/two_shot/wide."""
    if not faces:
        return SHOT_NONE
    areas = [max(0.0, f["width"]) * max(0.0, f["height"]) for f in faces]
    order = sorted(range(len(faces)), key=lambda i: areas[i], reverse=True)
    largest_area = areas[order[0]]

    if len(faces) >= 2:
        second_area = areas[order[1]]
        if largest_area > 0 and second_area >= largest_area * TWO_SHOT_SIZE_RATIO_MIN:
            return SHOT_TWO_SHOT

    dominant = faces[order[0]]
    centroid_x = dominant["x"] + dominant["width"] / 2.0
    centered = abs(centroid_x - 0.5) <= CENTER_TOLERANCE_X
    if largest_area >= CLOSE_AREA_RATIO_MIN and centered:
        return SHOT_CLOSE
    return SHOT_WIDE


def cluster_identity_slots(centroids_x: list[float]) -> list[float]:
    """1-D clustering of episode-wide face x-centroids into stable slots.

    Splits at the single largest gap in the sorted centroids, but only if
    that gap is wide relative to the overall spread (``CLUSTER_GAP_RATIO_MIN``)
    — otherwise everything collapses into one slot. Returns slot centers
    sorted ascending (left to right).
    """
    if not centroids_x:
        return []
    values = sorted(centroids_x)
    if len(values) == 1:
        return [values[0]]

    total_range = values[-1] - values[0]
    if total_range <= 0:
        return [sum(values) / len(values)]

    max_gap = 0.0
    split_at = -1
    for i in range(len(values) - 1):
        gap = values[i + 1] - values[i]
        if gap > max_gap:
            max_gap = gap
            split_at = i

    if split_at < 0 or max_gap < total_range * CLUSTER_GAP_RATIO_MIN:
        return [sum(values) / len(values)]

    left = values[: split_at + 1]
    right = values[split_at + 1 :]
    return [sum(left) / len(left), sum(right) / len(right)]


def slot_label(slot_index: int, total_slots: int) -> str:
    """Stable human-readable name for a slot at ``slot_index`` (left to right)."""
    if total_slots <= 1:
        return "person"
    if slot_index == 0:
        return "person_left"
    if slot_index == total_slots - 1:
        return "person_right"
    return f"person_center_{slot_index}"


def assign_track_id(centroid_x: float, slot_centers: list[float]) -> str:
    """Nearest-centroid assignment of one detection to an episode-wide slot."""
    if not slot_centers:
        return "person"
    nearest = min(range(len(slot_centers)), key=lambda i: abs(slot_centers[i] - centroid_x))
    return slot_label(nearest, len(slot_centers))


def iou(box_a: dict, box_b: dict) -> float:
    """IoU of two ``{x, y, width, height}`` normalized bboxes."""
    ax1, ay1 = box_a["x"], box_a["y"]
    ax2, ay2 = ax1 + box_a["width"], ay1 + box_a["height"]
    bx1, by1 = box_b["x"], box_b["y"]
    bx2, by2 = bx1 + box_b["width"], by1 + box_b["height"]

    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = inter_w * inter_h
    area_a = box_a["width"] * box_a["height"]
    area_b = box_b["width"] * box_b["height"]
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


def aggregate_scene_summaries(frames: list[dict], scenes: list[dict]) -> list[dict]:
    """Aggregate per-frame shot/identity data into one summary per scene.

    ``frames`` items need ``time``, ``shot_type`` and ``faces`` (each with a
    ``track_id``). ``scenes`` items need ``index``, ``start``, ``end``.
    """
    summaries = []
    for scene in scenes:
        in_scene = [f for f in frames if scene["start"] <= f["time"] < scene["end"]]
        if not in_scene:
            summaries.append({
                "scene_index": scene["index"],
                "dominant_shot_type": SHOT_NONE,
                "track_ids_present": [],
                "sample_count": 0,
            })
            continue
        shot_counts = Counter(f["shot_type"] for f in in_scene)
        dominant_shot_type = shot_counts.most_common(1)[0][0]
        track_ids = sorted({
            face.get("track_id")
            for f in in_scene
            for face in f["faces"]
            if face.get("track_id")
        })
        summaries.append({
            "scene_index": scene["index"],
            "dominant_shot_type": dominant_shot_type,
            "track_ids_present": track_ids,
            "sample_count": len(in_scene),
        })
    return summaries


def assign_scene_tracks(frames: list[dict], scenes: list[dict]) -> None:
    """Assign local slots per camera scene, rejecting simultaneous collisions.

    A maximum cluster diameter prevents intermediate positions in other camera
    layouts from joining the left and right people into a single episode slot.
    IDs are local positions; cross-camera identity remains the SFace service's job.
    """
    for frame in frames:
        for face in frame["faces"]:
            face["track_id"] = None
    for scene in scenes:
        local = [frame for frame in frames if scene["start"] <= frame["time"] < scene["end"]]
        centers = sorted(face["x"] + face["width"] / 2 for frame in local for face in frame["faces"])
        clusters: list[list[float]] = []
        for center in centers:
            if not clusters or center - clusters[-1][0] > 0.18:
                clusters.append([center])
            else:
                clusters[-1].append(center)
        slots = [sum(cluster) / len(cluster) for cluster in clusters]
        for frame in local:
            for face in frame["faces"]:
                face["track_id"] = assign_track_id(face["x"] + face["width"] / 2, slots)
            counts = Counter(face["track_id"] for face in frame["faces"])
            for face in frame["faces"]:
                if counts[face["track_id"]] > 1:
                    face["track_id"] = None
