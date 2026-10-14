"""Stable, scene-local framing of an already switched podcast master.

Uses existing CPU face/mouth-motion evidence. Ambiguity keeps the full image;
position-based tracks are never treated as identities across camera cuts.
"""
from __future__ import annotations

import hashlib
from bisect import bisect_left
from pathlib import Path
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cortex.analyze.camera_schemas import CameraTimelineDocument
from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.speaker_schemas import SpeakerTimelineDocument
from cortex.diarize.attribution import ConfirmedVoice
from cortex.domain.models import SourceAsset
from cortex.domain.store import DomainStore
from cortex.edit.camera_plan_schemas import CameraEditPlanDocument

AUTO_FRAMING_VERSION = "1.3.0"
MIN_SPEAKER_SECONDS = 1.2
MIN_CONFIDENCE = 0.6
FACE_FIRST_PRESENCE = 0.8
FACE_FIRST_NOISE_TRACK = 0.2
FACE_FIRST_MIN_SWITCH_US = 1_500_000
FACE_FIRST_MIN_SUBSPAN_US = 2_000_000


class SceneFramingOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=0)
    target: Literal["left", "right", "full"]


class AutoFramingSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_order: int
    scene_index: int
    source_start_us: int
    source_end_us: int
    visual_origin: str
    mode: Literal["face_crop", "blurred_background"]
    track_id: str | None = None
    reason: str
    sample_count: int = 0
    crop_x: int = 0
    crop_y: int = 0
    crop_width: int = 0
    crop_height: int = 0
    requested_zoom: float = 1.0
    effective_zoom: float = 1.0
    zoom_reason: str = "disabled"


def load_framing_inputs(domain: DomainStore, plan: CameraEditPlanDocument, source: SourceAsset):
    """Resolve and validate persisted dependencies before any video is encoded."""
    hashes: dict[str, str] = {}

    def read(artifact_id, stage, expected_hash, model):
        artifact = domain.get_stage_artifact(artifact_id)
        if (artifact.project_id != plan.project_id or artifact.stage != stage
                or artifact.input_hash != expected_hash):
            raise ValueError(f"cadeia de enquadramento inválida: {stage}")
        payload = Path(artifact.path).read_bytes()
        doc = model.model_validate_json(payload)
        if (doc.project_id != plan.project_id or doc.source_asset_id != source.id
                or doc.source_sha256 != source.sha256 or doc.input_hash != expected_hash):
            raise ValueError(f"fonte/hash do enquadramento não corresponde: {stage}")
        hashes[stage] = hashlib.sha256(payload).hexdigest()
        return doc

    camera = read(plan.camera_timeline_artifact_id, "camera_timeline",
                  plan.camera_timeline_input_hash, CameraTimelineDocument)
    faces = read(camera.face_index_artifact_id, "face_index",
                 camera.face_index_input_hash, FaceIndexDocument)
    speakers = read(camera.speaker_timeline_artifact_id, "speaker_timeline",
                    camera.speaker_timeline_input_hash, SpeakerTimelineDocument)
    if (faces.scene_index_artifact_id != camera.scene_index_artifact_id
            or speakers.scene_index_artifact_id != camera.scene_index_artifact_id
            or speakers.scene_index_input_hash != camera.scene_index_input_hash
            or speakers.face_index_artifact_id != camera.face_index_artifact_id
            or speakers.face_index_input_hash != camera.face_index_input_hash):
        raise ValueError("cenas/faces/fala não pertencem à mesma análise")
    return faces, speakers, hashes


