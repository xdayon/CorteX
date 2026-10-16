"""Cheap vertical H.264 preview of a suggested clip, for review before export.

No captions, no face analysis, no LLM: the clip's pieces concatenated on an exact
frame grid (same tpad/trim technique as the export filtergraph) in a 9:16
blurred-background frame. Browsers that cannot decode VP9/AV1 sources still play it.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path

from cortex.config import CortexConfig
from cortex.render.service import _blurred_geometry

QUICK_PREVIEW_VERSION = 1
CANVAS_WIDTH = 360
CANVAS_HEIGHT = 640
FPS = 30
# Mirrors the Studio's default framing (apps/web/src/editor/settings.ts).
FRAMING_ZOOM = 1.45
FRAMING_FOCUS_X = 0.5
FRAMING_POSITION_Y = 0.37
FFMPEG_TIMEOUT_SECONDS = 120
_SEEK_MARGIN_SECONDS = 0.2

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


class QuickPreviewError(RuntimeError):
    pass


def quick_preview_key(source_sha256: str, pieces: list[tuple[float, float]]) -> str:
    payload = json.dumps({
        "version": QUICK_PREVIEW_VERSION,
        "source": source_sha256,
        "pieces": [[round(start * 1000), round(end * 1000)] for start, end in pieces],
        "framing": [FRAMING_ZOOM, FRAMING_FOCUS_X, FRAMING_POSITION_Y],
        "canvas": [CANVAS_WIDTH, CANVAS_HEIGHT, FPS],
    }, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _source_size(config: CortexConfig, source_path: Path) -> tuple[int, int]:
    try:
        completed = subprocess.run(
            [str(config.render.ffprobe), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(source_path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        stream = json.loads(completed.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError) as exc:
        raise QuickPreviewError("Não foi possível ler as dimensões da fonte") from exc


def _filtergraph(pieces: list[tuple[float, float]], source_size: tuple[int, int]) -> str:
    geometry = _blurred_geometry(
        *source_size, CANVAS_WIDTH, CANVAS_HEIGHT, FRAMING_ZOOM, FRAMING_FOCUS_X,
    )
    blur_w, blur_h = CANVAS_WIDTH // 4 // 2 * 2, CANVAS_HEIGHT // 4 // 2 * 2
    foreground_y = f"if(gte(H,h),(H-h)*{FRAMING_POSITION_Y:.6f},(H-h)/2)"
    video: list[str] = []
    audio: list[str] = []
    elapsed = 0.0
    emitted = 0
    for index, (start, end) in enumerate(pieces):
        duration = end - start
        elapsed += duration
        frames = max(1, round(elapsed * FPS) - emitted)
        emitted += frames
        video.append(
            f"[{index}:v]fps={FPS},split=2[bgraw{index}][fgraw{index}];"
            f"[bgraw{index}]scale={blur_w}:{blur_h}:force_original_aspect_ratio=increase:flags=fast_bilinear,"
            f"crop={blur_w}:{blur_h},gblur=sigma=6:steps=2,"
            f"scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}:flags=fast_bilinear,setsar=1[bg{index}];"
            f"[fgraw{index}]crop={geometry.w}:{geometry.h}:{geometry.x}:{geometry.y},"
            f"scale={geometry.window_w}:{geometry.window_h}:flags=fast_bilinear,setsar=1[fg{index}];"
            f"[bg{index}][fg{index}]overlay=x=(W-w)/2:y='{foreground_y}':format=yuv420,"
            f"format=yuv420p,tpad=stop_mode=clone:stop_duration=1,"
            f"trim=end_frame={frames},setpts=PTS-STARTPTS[v{index}]"
        )
        audio.append(
            f"[{index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"apad=whole_dur={duration:.6f},atrim=end={duration:.6f},asetpts=PTS-STARTPTS[a{index}]"
        )
    count = len(pieces)
    joined = "".join(f"[v{i}]" for i in range(count))
    joined_audio = "".join(f"[a{i}]" for i in range(count))
    tail = [
        f"{joined}concat=n={count}:v=1:a=0[vout]",
        f"{joined_audio}concat=n={count}:v=0:a=1[aout]",
    ]
    return ";".join(video + audio + tail)


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def build_quick_preview(
    config: CortexConfig,
    source_path: Path,
    source_sha256: str,
    pieces: list[tuple[float, float]],
    output_dir: Path,
) -> Path:
    """Render (or reuse) the vertical preview and return its path."""
    pieces = [(float(start), float(end)) for start, end in pieces]
    if not pieces or any(start < 0 or end - start < 0.05 for start, end in pieces):
        raise QuickPreviewError("Trechos do corte inválidos")
    key = quick_preview_key(source_sha256, pieces)
    output = output_dir / f"{key}.mp4"
    with _lock_for(key):
        if output.is_file():
            return output
        output_dir.mkdir(parents=True, exist_ok=True)
        graph = _filtergraph(pieces, _source_size(config, source_path))
        command = [str(config.render.ffmpeg), "-y", "-v", "error", "-nostdin"]
        for start, end in pieces:
            command += [
                "-ss", f"{start:.6f}", "-t", f"{end - start + _SEEK_MARGIN_SECONDS:.6f}",
                "-i", str(source_path),
            ]
        temporary = output.with_name(f".{key}.tmp.mp4")
        command += [
            "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-profile:v", "main", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(temporary),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True,
                timeout=FFMPEG_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            temporary.unlink(missing_ok=True)
            raise QuickPreviewError("Tempo esgotado ao gerar a prévia vertical") from exc
        except OSError as exc:
            raise QuickPreviewError("FFmpeg indisponível para gerar a prévia vertical") from exc
        if completed.returncode != 0 or not temporary.is_file():
            temporary.unlink(missing_ok=True)
            detail = (completed.stderr or "").strip().splitlines()[-1:] or ["erro desconhecido"]
            raise QuickPreviewError(f"Falha ao gerar a prévia vertical: {detail[0][:200]}")
        temporary.replace(output)
    return output
