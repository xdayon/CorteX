from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument


IDENTITY_LIMITATION = (
    "FaceIndexDocument exposes position-based track_id values, not global identities."
)


@dataclass(frozen=True)
class StaticFaceCrop:
    """One immutable horizontal crop for an entire source interval."""

    crop_x: int
    crop_y: int
    crop_width: int
    crop_height: int
    provenance: dict[str, Any]


def _weighted_median(samples: list[tuple[float, float]]) -> float:
    ordered = sorted(samples, key=lambda item: item[0])
    threshold = sum(weight for _, weight in ordered) / 2.0
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def resolve_static_face_crop(
    face_index: FaceIndexDocument,
    *,
    start_seconds: float,
    end_seconds: float,
    source_width: int,
    source_height: int,
    canvas_width: int,
    canvas_height: int,
    target_track_id: str | None = None,
    identity_index: IdentityIndexDocument | None = None,
    target_identity_id: str | None = None,
) -> StaticFaceCrop:
    """Resolve a single face-focused crop; never produces temporal camera motion."""

    if start_seconds < 0 or end_seconds <= start_seconds:
        raise ValueError("source interval must have positive duration")
    if min(source_width, source_height, canvas_width, canvas_height) <= 0:
        raise ValueError("source and canvas dimensions must be positive")

    source_ratio = source_width / source_height
    canvas_ratio = canvas_width / canvas_height
    if source_ratio > canvas_ratio:
        crop_height = source_height
        crop_width = max(1, min(source_width, round(source_height * canvas_ratio)))
    else:
        crop_width = source_width
        crop_height = max(1, min(source_height, round(source_width / canvas_ratio)))

    identity_by_sample = {
        (observation.time_us, observation.local_track_id): observation.identity_id
        for observation in (identity_index.observations if identity_index else [])
        if observation.status == "confirmed"
    }
    confirmed_target = bool(
        target_identity_id
        and identity_index
        and any(
            identity.identity_id == target_identity_id and identity.status == "confirmed"
            for identity in identity_index.identities
        )
    )
    observations: dict[str, list[tuple[float, float, float]]] = {}
    for frame in face_index.frames:
        if not start_seconds <= frame.time <= end_seconds:
            continue
        for face in frame.faces:
            if face.track_id is None:
                continue
            weight = face.width * face.height * face.score
            if weight <= 0:
                continue
            identity_id = identity_by_sample.get((round(frame.time * 1_000_000), face.track_id))
            if target_identity_id is not None and (
                not confirmed_target or identity_id != target_identity_id
            ):
                continue
            center_x = (face.x + face.width / 2.0) * source_width
            observations.setdefault(face.track_id, []).append((center_x, weight, frame.time))

    if target_identity_id is not None:
        effective_track = max(
            observations,
            key=lambda track: (sum(item[1] for item in observations[track]), track),
            default=None,
        )
        selection = "confirmed_identity"
    elif target_track_id is not None:
        effective_track = target_track_id if observations.get(target_track_id) else None
        selection = "explicit_track_id"
    else:
        effective_track = max(
            observations,
            key=lambda track: (sum(item[1] for item in observations[track]), track),
            default=None,
        )
        selection = "dominant_weighted_track"

    selected = observations.get(effective_track, []) if effective_track else []
    if selected:
        center_x = _weighted_median([(center, weight) for center, weight, _ in selected])
        unclamped_x = round(center_x - crop_width / 2.0)
        crop_x = max(0, min(source_width - crop_width, unclamped_x))
        fallback = None
    else:
        center_x = source_width / 2.0
        crop_x = max(0, (source_width - crop_width) // 2)
        fallback = "center_crop"

    crop_y = max(0, (source_height - crop_height) // 2)
    return StaticFaceCrop(
        crop_x=crop_x,
        crop_y=crop_y,
        crop_width=crop_width,
        crop_height=crop_height,
        provenance={
            "method": "static_weighted_median",
            "interval": {"start_seconds": start_seconds, "end_seconds": end_seconds},
            "requested_target_track_id": target_track_id,
            "effective_target_track_id": effective_track,
            "requested_target_identity_id": target_identity_id,
            "effective_target_identity_id": target_identity_id if selected else None,
            "target_selection": selection,
            "sample_count": len(selected),
            "samples": [
                {"time": time, "center_x": center, "weight": weight}
                for center, weight, time in selected
            ],
            "resolved_center_x": center_x,
            "fallback": fallback,
            "identity_scope": (
                "confirmed_cross_layout_identity" if target_identity_id else "position_based_track_id"
            ),
            "identity_limitation": None if target_identity_id else IDENTITY_LIMITATION,
            "temporal_motion": False,
        },
    )