def resolve_auto_framing(
    plan: CameraEditPlanDocument, faces: FaceIndexDocument, speakers: SpeakerTimelineDocument,
    *, source_width: int, source_height: int, width: int, height: int, fps: int,
    zoom: float = 1.0, alternate: bool = True,
    overrides: list[SceneFramingOverride] | None = None,
    policy: Literal["default", "face_first"] = "default",
    confirmed_voice: ConfirmedVoice | dict | None = None,
    primary_side: Literal["left", "right"] | None = None,
) -> list[AutoFramingSpan]:
    frames = sorted(faces.frames, key=lambda frame: frame.time)
    times = [round(frame.time * 1_000_000) for frame in frames]
    result: list[AutoFramingSpan] = []
    choices = {item.scene_index: item.target for item in overrides or []}
    if policy == "face_first":
        voice = (ConfirmedVoice.model_validate(confirmed_voice)
                 if isinstance(confirmed_voice, dict) else confirmed_voice)
        return _resolve_face_first(
            plan, frames, times, choices, voice, primary_side,
            source_width=source_width, source_height=source_height, width=width, height=height,
        )
    for shot in plan.shots:
        start, end = shot.source_start_us, shot.source_end_us
        manual = choices.get(shot.scene_index) if shot.visual_origin == "primary_in_place" else None
        shot_samples = frames[bisect_left(times, start):bisect_left(times, end)]
        # The master already contains editorial camera choices. A table/two-shot
        # provides real conversation context; mouth motion must not erase it by
        # turning every speaker into a close-up. Observed companions also protect
        # context when the coarse camera classification misses a wide layout.
        keep_context = manual is None and (
            shot.intent == "context" or shot.camera_role in {"wide", "two_shot"}
            or any(len(frame.faces) > 1 for frame in shot_samples)
        )
        # Only sustained visual speaker evidence creates a new framing boundary.
        turns = []
        if shot.visual_origin == "primary_in_place" and manual is None and not keep_context:
            for turn in speakers.segments:
                left, right = max(start, turn.start_us), min(end, turn.end_us)
                if (turn.state == "speaker" and turn.confidence >= MIN_CONFIDENCE
                        and right - left >= MIN_SPEAKER_SECONDS * 1_000_000):
                    turns.append((left, right, turn.speaker_track_id))
        # Brief confidence gaps between the same speaker do not make the crop
        # bounce out to the wide image. Never bridge a different/overlapping speaker.
        consolidated = []
        for left, right, track in sorted(turns):
            if (consolidated and consolidated[-1][2] == track
                    and 0 <= left - consolidated[-1][1] <= 750_000
                    and not any(turn.state in {"speaker", "overlap"}
                                and turn.speaker_track_id != track
                                and turn.start_us < left and turn.end_us > consolidated[-1][1]
                                for turn in speakers.segments)):
                consolidated[-1] = (consolidated[-1][0], right, track)
            else:
                consolidated.append((left, right, track))
        turns = consolidated
        boundaries = sorted({start, end, *(value for a, b, _ in turns for value in (a, b))})
        # Merge adjacent turns of the same speaker; do not add repeated cuts.
        windows: list[tuple[int, int, str | None]] = []
        for left, right in zip(boundaries, boundaries[1:]):
            targets = {track for a, b, track in turns if a <= left and b >= right}
            target = next(iter(targets)) if len(targets) == 1 else None
            if windows and windows[-1][2] == target:
                windows[-1] = (windows[-1][0], right, target)
            else:
                windows.append((left, right, target))
        # Hold a stable composition through sub-second confidence gaps instead
        # of flashing the full-frame fallback for a handful of frames.
        index = 0
        while index < len(windows):
            left, right, target = windows[index]
            if target is None and right - left <= 750_000 and len(windows) > 1:
                if index > 0 and windows[index - 1][2] is not None:
                    previous = windows[index - 1]
                    windows[index - 1] = (previous[0], right, previous[2])
                    windows.pop(index)
                    continue
                if index == 0 and windows[1][2] is not None:
                    following = windows[1]
                    windows[1] = (left, following[1], following[2])
                    windows.pop(0)
                    continue
            index += 1
        for left, right, target in windows:
            samples = frames[bisect_left(times, left):bisect_left(times, right)]
            span = AutoFramingSpan(
                segment_order=shot.edit_segment_order, scene_index=shot.scene_index,
                source_start_us=left, source_end_us=right, visual_origin=shot.visual_origin,
                mode="blurred_background", reason="insufficient_face_evidence",
                sample_count=len(samples), requested_zoom=zoom,
            )
            if keep_context:
                span.reason = "source_context_preserved"
                span.zoom_reason = "context_preserved" if zoom > 1 else "disabled"
                result.append(span)
                continue
            # Every sampled frame must support the crop. Empty frames are evidence
            # of absence, not discarded observations that inflate confidence.
            tracks = {face.track_id for frame in samples for face in frame.faces
                      if face.track_id is not None and face.score >= MIN_CONFIDENCE}
            single_face = bool(samples) and all(len(frame.faces) == 1 for frame in samples)
            if single_face and len(tracks) == 1:
                target = next(iter(tracks))
                reason = "single_visible_person"  # Includes the real listener shot.
            else:
                reason = "sustained_visual_speaker"
            if manual == "full":
                span.reason = "manual_full_frame"
                result.append(span)
                continue
            if manual in {"left", "right"} and samples and all(frame.faces for frame in samples):
                chosen = [sorted(frame.faces, key=lambda face: face.x + face.width / 2)[
                    0 if manual == "left" else -1] for frame in samples]
                targets = {face.track_id for face in chosen}
                target = next(iter(targets)) if len(targets) == 1 else None
                reason = f"manual_{manual}"
            selected = [next((face for face in frame.faces if face.track_id == target
                              and face.score >= MIN_CONFIDENCE), None) for frame in samples]
            duplicate_target = target is not None and any(
                sum(face.track_id == target for face in frame.faces) > 1 for frame in samples
            )
            if duplicate_target:
                span.reason = "ambiguous_duplicate_track"
            elif not shot.visual_quality_usable:
                span.reason = "scene_quality_uncertain"
            elif not target:
                span.reason = "speaker_uncertain_keep_context"
            elif not selected or any(face is None for face in selected):
                span.reason = "target_not_consistently_visible"
            elif len(samples) < 2 and right - left > 1_200_000:
                span.reason = "sparse_face_evidence"
            else:
                detected = [face for face in selected if face is not None]
                base_w = min(source_width, int(source_height * width / height)) // 2 * 2
                base_h = min(source_height, int(source_width * height / width)) // 2 * 2
                center_x = median((face.x + face.width / 2) * source_width for face in detected)
                center_y = source_height / 2
                # Zoom is optional and skipped if it would clip any sampled face
                # or amplify native source detail more than 3x.
                effective_zoom = zoom if not alternate or shot.edit_segment_order % 2 == 0 else 1.0
                span.zoom_reason = "applied" if effective_zoom > 1 else "disabled_or_alternating"
                for scale in dict.fromkeys((effective_zoom, 1.0)):
                    crop_w, crop_h = int(base_w / scale) // 2 * 2, int(base_h / scale) // 2 * 2
                    if crop_w < 2 or crop_h < 2:
                        continue
                    x = max(0, min(source_width - crop_w, round(center_x - crop_w / 2))) // 2 * 2
                    y = max(0, min(source_height - crop_h, round(center_y - crop_h / 2))) // 2 * 2
                    fits = all(
                        x <= (face.x - face.width * 0.15) * source_width
                        and x + crop_w >= (face.x + face.width * 1.15) * source_width
                        and y <= (face.y - face.height * 0.2) * source_height
                        and y + crop_h >= (face.y + face.height * 1.1) * source_height
                        for face in detected
                    )
                    if fits and (scale == 1.0 or max(width / crop_w, height / crop_h) <= 3):
                        span.mode, span.reason, span.track_id = "face_crop", reason, target
                        span.crop_x, span.crop_y = x, y
                        span.crop_width, span.crop_height = crop_w, crop_h
                        span.effective_zoom = scale
                        if scale != effective_zoom:
                            span.zoom_reason = "limited_by_face_or_source_resolution"
                        break
                if span.mode != "face_crop":
                    span.reason = "face_outside_safe_crop"
            result.append(span)
    return result


