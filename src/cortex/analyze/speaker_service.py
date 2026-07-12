from __future__ import annotations

import hashlib
import json
import subprocess
from bisect import bisect_right
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import BinaryIO

import numpy as np

from cortex.analyze.face_schemas import FaceDetection, FaceIndexDocument, FrameFaces
from cortex.analyze.scene_detect import ffmpeg_version
from cortex.analyze.scene_schemas import SceneIndexDocument, SceneSegment
from cortex.analyze.schemas import AnalysisDocument
from cortex.analyze.speaker_motion import compensated_motion, select_speaker, smooth_scores
from cortex.analyze.speaker_schemas import (
    SPEAKER_TIMELINE_SCHEMA_VERSION,
    SpeakerEngineInfo,
    SpeakerObservation,
    SpeakerSegment,
    SpeakerTimelineDocument,
    TrackScore,
)
from cortex.config import CortexConfig
from cortex.domain.models import SourceAsset, StageArtifact
from cortex.domain.store import DomainStore
from cortex.paths import speakers_dir

SPEAKER_ALGORITHM_VERSION = "1.0.0"
SPEAKER_FRAME_WIDTH = 320
MOTION_THRESHOLD = 0.08
WINNER_MARGIN = 0.03
_MAX_FACE_SAMPLE_DISTANCE_SECONDS = 1.25
_CHUNK_MERGE_GAP_SECONDS = 2.0


class SpeakerTimelineJobCancelled(RuntimeError):
    pass


class SpeakerTimelinePreconditionError(ValueError):
    pass


def _read_token(stream: BinaryIO) -> bytes | None:
    token = bytearray()
    while True:
        char = stream.read(1)
        if not char:
            return bytes(token) or None
        if char == b"#" and not token:
            stream.readline()
            continue
        if char.isspace():
            if token:
                return bytes(token)
            continue
        token.extend(char)


def _read_ppm(stream: BinaryIO) -> np.ndarray | None:
    magic = _read_token(stream)
    if magic is None:
        return None
    if magic != b"P6":
        raise SpeakerTimelinePreconditionError("stream de frames do ffmpeg não é PPM P6")
    width_token, height_token, maxval_token = (_read_token(stream) for _ in range(3))
    if None in (width_token, height_token, maxval_token):
        raise SpeakerTimelinePreconditionError("cabeçalho PPM truncado no tracking de interlocutor")
    width, height, maxval = int(width_token), int(height_token), int(maxval_token)
    if width <= 0 or height <= 0 or maxval != 255:
        raise SpeakerTimelinePreconditionError("cabeçalho PPM inválido no tracking de interlocutor")
    payload = stream.read(width * height * 3)
    if len(payload) != width * height * 3:
        raise SpeakerTimelinePreconditionError("frame PPM truncado no tracking de interlocutor")
    return np.frombuffer(payload, dtype=np.uint8).reshape(height, width, 3)


def _coalesced_vad_chunks(
    analysis: AnalysisDocument, *, padding: float, duration: float
) -> list[tuple[float, float]]:
    windows = [
        (max(0.0, interval.start - padding), min(duration, interval.end + padding))
        for interval in analysis.vad_intervals
        if interval.end > interval.start
    ]
    chunks: list[tuple[float, float]] = []
    for start, end in sorted(windows):
        if chunks and start - chunks[-1][1] <= _CHUNK_MERGE_GAP_SECONDS:
            chunks[-1] = (chunks[-1][0], max(chunks[-1][1], end))
        else:
            chunks.append((start, end))
    return chunks


def _iter_chunk_frames(
    ffmpeg: str | Path,
    source_path: Path,
    *,
    start: float,
    end: float,
    sample_fps: float,
    should_cancel: Callable[[], bool],
) -> Iterator[tuple[float, np.ndarray]]:
    command = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.6f}",
        "-i", str(source_path), "-t", f"{max(0.0, end - start):.6f}",
        "-vf", f"fps={sample_fps:.6f},scale={SPEAKER_FRAME_WIDTH}:-2",
        "-pix_fmt", "rgb24", "-f", "image2pipe", "-vcodec", "ppm", "-",
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise SpeakerTimelinePreconditionError(f"ffmpeg não pôde extrair frames: {exc}") from exc
    assert process.stdout is not None
    try:
        index = 0
        while True:
            if should_cancel():
                process.terminate()
                raise SpeakerTimelineJobCancelled()
            frame = _read_ppm(process.stdout)
            if frame is None:
                break
            yield start + index / sample_fps, frame
            index += 1
        stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
        return_code = process.wait(timeout=30)
        if return_code:
            raise SpeakerTimelinePreconditionError(
                f"ffmpeg falhou no tracking de interlocutor ({return_code}): {stderr[-2000:]}"
            )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def _scene_at(
    scenes: list[SceneSegment], scene_starts: list[float], timestamp: float
) -> SceneSegment | None:
    index = bisect_right(scene_starts, timestamp) - 1
    if index < 0 or index >= len(scenes):
        return None
    scene = scenes[index]
    if timestamp < scene.end or (index == len(scenes) - 1 and abs(timestamp - scene.end) < 1e-6):
        return scene
    return None


