from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class FFprobeError(RuntimeError):
    pass


def probe_media(ffprobe: str | Path, media_path: str | Path) -> dict[str, Any]:
    """Run ffprobe on `media_path` and return the parsed JSON as a dict.

    Raises FFprobeError if ffprobe exits non-zero or the output cannot be
    parsed as JSON.
    """
    command = [
        str(ffprobe),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(media_path),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FFprobeError(f"ffprobe não pôde ser executado: {exc}") from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()[-2000:]
        raise FFprobeError(f"ffprobe falhou ({completed.returncode}): {stderr}")
    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise FFprobeError(f"saída do ffprobe não é JSON válido: {exc}") from exc
    if not data.get("format") and not data.get("streams"):
        raise FFprobeError("ffprobe não retornou format/streams")
    return data


def duration_seconds(probe: dict[str, Any]) -> float:
    format_duration = (probe.get("format") or {}).get("duration")
    if format_duration is not None:
        return float(format_duration)
    for stream in probe.get("streams") or []:
        if stream.get("duration") is not None:
            return float(stream["duration"])
    return 0.0
