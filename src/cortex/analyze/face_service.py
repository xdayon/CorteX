from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import numpy as np

from cortex.analyze.face_classify import (
    aggregate_scene_summaries,
    assign_scene_tracks,
    classify_frame_shot,
)
from cortex.analyze.face_detect import (
    DEFAULT_NMS_THRESHOLD,
    DEFAULT_SCORE_THRESHOLD,
    MODEL_PATH,
    FaceDetectionError,
    detect_faces,
    load_session,
)
from cortex.analyze.face_recognition import (
    COSINE_MATCH_THRESHOLD,
    EMBEDDING_DIMENSION,
    MODEL_PATH as RECOGNITION_MODEL_PATH,
    MODEL_SHA256 as RECOGNITION_MODEL_SHA256,
    extract_embedding,
    load_recognition_session,
)
from cortex.analyze.face_schemas import (
    FACE_INDEX_SCHEMA_VERSION,
    FaceDetection,
    FaceIndexDocument,
    FaceIndexEngineInfo,
    FaceLandmarks,
    FrameFaces,
    SceneFaceSummary,
)
from cortex.analyze.scene_detect import ffmpeg_version
from cortex.analyze.scene_schemas import SceneIndexDocument
from cortex.config import CortexConfig
from cortex.domain.models import SourceAsset, StageArtifact
from cortex.domain.store import DomainStore
from cortex.ingest.ffprobe import duration_seconds, probe_media
from cortex.paths import faces_dir

FACE_ALGORITHM_VERSION = "3.0.0"
FRAME_EXTRACTION_WIDTH = 640


class FaceIndexJobCancelled(RuntimeError):
    pass


class FaceIndexPreconditionError(ValueError):
    pass


def _parse_ppm(data: bytes) -> np.ndarray:
    """Parse a P6 (binary RGB) PPM buffer, as produced by ffmpeg's ppm muxer.

    PPM is a trivial, dependency-free way to learn the exact width/height
    ffmpeg picked for ``scale=640:-2`` (which rounds to the nearest even
    number) without guessing at swscale's rounding rules ourselves.
    """
    if not data.startswith(b"P6"):
        raise FaceDetectionError("saída do ffmpeg não é um PPM (P6) válido")
    idx = 2
    values: list[int] = []
    while len(values) < 3:
        while idx < len(data) and data[idx : idx + 1].isspace():
            idx += 1
        if idx < len(data) and data[idx : idx + 1] == b"#":
            while idx < len(data) and data[idx : idx + 1] != b"\n":
                idx += 1
            continue
        start = idx
        while idx < len(data) and not data[idx : idx + 1].isspace():
            idx += 1
        if start == idx:
            raise FaceDetectionError("cabeçalho PPM malformado")
        values.append(int(data[start:idx]))
    idx += 1
    width, height, _maxval = values
    pixel_bytes = data[idx : idx + width * height * 3]
    if len(pixel_bytes) != width * height * 3:
        raise FaceDetectionError("dados de pixel PPM truncados")
    return np.frombuffer(pixel_bytes, dtype=np.uint8).reshape(height, width, 3)


def _extract_frame_rgb(ffmpeg: str | Path, source_path: Path, timestamp: float) -> np.ndarray:
    command = [
        str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, timestamp):.3f}", "-i", str(source_path),
        "-frames:v", "1", "-vf", f"scale={FRAME_EXTRACTION_WIDTH}:-2",
        "-pix_fmt", "rgb24", "-f", "image2pipe", "-vcodec", "ppm", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FaceDetectionError(f"extração de frame via ffmpeg falhou: {exc}") from exc
    if result.returncode != 0 or not result.stdout:
        detail = (result.stderr or b"").decode(errors="replace")[-2000:]
        raise FaceDetectionError(f"extração de frame via ffmpeg falhou ({result.returncode}): {detail}")
    return _parse_ppm(result.stdout)


def _sample_timestamps(
    *, scenes: list[dict], cuts: list[dict], duration: float, sample_fps: float
) -> list[float]:
    timestamps: set[float] = set()
    for scene in scenes:
        midpoint = (scene["start"] + scene["end"]) / 2.0
        timestamps.add(round(midpoint, 3))
    for cut in cuts:
        after_cut = cut["time"] + 0.3
        timestamps.add(round(after_cut, 3))
    if sample_fps > 0 and duration > 0:
        step = 1.0 / sample_fps
        grid_ts = 0.0
        while grid_ts < duration:
            timestamps.add(round(grid_ts, 3))
            grid_ts += step
    clipped = sorted(t for t in timestamps if 0.0 <= t < max(duration, 0.0)
                     and any(scene["start"] <= t < scene["end"] for scene in scenes))
    return clipped or ([0.0] if duration > 0 else [])