def _voice_subspans(
    start: int, end: int, voice: ConfirmedVoice, primary_side: str,
) -> list[tuple[int, int, str]]:
    """Split a shot by the confirmed voice, holding the target through short interjections."""
    other = "right" if primary_side == "left" else "left"
    turns = []
    for turn in sorted(voice.turns, key=lambda item: (item.start, item.end)):
        left, right = max(start, round(turn.start * 1_000_000)), min(end, round(turn.end * 1_000_000))
        if right > left:
            turns.append((left, right, primary_side if turn.speaker == voice.speaker else other))
    if not turns:
        return [(start, end, primary_side)]
    current = next((side for a, b, side in turns if b - a >= FACE_FIRST_MIN_SWITCH_US), turns[0][2])
    changes: list[tuple[int, str]] = [(start, current)]
    for a, b, side in turns:
        if side != current and b - a >= FACE_FIRST_MIN_SWITCH_US:
            current = side
            changes.append((a, side))
    spans = [[a, b, side] for (a, side), (b, _) in zip(changes, [*changes[1:], (end, "")])]
    while len(spans) > 1:
        shortest = min(range(len(spans)), key=lambda i: spans[i][1] - spans[i][0])
        if spans[shortest][1] - spans[shortest][0] >= FACE_FIRST_MIN_SUBSPAN_US:
            break
        neighbor = shortest - 1 if shortest > 0 else shortest + 1
        low, high = sorted((neighbor, shortest))
        spans[low] = [spans[low][0], spans[high][1], spans[neighbor][2]]
        spans.pop(high)
        merged = [spans[0]]
        for item in spans[1:]:
            if item[2] == merged[-1][2]:
                merged[-1][1] = item[1]
            else:
                merged.append(item)
        spans = merged
    return [(a, b, side) for a, b, side in spans]


