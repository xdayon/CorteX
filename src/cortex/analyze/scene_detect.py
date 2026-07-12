"""Pure helpers around FFmpeg's ``scdet`` filter (shot/scene-cut detection).

Zero new Python dependencies: no PySceneDetect, no OpenCV. FFmpeg's scdet
filter is a decoder-side scene-change detector that already ships with the
system FFmpeg (see docs/HANDOFF_FABLE_5.md, 2026-07-12). scdet logs its
detections to stderr as INFO lines when a frame's score crosses the
threshold — the same "parse stderr with -v info" pattern already used by
blackdetect/freezedetect in cortex.render.service.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

SCENE_DETECT_FILTER = "scdet"

_SCENE_CUT_RE = re.compile(
    r"lavfi\.scd\.score:\s*(-?\d+(?:\.\d+)?),\s*lavfi\.scd\.time:\s*(-?\d+(?:\.\d+)?)"
)
_FFMPEG_VERSION_RE = re.compile(r"ffmpeg version (\S+)")


class SceneDetectionError(RuntimeError):
    pass


def ffmpeg_version(ffmpeg: str | Path) -> str:
    try:
        result = subprocess.run(
            [str(ffmpeg), "-version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SceneDetectionError(f"ffmpeg -version não pôde ser executado: {exc}") from exc
    if result.returncode != 0:
        raise SceneDetectionError(
            f"ffmpeg -version falhou ({result.returncode}): {(result.stderr or '')[-500:]}"
        )
    match = _FFMPEG_VERSION_RE.search(result.stdout or "")
    if not match:
        raise SceneDetectionError("não foi possível determinar a versão do ffmpeg")
    return match.group(1)


def scdet_command(ffmpeg: str | Path, media_path: Path, threshold: float) -> list[str]:
    return [
        str(ffmpeg), "-hide_banner", "-nostats", "-v", "info", "-i", str(media_path),
        "-map", "0:v:0", "-vf", f"{SCENE_DETECT_FILTER}=threshold={threshold}",
        "-an", "-f", "null", "-",
    ]


def parse_scene_cuts(stderr: str) -> list[tuple[float, float]]:
    """Parse scdet's ``lavfi.scd.score/lavfi.scd.time`` stderr lines.

    Returns ``(time, score)`` pairs sorted by time, deduped by time (a
    duplicate frame log at the same timestamp keeps its latest score).
    """
    matches = _SCENE_CUT_RE.findall(stderr or "")
    by_time: dict[float, float] = {}
    for score, time in matches:
        by_time[round(float(time), 3)] = round(float(score), 3)
    return [(time, score) for time, score in sorted(by_time.items())]


def build_scenes(cuts: list[tuple[float, float]], duration: float) -> list[dict]:
    """Turn cut timestamps into contiguous [start, end) scene segments."""
    duration = round(max(0.0, duration), 3)
    boundaries = sorted({0.0, *(t for t, _ in cuts if 0.0 < t < duration), duration})
    if len(boundaries) < 2:
        boundaries = [0.0, duration]
    return [
        {"index": index, "start": boundaries[index], "end": boundaries[index + 1]}
        for index in range(len(boundaries) - 1)
    ]