def _speech_active(vad_starts: list[float], vad_ends: list[float], timestamp: float) -> bool:
    index = bisect_right(vad_starts, timestamp) - 1
    return index >= 0 and timestamp < vad_ends[index]


def _interpolate_faces(
    candidates: list[FrameFaces], timestamp: float
) -> list[FaceDetection]:
    candidates = [
        frame for frame in candidates if abs(frame.time - timestamp) <= _MAX_FACE_SAMPLE_DISTANCE_SECONDS
    ]
    if not candidates:
        return []
    before = max((frame for frame in candidates if frame.time <= timestamp), key=lambda item: item.time, default=None)
    after = min((frame for frame in candidates if frame.time >= timestamp), key=lambda item: item.time, default=None)
    if before is None or after is None or before.time == after.time:
        nearest = min(candidates, key=lambda item: abs(item.time - timestamp))
        return nearest.faces
    after_by_track = {face.track_id: face for face in after.faces if face.track_id}
    ratio = (timestamp - before.time) / (after.time - before.time)
    output: list[FaceDetection] = []
    for first in before.faces:
        second = after_by_track.get(first.track_id)
        if second is None or first.track_id is None:
            continue
        first_data = first.model_dump()
        second_data = second.model_dump()
        for key in ("x", "y", "width", "height", "score"):
            first_data[key] = first_data[key] + (second_data[key] - first_data[key]) * ratio
        for key in first_data["landmarks"]:
            a = first_data["landmarks"][key]
            b = second_data["landmarks"][key]
            first_data["landmarks"][key] = (
                a[0] + (b[0] - a[0]) * ratio,
                a[1] + (b[1] - a[1]) * ratio,
            )
        output.append(FaceDetection(**first_data))
    return output


