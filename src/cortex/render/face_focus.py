"""Per-scene horizontal focus from detected faces (``framing.focus_mode == "faces"``).

Scene cuts are found with FFmpeg ``scdet`` on the clip window only; faces are
detected on a few frames per scene (YuNet, CPU). The result is one static
horizontal focus per (EDL segment, scene) span: no tracking, no motion.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from cortex.analyze.face_detect import MODEL_PATH, FaceDetectionError, detect_faces, load_session
from cortex.analyze.frames import FRAME_EXTRACTION_WIDTH, _extract_frame_rgb
from cortex.analyze.scene_detect import SCENE_DETECT_FILTER, parse_scene_cuts

FACE_FOCUS_ALGORITHM_VERSION = "1.0.0"

MIN_FACE_SCORE = 0.6
MIN_FACE_HEIGHT = 0.08
FIT_MARGIN = 0.92
MAX_SAMPLES_PER_SCENE = 3
CUT_MARGIN_SECONDS = 0.2
_SPLIT_EPSILON = 1e-3
_SAME_FACE_DISTANCE = 0.08
_SCDET_WIDTH = 320
_SCDET_TIMEOUT_SECONDS = 600


class FaceFocusError(RuntimeError):
    pass


class FaceFocusCancelled(RuntimeError):
    pass


class _Segment(Protocol):
    timeline_order: int
    video_start: float
    video_end: float


class FaceFocusSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_order: int = Field(ge=0)
    scene_index: int = Field(ge=0)
    source_start: float = Field(ge=0)
    source_end: float = Field(ge=0)
    focus_x: float = Field(ge=0.0, le=1.0)
    reason: str
    face_count: int = Field(ge=0)
    sample_times: list[float] = Field(default_factory=list)


def _cache_path(cache_dir: Path, kind: str, payload: dict) -> Path:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return cache_dir / "face_focus" / f"{kind}-{digest}.json"


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def scene_cuts(
    ffmpeg: str | Path, source_path: Path, window_start: float, window_end: float,
    threshold: float, cache_dir: Path, source_sha256: str,
) -> list[float]:
    """Absolute scene-cut times strictly inside the window (scdet on the window only)."""
    window_start = round(max(0.0, window_start), 3)
    duration = round(window_end - window_start, 3)
    if duration <= 0:
        return []
    cache_file = _cache_path(cache_dir, "scdet", {
        "algorithm": FACE_FOCUS_ALGORITHM_VERSION, "source": source_sha256,
        "start": window_start, "end": round(window_end, 3), "threshold": round(threshold, 4),
    })
    cached = _read_json(cache_file)
    if isinstance(cached, list):
        return [float(item) for item in cached]
    command = [
        str(ffmpeg), "-hide_banner", "-nostats", "-v", "info",
        "-ss", f"{window_start:.3f}", "-t", f"{duration:.3f}", "-i", str(source_path),
        "-map", "0:v:0", "-vf", f"scale={_SCDET_WIDTH}:-2,{SCENE_DETECT_FILTER}=threshold={threshold}",
        "-an", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=_SCDET_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FaceFocusError(f"detecção de cenas via ffmpeg falhou: {exc}") from exc
    if result.returncode != 0:
        raise FaceFocusError(
            f"detecção de cenas via ffmpeg falhou ({result.returncode}): {(result.stderr or '')[-1000:]}"
        )
    cuts = sorted({
        round(window_start + time, 3) for time, _score in parse_scene_cuts(result.stderr)
        if _SPLIT_EPSILON < time < duration - _SPLIT_EPSILON
    })
    _write_json(cache_file, cuts)
    return cuts


def split_spans(segments: Sequence[_Segment], cuts: Sequence[float]) -> list[tuple[int, int, float, float]]:
    """Split every EDL segment at the cuts strictly inside it.

    Returns ``(segment_order, scene_index, source_start, source_end)`` in
    timeline order; scene_index counts the scene intervals delimited by ``cuts``.
    """
    ordered_cuts = sorted(cuts)
    spans: list[tuple[int, int, float, float]] = []
    for segment in sorted(segments, key=lambda item: item.timeline_order):
        start, end = float(segment.video_start), float(segment.video_end)
        inner = [cut for cut in ordered_cuts if start + _SPLIT_EPSILON < cut < end - _SPLIT_EPSILON]
        edges = [start, *inner, end]
        for left, right in zip(edges, edges[1:]):
            scene_index = sum(1 for cut in ordered_cuts if cut <= left + _SPLIT_EPSILON)
            spans.append((segment.timeline_order, scene_index, round(left, 6), round(right, 6)))
    return spans


def sample_times(
    scene_spans: Sequence[tuple[float, float]], scene_start: float, scene_end: float,
    *, start_is_cut: bool, end_is_cut: bool,
) -> list[float]:
    """Up to 3 timestamps for one scene, midpoints of its spans first."""
    low = scene_start + (CUT_MARGIN_SECONDS if start_is_cut else 0.0)
    high = scene_end - (CUT_MARGIN_SECONDS if end_is_cut else 0.0)
    if low >= high:
        return [round((scene_start + scene_end) / 2.0, 3)]

    def clamp(value: float) -> float:
        return round(min(max(value, low), high), 3)

    mids = [clamp((start + end) / 2.0) for start, end in scene_spans]
    if len(mids) > MAX_SAMPLES_PER_SCENE:
        last = len(mids) - 1
        picks = sorted({round(i * last / (MAX_SAMPLES_PER_SCENE - 1)) for i in range(MAX_SAMPLES_PER_SCENE)})
        mids = [mids[i] for i in picks]
    times = sorted(set(mids))
    if len(times) < MAX_SAMPLES_PER_SCENE:
        for start, end in sorted(scene_spans, key=lambda item: item[0] - item[1]):
            if end - start < 1.5:
                continue
            for fraction in (0.25, 0.75):
                candidate = clamp(start + (end - start) * fraction)
                if len(times) < MAX_SAMPLES_PER_SCENE and candidate not in times:
                    times.append(candidate)
    return sorted(times)


def _kept(faces: Sequence[dict]) -> list[dict]:
    return [
        face for face in faces
        if face["score"] >= MIN_FACE_SCORE and face["height"] >= MIN_FACE_HEIGHT
    ]


def _cluster(faces: list[dict]) -> list[dict]:
    clusters: list[dict] = []
    for face in sorted(faces, key=lambda item: item["x"] + item["width"] / 2.0):
        center = face["x"] + face["width"] / 2.0
        for cluster in clusters:
            if abs(cluster["mean"] - center) <= _SAME_FACE_DISTANCE:
                cluster["centers"].append(center)
                cluster["areas"].append(face["width"] * face["height"])
                cluster["mean"] = sum(cluster["centers"]) / len(cluster["centers"])
                break
        else:
            clusters.append({
                "centers": [center], "areas": [face["width"] * face["height"]], "mean": center,
            })
    for cluster in clusters:
        cluster["area"] = sum(cluster["areas"]) / len(cluster["areas"])
    return clusters


def count_faces(faces_per_sample: Sequence[Sequence[dict]]) -> int:
    return len(_cluster([face for sample in faces_per_sample for face in _kept(sample)]))


def decide_focus(
    faces_per_sample: Sequence[Sequence[dict]], *, crop_width_fraction: float,
    manual_focus_x: float, primary_side: str | None,
) -> tuple[float, str]:
    """Return ``(focus_x, reason)`` for one scene."""
    half = min(crop_width_fraction, 1.0) / 2.0

    def clamp(value: float) -> float:
        return round(min(max(value, half), 1.0 - half), 6)

    kept = [face for sample in faces_per_sample for face in _kept(sample)]
    if not kept:
        return clamp(manual_focus_x), "no_faces"
    left = min(face["x"] for face in kept)
    right = max(face["x"] + face["width"] for face in kept)
    if right - left <= FIT_MARGIN * crop_width_fraction:
        return clamp((left + right) / 2.0), "all_faces"
    clusters = _cluster(kept)
    if primary_side in ("left", "right"):
        chosen = (min if primary_side == "left" else max)(clusters, key=lambda item: item["mean"])
        return clamp(chosen["mean"]), "primary_side"
    chosen = max(clusters, key=lambda item: item["area"])
    return clamp(chosen["mean"]), "largest_face"


@lru_cache(maxsize=4)
def _model_sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _detect_cached(
    session_factory: Callable[[], Any], ffmpeg: str | Path, source_path: Path,
    timestamp: float, cache_dir: Path, source_sha256: str,
) -> list[dict]:
    timestamp = round(timestamp, 3)
    cache_file = _cache_path(cache_dir, "faces", {
        "algorithm": FACE_FOCUS_ALGORITHM_VERSION, "source": source_sha256,
        "timestamp": timestamp, "width": FRAME_EXTRACTION_WIDTH,
        "model": _model_sha256(str(MODEL_PATH)),
    })
    cached = _read_json(cache_file)
    if isinstance(cached, list):
        return cached
    frame = _extract_frame_rgb(ffmpeg, source_path, timestamp)
    faces = [
        {key: float(face[key]) for key in ("x", "y", "width", "height", "score")}
        for face in detect_faces(session_factory(), frame)
    ]
    _write_json(cache_file, faces)
    return faces


def resolve_face_focus(
    *, ffmpeg: str | Path, source_path: Path, source_sha256: str,
    segments: Sequence[_Segment], scene_threshold: float, cache_dir: Path,
    crop_width_fraction: float, manual_focus_x: float, primary_side: str | None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[FaceFocusSpan]:
    def check() -> None:
        if should_cancel is not None and should_cancel():
            raise FaceFocusCancelled()

    if not segments:
        return []
    window_start = min(float(segment.video_start) for segment in segments)
    window_end = max(float(segment.video_end) for segment in segments)
    check()
    cuts = scene_cuts(
        ffmpeg, source_path, window_start, window_end, scene_threshold, cache_dir, source_sha256,
    )
    spans = split_spans(segments, cuts)
    boundaries = [window_start, *cuts, window_end]
    by_scene: dict[int, list[tuple[float, float]]] = {}
    for _order, scene_index, start, end in spans:
        by_scene.setdefault(scene_index, []).append((start, end))

    session_holder: list[Any] = []

    def session() -> Any:
        if not session_holder:
            try:
                session_holder.append(load_session())
            except FaceDetectionError as exc:
                raise FaceFocusError(str(exc)) from exc
        return session_holder[0]

    decisions: dict[int, tuple[float, str, int, list[float]]] = {}
    for scene_index, scene_spans in sorted(by_scene.items()):
        check()
        times = sample_times(
            scene_spans, boundaries[scene_index], boundaries[scene_index + 1],
            start_is_cut=scene_index > 0, end_is_cut=scene_index < len(boundaries) - 2,
        )
        faces_per_sample = []
        for timestamp in times:
            check()
            try:
                faces_per_sample.append(_detect_cached(
                    session, ffmpeg, source_path, timestamp, cache_dir, source_sha256,
                ))
            except FaceDetectionError as exc:
                raise FaceFocusError(str(exc)) from exc
        focus, reason = decide_focus(
            faces_per_sample, crop_width_fraction=crop_width_fraction,
            manual_focus_x=manual_focus_x, primary_side=primary_side,
        )
        decisions[scene_index] = (focus, reason, count_faces(faces_per_sample), times)
    return [
        FaceFocusSpan(
            segment_order=order, scene_index=scene_index, source_start=start, source_end=end,
            focus_x=decisions[scene_index][0], reason=decisions[scene_index][1],
            face_count=decisions[scene_index][2], sample_times=decisions[scene_index][3],
        )
        for order, scene_index, start, end in spans
    ]