class FaceIndexService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def run(
        self,
        *,
        source_asset: SourceAsset,
        scene_index_artifact: StageArtifact,
        sample_fps: float | None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict:
        if should_cancel():
            raise FaceIndexJobCancelled()
        progress_cb(0.0, "Lendo metadados da fonte")
        source_path = self._config.paths.data_dir / source_asset.stored_path
        if not source_path.exists():
            raise FaceIndexPreconditionError("arquivo da fonte não está disponível para detecção de faces")

        scene_index_path = Path(scene_index_artifact.path)
        if not scene_index_path.exists():
            raise FaceIndexPreconditionError(
                "artifact scene_index não está disponível; execute a detecção de cenas antes das faces"
            )
        scene_index = SceneIndexDocument.model_validate_json(scene_index_path.read_text(encoding="utf-8"))

        probe = probe_media(self._config.render.ffprobe, source_path)
        duration = duration_seconds(probe)

        requested_sample_fps = (
            round(float(sample_fps), 4) if sample_fps is not None
            else round(self._config.analysis.face_sample_fps, 4)
        )
        effective_ffmpeg_version = ffmpeg_version(self._config.render.ffmpeg)

        hash_payload = {
            "algorithm": FACE_ALGORITHM_VERSION,
            "schema_version": FACE_INDEX_SCHEMA_VERSION,
            "source_asset_id": source_asset.id,
            "source_sha256": source_asset.sha256,
            "scene_index_artifact_id": scene_index_artifact.id,
            "scene_index_input_hash": scene_index_artifact.input_hash,
            "sample_fps": requested_sample_fps,
            "score_threshold": DEFAULT_SCORE_THRESHOLD,
            "nms_threshold": DEFAULT_NMS_THRESHOLD,
            "model": MODEL_PATH.name,
            "recognition_model": [RECOGNITION_MODEL_PATH.name, RECOGNITION_MODEL_SHA256],
        }
        input_hash = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        cached = self._domain.find_cached_stage_artifact(
            project_id=source_asset.project_id, stage="face_index", input_hash=input_hash,
            schema_version=FACE_INDEX_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Índice de faces em cache reutilizado")
            return {
                "cached": True, "face_index_artifact_id": cached.id,
                "face_index_path": cached.path, "schema_version": cached.schema_version,
            }

        if should_cancel():
            raise FaceIndexJobCancelled()
        progress_cb(5.0, "Selecionando timestamps de amostragem")
        scenes = [scene.model_dump() for scene in scene_index.scenes]
        cuts = [cut.model_dump() for cut in scene_index.cuts]
        timestamps = _sample_timestamps(
            scenes=scenes, cuts=cuts, duration=duration, sample_fps=requested_sample_fps
        )

        progress_cb(10.0, "Carregando modelo YuNet (onnxruntime CPU)")
        session = load_session()
        recognition_session = load_recognition_session()

        frames_data: list[dict] = []
        total = max(len(timestamps), 1)
        for position, timestamp in enumerate(timestamps):
            if should_cancel():
                raise FaceIndexJobCancelled()
            frame = _extract_frame_rgb(self._config.render.ffmpeg, source_path, timestamp)
            faces = detect_faces(session, frame)
            for face_data in faces:
                face_model = FaceDetection.model_validate(face_data)
                face_data["embedding"] = extract_embedding(
                    recognition_session, frame, face_model
                )
            shot_type = classify_frame_shot(faces)
            frames_data.append({"time": timestamp, "faces": faces, "shot_type": shot_type})
            progress_cb(10.0 + 75.0 * (position + 1) / total, f"Rostos: {position + 1}/{total} amostras · posição {timestamp:.0f}s do episódio")

        if should_cancel():
            raise FaceIndexJobCancelled()
        progress_cb(88.0, "Associando rostos por posição em cada cena")
        assign_scene_tracks(frames_data, scenes)

        if should_cancel():
            raise FaceIndexJobCancelled()
        progress_cb(92.0, "Agregando planos por cena")
        scene_summaries = aggregate_scene_summaries(frames_data, scenes)

        document = FaceIndexDocument(
            project_id=source_asset.project_id, source_asset_id=source_asset.id,
            source_sha256=source_asset.sha256, scene_index_artifact_id=scene_index_artifact.id,
            input_hash=input_hash, duration_seconds=round(duration, 4),
            frames=[
                FrameFaces(
                    time=frame["time"],
                    shot_type=frame["shot_type"],
                    faces=[
                        FaceDetection(
                            x=face["x"], y=face["y"], width=face["width"], height=face["height"],
                            score=face["score"], track_id=face["track_id"],
                            embedding=face["embedding"],
                            landmarks=FaceLandmarks(**face["landmarks"]),
                        )
                        for face in frame["faces"]
                    ],
                )
                for frame in frames_data
            ],
            scenes=[SceneFaceSummary(**summary) for summary in scene_summaries],
            frame_count=len(frames_data),
            engine=FaceIndexEngineInfo(
                detector="yunet_2023mar", model_path=str(MODEL_PATH),
                providers=list(session.get_providers()),
                score_threshold=DEFAULT_SCORE_THRESHOLD, nms_threshold=DEFAULT_NMS_THRESHOLD,
                sample_fps=requested_sample_fps, ffmpeg_path=str(self._config.render.ffmpeg),
                ffmpeg_version=effective_ffmpeg_version,
                recognizer="sface_2021dec",
                recognition_model_path=str(RECOGNITION_MODEL_PATH),
                recognition_model_sha256=RECOGNITION_MODEL_SHA256,
                embedding_dimension=EMBEDDING_DIMENSION,
                cosine_match_threshold=COSINE_MATCH_THRESHOLD,
            ),
        )

        if should_cancel():
            raise FaceIndexJobCancelled()
        progress_cb(97.0, "Persistindo FaceIndexArtifact")
        out_dir = faces_dir(self._config, source_asset.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"face-index-{input_hash[:16]}.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if should_cancel():
            temporary_path.unlink(missing_ok=True)
            raise FaceIndexJobCancelled()
        temporary_path.replace(output_path)
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=source_asset.project_id, stage="face_index",
            schema_version=FACE_INDEX_SCHEMA_VERSION, path=str(output_path), input_hash=input_hash,
            metadata={
                "source_asset_id": source_asset.id,
                "scene_index_artifact_id": scene_index_artifact.id,
                "sample_fps": requested_sample_fps,
                "frame_count": len(frames_data),
            },
        ))
        progress_cb(100.0, "Índice de faces concluído")
        return {
            "cached": False, "face_index_artifact_id": artifact.id,
            "face_index_path": artifact.path, "schema_version": artifact.schema_version,
        }