def _segments_from_observations(
    observations: list[SpeakerObservation], analysis: AnalysisDocument, duration_us: int
) -> list[SpeakerSegment]:
    segments: list[SpeakerSegment] = []
    cursor = 0
    observation_cursor = 0
    for vad in analysis.vad_intervals:
        start_us = max(cursor, round(vad.start * 1_000_000))
        end_us = min(duration_us, round(vad.end * 1_000_000))
        if start_us >= end_us:
            continue
        if cursor < start_us:
            segments.append(SpeakerSegment(
                start_us=cursor, end_us=start_us, state="no_speech", confidence=1.0,
                evidence="global_vad",
            ))
        while observation_cursor < len(observations) and observations[observation_cursor].time_us < start_us:
            observation_cursor += 1
        matching: list[SpeakerObservation] = []
        while (
            observation_cursor < len(observations)
            and observations[observation_cursor].time_us < end_us
        ):
            matching.append(observations[observation_cursor])
            observation_cursor += 1
        groups: list[tuple[int, int, str, str | None, list[float]]] = []
        if not matching:
            groups.append((start_us, end_us, "unknown", None, [0.0]))
        else:
            boundaries = [start_us]
            boundaries.extend((a.time_us + b.time_us) // 2 for a, b in zip(matching, matching[1:]))
            boundaries.append(end_us)
            for index, item in enumerate(matching):
                cell = (boundaries[index], boundaries[index + 1], item.state,
                        item.speaker_track_id, [item.confidence])
                if groups and groups[-1][2:4] == cell[2:4]:
                    previous = groups[-1]
                    groups[-1] = (previous[0], cell[1], previous[2], previous[3], previous[4] + cell[4])
                else:
                    groups.append(cell)
        for group_start, group_end, state, track_id, confidence_values in groups:
            segments.append(SpeakerSegment(
                start_us=group_start, end_us=group_end, state=state,
                speaker_track_id=track_id,
                confidence=round(sum(confidence_values) / len(confidence_values), 6),
                evidence="mouth_motion+global_vad" if state != "unknown" else "insufficient_visual_evidence",
            ))
        cursor = end_us
    if cursor < duration_us:
        segments.append(SpeakerSegment(
            start_us=cursor, end_us=duration_us, state="no_speech", confidence=1.0,
            evidence="global_vad",
        ))
    return segments


class SpeakerTimelineService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def run(
        self,
        *,
        source_asset: SourceAsset,
        scene_index_artifact: StageArtifact,
        face_index_artifact: StageArtifact,
        analysis_artifact: StageArtifact,
        sample_fps: float | None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        if should_cancel():
            raise SpeakerTimelineJobCancelled()
        source_path = self._config.paths.data_dir / source_asset.stored_path
        paths = [Path(item.path) for item in (scene_index_artifact, face_index_artifact, analysis_artifact)]
        if not source_path.exists() or not all(path.exists() for path in paths):
            raise SpeakerTimelinePreconditionError("fonte ou artifact upstream não está disponível")
        progress_cb(0.0, "Validando índices e VAD")
        scene_index = SceneIndexDocument.model_validate_json(paths[0].read_text(encoding="utf-8"))
        face_index = FaceIndexDocument.model_validate_json(paths[1].read_text(encoding="utf-8"))
        analysis = AnalysisDocument.model_validate_json(paths[2].read_text(encoding="utf-8"))
        if scene_index.source_asset_id != source_asset.id or scene_index.source_sha256 != source_asset.sha256:
            raise SpeakerTimelinePreconditionError("scene_index não corresponde à fonte")
        if (
            face_index.source_asset_id != source_asset.id
            or face_index.source_sha256 != source_asset.sha256
            or face_index.scene_index_artifact_id != scene_index_artifact.id
        ):
            raise SpeakerTimelinePreconditionError("face_index não corresponde à cadeia fonte/scene_index")
        if analysis.source_asset_id != source_asset.id:
            raise SpeakerTimelinePreconditionError("analysis/VAD não corresponde à fonte")

        requested_fps = round(float(sample_fps or self._config.analysis.speaker_sample_fps), 4)
        padding = round(self._config.analysis.speaker_vad_padding_seconds, 4)
        duration = min(scene_index.duration_seconds, face_index.duration_seconds, analysis.duration_seconds)
        duration_us = max(0, round(duration * 1_000_000))
        effective_ffmpeg_version = ffmpeg_version(self._config.render.ffmpeg)
        hash_payload = {
            "algorithm": SPEAKER_ALGORITHM_VERSION,
            "schema_version": SPEAKER_TIMELINE_SCHEMA_VERSION,
            "source_asset_id": source_asset.id,
            "source_sha256": source_asset.sha256,
            "scene_index": [scene_index_artifact.id, scene_index_artifact.input_hash],
            "face_index": [face_index_artifact.id, face_index_artifact.input_hash],
            "analysis": [analysis_artifact.id, analysis_artifact.input_hash],
            "sample_fps": requested_fps,
            "frame_width": SPEAKER_FRAME_WIDTH,
            "vad_padding_seconds": padding,
            "motion_threshold": MOTION_THRESHOLD,
            "winner_margin": WINNER_MARGIN,
            "ffmpeg_version": effective_ffmpeg_version,
        }
        input_hash = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cached = self._domain.find_cached_stage_artifact(
            project_id=source_asset.project_id, stage="speaker_timeline", input_hash=input_hash,
            schema_version=SPEAKER_TIMELINE_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Timeline de interlocutores em cache reutilizada")
            return {
                "cached": True, "speaker_timeline_artifact_id": cached.id,
                "speaker_timeline_path": cached.path, "schema_version": cached.schema_version,
            }

        chunks = _coalesced_vad_chunks(analysis, padding=padding, duration=duration)
        scene_starts = [scene.start for scene in scene_index.scenes]
        vad_starts = [interval.start for interval in analysis.vad_intervals]
        vad_ends = [interval.end for interval in analysis.vad_intervals]
        face_frames_by_scene: dict[int, list[FrameFaces]] = {
            scene.index: [] for scene in scene_index.scenes
        }
        for face_frame in face_index.frames:
            scene = _scene_at(scene_index.scenes, scene_starts, face_frame.time)
            if scene is not None:
                face_frames_by_scene[scene.index].append(face_frame)
        records: list[dict] = []
        effective_width = SPEAKER_FRAME_WIDTH
        for chunk_index, (start, end) in enumerate(chunks):
            previous_frame = None
            previous_faces: dict[str, FaceDetection] = {}
            previous_scene_index = None
            for timestamp, frame in _iter_chunk_frames(
                self._config.render.ffmpeg, source_path, start=start, end=end,
                sample_fps=requested_fps, should_cancel=should_cancel,
            ):
                effective_width = frame.shape[1]
                scene = _scene_at(scene_index.scenes, scene_starts, timestamp)
                if scene is None:
                    continue
                faces = _interpolate_faces(face_frames_by_scene.get(scene.index, []), timestamp)
                current_faces = {face.track_id: face for face in faces if face.track_id}
                scores: dict[str, float] = {}
                if previous_frame is not None and previous_scene_index == scene.index:
                    for track_id, face in current_faces.items():
                        previous_face = previous_faces.get(track_id)
                        if previous_face is not None:
                            scores[track_id] = compensated_motion(
                                previous_frame, frame, previous_face, face
                            )
                records.append({
                    "time": timestamp, "scene_index": scene.index,
                    "speech_active": _speech_active(vad_starts, vad_ends, timestamp), "scores": scores,
                })
                previous_frame, previous_faces, previous_scene_index = frame, current_faces, scene.index
            progress_cb(
                5.0 + 80.0 * (chunk_index + 1) / max(len(chunks), 1),
                f"Analisando movimento labial no chunk {chunk_index + 1}/{len(chunks)}",
            )

        score_groups: dict[tuple[int, str], list[int]] = {}
        for record_index, record in enumerate(records):
            for track_id in record["scores"]:
                score_groups.setdefault((record["scene_index"], track_id), []).append(record_index)
        for (_scene_index, track_id), record_indices in score_groups.items():
            smoothed = smooth_scores([records[index]["scores"][track_id] for index in record_indices])
            baseline_values = [
                score for record_index, score in zip(record_indices, smoothed)
                if not records[record_index]["speech_active"]
            ]
            baseline = 0.0
            if baseline_values:
                median = float(np.median(baseline_values))
                mad = float(np.median(np.abs(np.asarray(baseline_values) - median)))
                baseline = median + 2.5 * 1.4826 * mad
            for record_index, score in zip(record_indices, smoothed):
                records[record_index]["scores"][track_id] = float(
                    np.clip(score - baseline, 0.0, 1.0)
                )

        observations: list[SpeakerObservation] = []
        for record in records:
            if not record["speech_active"]:
                continue
            state, speaker_track_id, confidence = select_speaker(
                record["scores"], threshold=MOTION_THRESHOLD, margin=WINNER_MARGIN
            )
            observations.append(SpeakerObservation(
                time_us=round(record["time"] * 1_000_000), scene_index=record["scene_index"],
                speech_active=True, state=state, speaker_track_id=speaker_track_id,
                confidence=round(confidence, 6),
                track_scores=[
                    TrackScore(
                        track_id=track_id, visible=True, mouth_motion_score=round(score, 6),
                        speaking_score=round(score, 6), confidence=round(min(1.0, score), 6),
                    )
                    for track_id, score in sorted(record["scores"].items())
                ],
            ))
        segments = _segments_from_observations(observations, analysis, duration_us)
        document = SpeakerTimelineDocument(
            project_id=source_asset.project_id, source_asset_id=source_asset.id,
            source_sha256=source_asset.sha256,
            scene_index_artifact_id=scene_index_artifact.id,
            scene_index_input_hash=scene_index_artifact.input_hash,
            face_index_artifact_id=face_index_artifact.id,
            face_index_input_hash=face_index_artifact.input_hash,
            analysis_artifact_id=analysis_artifact.id,
            analysis_input_hash=analysis_artifact.input_hash,
            input_hash=input_hash, duration_us=duration_us, observations=observations, segments=segments,
            engine=SpeakerEngineInfo(
                algorithm="visual_mouth_motion_vad_gated", algorithm_version=SPEAKER_ALGORITHM_VERSION,
                sample_fps_requested=requested_fps, sample_fps_effective=requested_fps,
                frame_width_requested=SPEAKER_FRAME_WIDTH, frame_width_effective=effective_width,
                vad_padding_seconds=padding, motion_threshold=MOTION_THRESHOLD,
                winner_margin=WINNER_MARGIN, ffmpeg_path=str(self._config.render.ffmpeg),
                ffmpeg_version=effective_ffmpeg_version, decoder_requested="ffmpeg_software",
                decoder_effective="ffmpeg_software", device_requested="cpu", device_effective="cpu",
            ),
        )
        if should_cancel():
            raise SpeakerTimelineJobCancelled()
        progress_cb(95.0, "Persistindo SpeakerTimelineArtifact")
        out_dir = speakers_dir(self._config, source_asset.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"speaker-timeline-{input_hash[:16]}.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if should_cancel():
            temporary_path.unlink(missing_ok=True)
            raise SpeakerTimelineJobCancelled()
        temporary_path.replace(output_path)
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=source_asset.project_id, stage="speaker_timeline",
            schema_version=SPEAKER_TIMELINE_SCHEMA_VERSION, path=str(output_path), input_hash=input_hash,
            metadata={
                "source_asset_id": source_asset.id,
                "scene_index_artifact_id": scene_index_artifact.id,
                "face_index_artifact_id": face_index_artifact.id,
                "analysis_artifact_id": analysis_artifact.id,
                "sample_fps": requested_fps,
                "observation_count": len(observations), "segment_count": len(segments),
            },
        ))
        progress_cb(100.0, "Tracking de interlocutor concluído")
        return {
            "cached": False, "speaker_timeline_artifact_id": artifact.id,
            "speaker_timeline_path": artifact.path, "schema_version": artifact.schema_version,
        }
