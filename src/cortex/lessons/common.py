from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from cortex.config import CortexConfig

FFMPEG_TIMEOUT = 3600


def video_encode(cfg: CortexConfig, final: bool = False) -> list[object]:
    """Encoder arguments for a lesson stage, from `lessons.encoder` (requested = effective, no fallback).

    Pieces (`final=False`) are near-lossless intermediates; the final pass carries the film grain. Measured on a
    lesson (60 s, 1080p, GTX 1060): NVENC 4.5–4.8x faster than x264 at the same SSIM (0.959).
    """
    if cfg.lessons.encoder == "h264_nvenc":
        from cortex.render.service import nvenc_unavailable_reason  # heavy module: only when NVENC is asked for

        reason = nvenc_unavailable_reason(cfg.render.ffmpeg)
        if reason:
            raise LessonError(f"lessons.encoder is h264_nvenc but {cfg.render.ffmpeg} cannot open it: {reason}")
        args: list[object] = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                              "-cq", 19 if final else 14, "-b:v", 0, "-spatial-aq", 1]
    elif final:
        args = ["-c:v", "libx264", "-preset", "slow", "-tune", "grain", "-crf", 19]
    else:
        args = ["-c:v", "libx264", "-preset", "fast", "-crf", 14]
    return [*args, "-pix_fmt", "yuv420p"]


def canvas(cfg: CortexConfig, aspect: str) -> tuple[int, int]:
    """(width, height) of the export: the configured frame, turned upright for 9:16."""
    w, h = cfg.lessons.width, cfg.lessons.height
    long, short = max(w, h), min(w, h)
    return (short, long) if aspect == "9:16" else (long, short)


class LessonError(RuntimeError):
    """A lesson problem the author has to fix (bad spec, missing media, mismatched duration)."""


def run(cmd: Sequence[object], *, timeout: float = FFMPEG_TIMEOUT, capture: bool = False) -> subprocess.CompletedProcess:
    argv = [str(c) for c in cmd]
    result = subprocess.run(argv, capture_output=capture, text=True, timeout=timeout)
    if result.returncode != 0:
        tail = (result.stderr or "")[-600:] if capture else ""
        raise LessonError(f"command failed ({result.returncode}): {' '.join(argv[:6])} ... {tail}")
    return result


def ffmpeg(cfg: CortexConfig, *args: object, timeout: float = FFMPEG_TIMEOUT) -> None:
    run([cfg.render.ffmpeg, "-v", "error", "-y", *args], timeout=timeout)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_parts(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def cache_valid(out: Path, key: str, force: bool = False) -> bool:
    sidecar = out.with_name(out.name + ".hash")
    if force or not out.exists() or not sidecar.exists():
        return False
    return sidecar.read_text().strip() == key


def cache_store(out: Path, key: str) -> None:
    out.with_name(out.name + ".hash").write_text(key + "\n")


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float


def probe(cfg: CortexConfig, path: Path) -> MediaInfo:
    out = run([cfg.render.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
              capture=True, timeout=60).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    duration = float(data["format"]["duration"])
    if video is None:
        return MediaInfo(duration, 0, 0, 0.0)
    num, _, den = video.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return MediaInfo(duration, int(video["width"]), int(video["height"]), fps)