def _resolve_face_first(
    plan: CameraEditPlanDocument, frames, times: list[int], choices: dict[int, str],
    voice: ConfirmedVoice | None, primary_side: str | None,
    *, source_width: int, source_height: int, width: int, height: int,
) -> list[AutoFramingSpan]:
    """Always crop on a face: the only person, or the voice-selected side of a two-shot."""
    result: list[AutoFramingSpan] = []
    base_w = min(source_width, int(source_height * width / height)) // 2 * 2
    base_h = min(source_height, int(source_width * height / width)) // 2 * 2

    def usable(frame, track):
        return next((face for face in frame.faces
                     if face.track_id == track and face.score >= MIN_CONFIDENCE), None)

    for shot in plan.shots:
        start, end = shot.source_start_us, shot.source_end_us
        manual = choices.get(shot.scene_index) if shot.visual_origin == "primary_in_place" else None
        shot_samples = frames[bisect_left(times, start):bisect_left(times, end)]

        def emit(left, right, track, reason):
            samples = frames[bisect_left(times, left):bisect_left(times, right)]
            span = AutoFramingSpan(
                segment_order=shot.edit_segment_order, scene_index=shot.scene_index,
                source_start_us=left, source_end_us=right, visual_origin=shot.visual_origin,
                mode="blurred_background", reason="face_first_target_missing",
                sample_count=len(samples),
            )
            result.append(span)
            if manual == "full":
                span.reason = "manual_full_frame"
                return
            if not shot.visual_quality_usable:
                span.reason = "scene_quality_uncertain"
                return
            selected = [usable(frame, track) for frame in samples] if track else []
            detected = [face for face in selected if face is not None]
            if not samples or not detected or len(detected) / len(samples) < FACE_FIRST_PRESENCE:
                return
            center_x = median((face.x + face.width / 2) * source_width for face in detected)
            x = max(0, min(source_width - base_w, round(center_x - base_w / 2))) // 2 * 2
            y = max(0, (source_height - base_h) // 2) // 2 * 2
            fits = sum(
                1 for face in detected
                if x <= (face.x - face.width * 0.15) * source_width
                and x + base_w >= (face.x + face.width * 1.15) * source_width
                and y <= (face.y - face.height * 0.2) * source_height
                and y + base_h >= (face.y + face.height * 1.1) * source_height
            )
            if fits / len(samples) < FACE_FIRST_PRESENCE:
                span.reason = "face_first_target_outside_crop"
                return
            span.mode, span.reason, span.track_id = "face_crop", reason, track
            span.crop_x, span.crop_y, span.crop_width, span.crop_height = x, y, base_w, base_h
            span.zoom_reason = "disabled"

        if manual in {"left", "right"} and shot_samples and all(f.faces for f in shot_samples):
            chosen = {sorted(f.faces, key=lambda face: face.x + face.width / 2)[
                0 if manual == "left" else -1].track_id for f in shot_samples}
            emit(start, end, next(iter(chosen)) if len(chosen) == 1 else None, f"manual_{manual}")
            continue

        stats: dict[str, list[float]] = {}
        presence: dict[str, int] = {}
        for frame in shot_samples:
            for face in frame.faces:
                if face.track_id is not None and face.score >= MIN_CONFIDENCE:
                    stats.setdefault(face.track_id, []).append(face.x + face.width / 2)
                    presence[face.track_id] = presence.get(face.track_id, 0) + 1
        area = {track: sum(face.width * face.height for frame in shot_samples for face in frame.faces
                           if face.track_id == track) / presence[track] for track in presence}
        total = max(1, len(shot_samples))
        significant = [track for track in presence if presence[track] / total >= FACE_FIRST_NOISE_TRACK]
        if len(significant) <= 1:
            only = significant[0] if significant else None
            emit(start, end, only, "face_first_single_person")
            continue
        ordered = sorted(significant, key=lambda track: median(stats[track]))
        sides = {"left": ordered[0], "right": ordered[-1]}
        dominant = max(significant, key=lambda track: (area[track], track))
        if primary_side is None:
            emit(start, end, dominant, "face_first_dominant_face")
        elif voice is None:
            emit(start, end, sides[primary_side], "face_first_primary_side")
        else:
            for left, right, side in _voice_subspans(start, end, voice, primary_side):
                emit(left, right, sides[side], "face_first_voice_turn")
    return result
