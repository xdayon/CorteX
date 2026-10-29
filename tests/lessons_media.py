"""Synthetic lesson fixtures (ffmpeg lavfi only) shared by the lessons tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

from cortex.config import CortexConfig, LessonsConfig


def ff(*args) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True, timeout=60)


def small_config(tmp_path: Path, **overrides) -> CortexConfig:
    lessons = dict(dir=tmp_path, ingest_dir=tmp_path / "ingest", width=320, height=180, fps=15,
                   flow_durations=[4, 6, 8, 10], xfade_seconds=0.4)
    lessons.update(overrides)
    return CortexConfig(lessons=LessonsConfig(**lessons))
