"""Sound analysis with ffprobe/ffmpeg: duration, loudest momentary LUFS, time of the energy peak."""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

import numpy as np

from cortex.config import CortexConfig
from cortex.sound.http import SoundError

RATE = 48000
WINDOW = RATE // 50
MOMENTARY = re.compile(r"\bM:\s*(-?\d+(?:\.\d+)?)")


def _run(argv: list, timeout: float = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([str(a) for a in argv], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SoundError(f"timeout running {Path(str(argv[0])).name}") from None


def probe_duration(cfg: CortexConfig, path: Path) -> float:
    res = _run([cfg.render.ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", path], 30)
    try:
        return float(res.stdout.decode().strip())
    except ValueError:
        raise SoundError(f"{path.name}: not a readable audio file") from None


def analyse(cfg: CortexConfig, path: Path) -> dict:
    duration = probe_duration(cfg, path)
    res = _run([cfg.render.ffmpeg, "-v", "verbose", "-nostats", "-i", path, "-af", "ebur128=framelog=verbose",
                "-f", "null", "-"])
    values = [float(v) for v in MOMENTARY.findall(res.stderr.decode("utf-8", "replace"))]
    values = [v for v in values if math.isfinite(v) and v > -120]
    pcm = _run([cfg.render.ffmpeg, "-v", "error", "-i", path, "-ac", "1", "-ar", RATE,
                "-f", "f32le", "-"]).stdout
    samples = np.frombuffer(pcm[: len(pcm) // 4 * 4], dtype="<f4")
    peak_time = 0.0
    frames = len(samples) // WINDOW
    if frames:
        env = np.sqrt((samples[: frames * WINDOW].reshape(frames, WINDOW) ** 2).mean(axis=1))
        peak_time = round(float(env.argmax()) * WINDOW / RATE, 3)
    return {"duration": duration, "max_momentary_lufs": max(values) if values else None,
            "peak_time": peak_time}
