from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.render.service import _measure_visual_quality, _parse_visual_quality


def test_visual_quality_parser_closes_open_freeze_at_eof() -> None:
    stderr = """
    black_start:0.1 black_end:0.8 black_duration:0.7
    lavfi.freezedetect.freeze_start: 1.25
    """

    metrics = _parse_visual_quality(stderr, actual_duration=3.0)

    assert metrics == {
        "black_interval_count": 1,
        "black_total_duration_seconds": 0.7,
        "black_max_duration_seconds": 0.7,
        "freeze_interval_count": 1,
        "freeze_total_duration_seconds": 1.75,
        "freeze_max_duration_seconds": 1.75,
    }


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_visual_quality_detects_black_and_frozen_video(tmp_path: Path) -> None:
    ffmpeg = Path(shutil.which("ffmpeg") or "ffmpeg")
    black = tmp_path / "black.mp4"
    frozen = tmp_path / "frozen.mp4"
    subprocess.run([
        str(ffmpeg), "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=black:size=160x90:rate=30:duration=0.8",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(black),
    ], check=True, timeout=30)
    subprocess.run([
        str(ffmpeg), "-y", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=160x90:rate=30:duration=0.5",
        "-vf", "tpad=stop_mode=clone:stop_duration=1.8",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(frozen),
    ], check=True, timeout=30)

    black_metrics = _measure_visual_quality(
        ffmpeg, black, actual_duration=0.8,
        black_threshold_seconds=0.5, freeze_threshold_seconds=1.5,
    )
    frozen_metrics = _measure_visual_quality(
        ffmpeg, frozen, actual_duration=2.3,
        black_threshold_seconds=0.5, freeze_threshold_seconds=1.5,
    )

    assert black_metrics["black_interval_count"] == 1
    assert black_metrics["black_max_duration_seconds"] >= 0.7
    assert frozen_metrics["freeze_interval_count"] == 1
    assert frozen_metrics["freeze_max_duration_seconds"] >= 1.7
