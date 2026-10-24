"""Episode camera map: keyframes grouped by appearance into camera groups.

The broadcast master carries a keyframe at every camera cut, so decoding only keyframes
(~1 min for a 2 h episode) is enough to group the shots, measure each camera's airtime,
count faces per group and, when a voiceprint exists, how much of the group's airtime the
subject's voice covers. A person confirms the role of each group on the "Câmeras" screen.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from cortex.analyze.face_detect import FaceDetectionError, detect_faces, load_session
from cortex.analyze.frames import _extract_frame_rgb
from cortex.analyze.scene_detect import SceneDetectionError, ffmpeg_version
from cortex.config import CortexConfig
from cortex.domain.models import SourceAsset, StageArtifact
from cortex.domain.store import DomainStore
from cortex.ingest.ffprobe import duration_seconds, probe_media
from cortex.paths import analysis_dir, cache_dir

CAMERA_MAP_ALGORITHM_VERSION = "1"
CAMERA_MAP_SCHEMA_VERSION = 1
FRAME_WIDTH, FRAME_HEIGHT = 160, 90
CLUSTER_THRESHOLD = 0.75
CLUSTER_ITERATIONS = 10
MIN_GROUP_SHARE = 0.01
MIN_FACE_SCORE = 0.6
MIN_FACE_HEIGHT = 0.035
FACE_FRAME_WIDTH = 1280
FACE_SAMPLES = 5
THUMBNAIL_WIDTH = 480
KEYFRAME_TIMEOUT_SECONDS = 1800
MAX_KEYFRAMES_PER_SECOND = 2
Role = Literal["subject", "interviewers", "wide", "ignore"]
_PTS_RE = re.compile(r"pts_time:(-?[0-9.]+)")


class CameraMapError(RuntimeError):
    pass


class CameraMapCancelled(RuntimeError):
    pass


class CameraGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    sample_time: float
    airtime_seconds: float
    airtime_share: float
    shot_count: int
    median_shot_seconds: float
    faces: int
    subject_voice_share: float | None = None
    suggested_role: Role | None = None
    thumbnail_path: str


class CameraShot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float
    end: float
    group: int


class CameraMapDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    algorithm_version: str
    project_id: str
    source_asset_id: str
    source_sha256: str
    duration_seconds: float
    keyframe_count: int
    groups: list[CameraGroup]
    shots: list[CameraShot]
    voice_artifact_id: str | None = None
    engine: dict = Field(default_factory=dict)
    timings: dict = Field(default_factory=dict)


def frame_features(frame: np.ndarray) -> np.ndarray:
    """Appearance of one 160x90 RGB frame: normalised 32x18 gray thumbnail + coarse colour histogram."""
    small = frame.reshape(18, 5, 32, 5, 3).mean(axis=(1, 3))
    gray = small.mean(axis=2).ravel()
    gray = (gray - gray.mean()) / (gray.std() + 1e-6)
    bins = (frame // 64).reshape(-1, 3).astype(np.int64)
    hist = np.bincount(bins[:, 0] * 16 + bins[:, 1] * 4 + bins[:, 2], minlength=64).astype(np.float64)
    return np.concatenate([gray / np.sqrt(gray.size), hist / hist.sum() * 2.0])


def _stop(process: subprocess.Popen) -> None:
    process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - kill is not ignorable
        pass


def extract_keyframes(
    ffmpeg: str | Path, media_path: Path, *, should_cancel: Callable[[], bool],
    timeout: float = KEYFRAME_TIMEOUT_SECONDS, max_frames: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    command = [
        # `-discard nokey` drops non-key packets in the demuxer for any codec; the VP9
        # decoder ignores `-skip_frame nokey` and would decode every frame.
        str(ffmpeg), "-hide_banner", "-nostats", "-threads", "4", "-discard", "nokey", "-skip_frame", "nokey",
        "-i", str(media_path),
        "-vf", f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:flags=area,showinfo", "-fps_mode", "passthrough", "-an",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:",
    ]
    size = FRAME_WIDTH * FRAME_HEIGHT * 3
    features: list[np.ndarray] = []
    with tempfile.TemporaryFile() as log:
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log)
        except OSError as exc:
            raise CameraMapError(f"ffmpeg não pôde ser executado: {exc}") from exc
        # The watchdog also ends a read blocked on an ffmpeg that stopped writing.
        expired = threading.Event()

        def expire() -> None:
            expired.set()
            process.kill()

        watchdog = threading.Timer(timeout, expire)
        watchdog.start()
        try:
            while True:
                if should_cancel():
                    _stop(process)
                    raise CameraMapCancelled()
                raw = process.stdout.read(size)
                if len(raw) < size:
                    break
                features.append(frame_features(np.frombuffer(raw, dtype=np.uint8).reshape(FRAME_HEIGHT, FRAME_WIDTH, 3)))
                if max_frames is not None and len(features) > max_frames:
                    _stop(process)
                    raise CameraMapError(
                        "O vídeo tem keyframes demais (quase todo quadro); o mapa de câmeras precisa de um "
                        "master com keyframes espaçados, como os do YouTube"
                    )
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                _stop(process)
        finally:
            watchdog.cancel()
            if process.poll() is None:
                _stop(process)
            process.stdout.close()
        log.seek(0)
        stderr = log.read().decode(errors="replace")
    if expired.is_set():
        raise CameraMapError(f"ffmpeg excedeu o timeout de {timeout:.0f}s ao ler os keyframes")
    if process.returncode:
        raise CameraMapError(f"ffmpeg falhou ({process.returncode}): {stderr[-500:]}")
    times = [float(m) for m in _PTS_RE.findall(stderr)]
    if len(times) != len(features):
        raise CameraMapError(f"ffmpeg devolveu {len(features)} quadros e {len(times)} tempos: {stderr[-300:]}")
    if not features:
        raise CameraMapError("Nenhum keyframe encontrado no vídeo")
    order = np.argsort(times, kind="stable")
    return np.array(times)[order], np.stack(features)[order]


def nearest(features: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """Index of the nearest centre per row, as an N x K matrix (no N x K x D array)."""
    distances = (features ** 2).sum(axis=1)[:, None] - 2 * features @ centers.T + (centers ** 2).sum(axis=1)[None]
    return np.argmin(distances, axis=1)


def cluster(
    features: np.ndarray, threshold: float = CLUSTER_THRESHOLD, iterations: int = CLUSTER_ITERATIONS,
) -> tuple[np.ndarray, np.ndarray]:
    """Leader clustering refined by k-means from the leaders; returns (labels, centres)."""
    leaders = [features[0]]
    for vector in features[1:]:
        if np.linalg.norm(np.array(leaders) - vector, axis=1).min() > threshold:
            leaders.append(vector)
    centers = np.array(leaders)
    labels = np.zeros(len(features), dtype=int)
    for _ in range(iterations):
        labels = nearest(features, centers)
        centers = np.array([features[labels == k].mean(axis=0) if (labels == k).any() else centers[k]
                            for k in range(len(centers))])
    return labels, centers


def group_keyframes(features: np.ndarray, airtime: np.ndarray):
    """Cluster and number groups by descending airtime. Small groups stay separate:
    folding them into the nearest centre mixed vignettes into a camera group."""
    labels, centers = cluster(features)
    order = sorted(range(len(centers)), key=lambda k: (-airtime[labels == k].sum(), k))
    rank = {old: new for new, old in enumerate(order)}
    return np.array([rank[label] for label in labels]), centers[order]


def build_shots(times: np.ndarray, labels: np.ndarray, duration: float) -> list[CameraShot]:
    shots: list[CameraShot] = []
    starts = [0] + [i for i in range(1, len(labels)) if labels[i] != labels[i - 1]]
    for position, first in enumerate(starts):
        end = float(times[starts[position + 1]]) if position + 1 < len(starts) else duration
        shots.append(CameraShot(start=round(float(times[first]), 3), end=round(max(end, float(times[first])), 3),
                                group=int(labels[first])))
    return shots


def face_count(samples: Sequence[Sequence[dict]]) -> int:
    """Median of per-frame counts: people shift between distant samples, so pooling the
    faces of every sample by position would count the same person twice. Wide shots
    have faces of 4-9 % of the frame height, below the face-focus minimum."""
    counts = [sum(1 for face in sample if face["score"] >= MIN_FACE_SCORE and face["height"] >= MIN_FACE_HEIGHT)
              for sample in samples]
    return int(np.median(counts)) if counts else 0


def subject_voice_share(intervals: Sequence[tuple[float, float]], turns: Sequence) -> float | None:
    """Seconds overlapped by subject turns over seconds overlapped by any turn; None without overlap."""
    from cortex.diarize.voice_match import SUBJECT_LABEL

    ordered = sorted(turns, key=lambda turn: turn.start)
    subject = total = 0.0
    for start, end in intervals:
        for turn in ordered:
            if turn.start >= end:
                break
            overlap = min(end, turn.end) - max(start, turn.start)
            if overlap > 0:
                total += overlap
                if turn.speaker == SUBJECT_LABEL:
                    subject += overlap
    return round(subject / total, 4) if total > 0 else None


def suggest_role(faces: int, share: float | None, airtime_share: float = 1.0) -> Role | None:
    """A starting point for the person who confirms the roles. The subject's voice share
    is the strong signal; face counts only separate graphics and wide shots.

    Two faces is ambiguous: two hosts (subject share 0-13 % in 2026-10-19) or the subject
    beside a host, the base camera of many podcasts (32-53 % in wk7PY31TQBc, confirmed
    "subject"). The middle stays unsuggested for the person to decide."""
    if faces == 0 or airtime_share < MIN_GROUP_SHARE:
        return "ignore"
    if share is not None and share >= 0.6:
        return "subject"
    if faces >= 3:
        return "wide"
    if share is None:
        return None
    if faces == 2:
        return "subject" if share >= 0.3 else "interviewers" if share <= 0.12 else None
    return "interviewers" if share <= 0.35 else None


def _voice_turns(
    config: CortexConfig, domain: DomainStore, source: SourceAsset,
    progress_cb: Callable[[float, str], None], should_cancel: Callable[[], bool],
) -> tuple[list, str | None, dict]:
    """Turns labelled by the stored voiceprint, or none with the reason; never falls back to pyannote."""
    from cortex.diarize.service import DiarizationDocument
    from cortex.diarize.voice_match import VoiceMatchCancelled, load_profile, match_transcript

    if not config.voice.match_enabled:
        return [], None, {"status": "unavailable", "reason": "Reconhecimento de voz desligado na configuração"}
    try:
        profile = load_profile(config)
    except (OSError, ValueError) as exc:
        return [], None, {"status": "failed", "reason": f"Perfil de voz ilegível: {exc}"[:300]}
    if profile is None:
        return [], None, {"status": "unavailable", "reason": "Sem perfil de voz cadastrado"}
    transcripts = domain.list_transcript_artifacts(source.id)
    if not transcripts:
        return [], None, {"status": "unavailable", "reason": "Episódio sem transcrição"}
    try:
        result = match_transcript(
            config, domain, source=source, transcript_artifact=transcripts[0], profile=profile,
            progress_cb=lambda percent, message: progress_cb(2 + percent * 0.18, message), should_cancel=should_cancel,
        )
        artifact = domain.get_stage_artifact(result["diarization_artifact_id"])
        turns = DiarizationDocument.model_validate_json(Path(artifact.path).read_text()).turns
    except VoiceMatchCancelled:
        raise CameraMapCancelled() from None
    except Exception as exc:  # noqa: BLE001 - the map is still useful without the voice
        return [], None, {"status": "failed", "reason": str(exc)[:300]}
    return turns, artifact.id, {"status": "ok", "cached": bool(result.get("cached"))}


def _write_thumbnail(ffmpeg: str | Path, media_path: Path, time_seconds: float, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, time_seconds):.3f}",
        "-i", str(media_path), "-frames:v", "1", "-vf", f"scale={THUMBNAIL_WIDTH}:-2", "-q:v", "4", str(output),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CameraMapError(f"miniatura da câmera falhou: {exc}") from exc
    if result.returncode or not output.is_file():
        raise CameraMapError(f"miniatura da câmera falhou: {(result.stderr or b'').decode(errors='replace')[-300:]}")


def _relative(config: CortexConfig, path: Path) -> str:
    try:
        return str(path.relative_to(config.paths.data_dir))
    except ValueError:
        return str(path)


def build_camera_map(
    config: CortexConfig, domain: DomainStore, source: SourceAsset, *,
    progress_cb: Callable[[float, str], None], should_cancel: Callable[[], bool],
) -> StageArtifact:
    def check() -> None:
        if should_cancel():
            raise CameraMapCancelled()

    check()
    source_path = config.paths.data_dir / source.stored_path
    if not source_path.is_file():
        raise CameraMapError("Arquivo do episódio não está disponível para o mapa de câmeras")
    progress_cb(1, "Reconhecendo a voz do Dayon")
    turns, voice_artifact_id, voice = _voice_turns(config, domain, source, progress_cb, should_cancel)
    input_hash = hashlib.sha256(json.dumps({
        "source_sha256": source.sha256, "algorithm": CAMERA_MAP_ALGORITHM_VERSION,
        "voice_artifact_id": voice_artifact_id, "threshold": CLUSTER_THRESHOLD,
        "sizes": [FRAME_WIDTH, FRAME_HEIGHT, THUMBNAIL_WIDTH, FACE_SAMPLES, MIN_GROUP_SHARE,
                  FACE_FRAME_WIDTH, MIN_FACE_SCORE, MIN_FACE_HEIGHT],
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    cached = domain.find_cached_stage_artifact(
        project_id=source.project_id, stage="camera_map", input_hash=input_hash,
        schema_version=CAMERA_MAP_SCHEMA_VERSION,
    )
    if cached is not None and Path(cached.path).is_file():
        progress_cb(100, "Mapa de câmeras em cache reutilizado")
        return cached

    started = time.monotonic()
    try:
        duration = duration_seconds(probe_media(config.render.ffprobe, source_path))
        version = ffmpeg_version(config.render.ffmpeg)
    except (SceneDetectionError, ValueError, OSError) as exc:
        raise CameraMapError(f"Não foi possível ler o vídeo: {exc}") from exc
    progress_cb(20, "Lendo os keyframes do episódio")
    times, features = extract_keyframes(
        config.render.ffmpeg, source_path, should_cancel=should_cancel,
        max_frames=int(duration * MAX_KEYFRAMES_PER_SECOND) + 100,
    )
    keyframes_done = time.monotonic()
    duration = max(duration, float(times[-1]))
    airtime = np.diff(np.append(times, duration))
    progress_cb(55, "Agrupando os planos por câmera")
    labels, centers = group_keyframes(features, airtime)
    shots = build_shots(times, labels, duration)

    try:
        session = load_session()
    except FaceDetectionError as exc:
        raise CameraMapError(f"Detector de rostos indisponível: {exc}") from exc
    thumbs = cache_dir(config, source.project_id) / "camera_map" / input_hash[:16]
    total = max(float(airtime.sum()), 1e-9)
    groups: list[CameraGroup] = []
    for group_id in range(len(centers)):
        check()
        progress_cb(60 + 35 * group_id / len(centers), "Contando rostos por câmera")
        members = np.where(labels == group_id)[0]
        medoid = int(members[np.argmin(np.linalg.norm(features[members] - centers[group_id], axis=1))])
        # Rare groups (vignettes, transitions) are ignored anyway: one sample is enough.
        samples_wanted = FACE_SAMPLES if airtime[members].sum() / total >= MIN_GROUP_SHARE else 1
        picks = members[np.linspace(0, len(members) - 1, min(samples_wanted, len(members))).astype(int)]
        try:
            samples = [detect_faces(session, _extract_frame_rgb(config.render.ffmpeg, source_path, float(times[i]), FACE_FRAME_WIDTH))
                       for i in picks]
        except FaceDetectionError as exc:
            raise CameraMapError(f"Contagem de rostos falhou: {exc}") from exc
        faces = face_count(samples)
        thumbnail = thumbs / f"group-{group_id}.jpg"
        _write_thumbnail(config.render.ffmpeg, source_path, float(times[medoid]), thumbnail)
        group_shots = [s for s in shots if s.group == group_id]
        share = subject_voice_share([(s.start, s.end) for s in group_shots], turns) if turns else None
        group_airtime = float(airtime[members].sum())
        groups.append(CameraGroup(
            id=group_id, sample_time=round(float(times[medoid]), 3), airtime_seconds=round(group_airtime, 3),
            airtime_share=round(group_airtime / total, 4), shot_count=len(group_shots),
            median_shot_seconds=round(float(np.median([s.end - s.start for s in group_shots])), 3),
            faces=faces, subject_voice_share=share, suggested_role=suggest_role(faces, share, group_airtime / total),
            thumbnail_path=_relative(config, thumbnail),
        ))
    check()
    finished = time.monotonic()
    document = CameraMapDocument(
        algorithm_version=CAMERA_MAP_ALGORITHM_VERSION, project_id=source.project_id,
        source_asset_id=source.id, source_sha256=source.sha256, duration_seconds=round(duration, 3),
        keyframe_count=len(times), groups=groups, shots=shots, voice_artifact_id=voice_artifact_id,
        engine={"ffmpeg_version": version, "face_detector": "yunet", "voice": voice, "threshold": CLUSTER_THRESHOLD},
        timings={"keyframes_seconds": round(keyframes_done - started, 2),
                 "faces_seconds": round(finished - keyframes_done, 2), "total_seconds": round(finished - started, 2)},
    )
    progress_cb(97, "Salvando o mapa de câmeras")
    directory = analysis_dir(config, source.project_id)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"camera-map-{input_hash[:16]}.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    artifact = domain.create_stage_artifact(StageArtifact(
        project_id=source.project_id, stage="camera_map", schema_version=CAMERA_MAP_SCHEMA_VERSION,
        path=str(output), input_hash=input_hash,
        metadata={"source_asset_id": source.id, "groups": len(groups), "keyframes": len(times)},
    ))
    progress_cb(100, "Mapa de câmeras concluído")
    return artifact


def thumbnail_file(config: CortexConfig, project_id: str, group: CameraGroup) -> Path | None:
    """Resolved thumbnail path, only when it sits inside the project's camera_map cache."""
    path = Path(group.thumbnail_path)
    resolved = (path if path.is_absolute() else config.paths.data_dir / path).resolve()
    root = (cache_dir(config, project_id) / "camera_map").resolve()
    return resolved if resolved.is_relative_to(root) and resolved.is_file() else None


def read_document(artifact: StageArtifact) -> CameraMapDocument:
    return CameraMapDocument.model_validate_json(Path(artifact.path).read_text(encoding="utf-8"))
