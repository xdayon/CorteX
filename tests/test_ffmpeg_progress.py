from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cortex.render.service import RenderExecutionError, RenderJobCancelled, _run_ffmpeg


def _program(tmp_path: Path, source: str) -> list[str]:
    script = tmp_path / "fake-ffmpeg"
    script.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    script.chmod(0o700)
    return [str(script)]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="real ffmpeg/ffprobe required")
def test_real_ffmpeg_streams_elapsed_video_progress_and_persists_two_seconds(tmp_path):
    output = tmp_path / "clip.mp4"
    command = [shutil.which("ffmpeg"), "-y", "-v", "error", "-re", "-f", "lavfi",
               "-i", "testsrc2=size=160x90:rate=30:duration=2", "-an", "-c:v", "libx264",
               "-preset", "ultrafast", "-tune", "zerolatency", str(output)]
    updates = []
    started = time.monotonic()
    _run_ffmpeg(command, tmp_path / "encode.log", lambda: False, duration_seconds_value=2,
                progress_cb=lambda percent, message: updates.append(
                    (percent, message, time.monotonic() - started)), timeout_seconds=15)
    assert output.is_file() and output.stat().st_size > 0
    probe = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries",
                            "format=duration", "-of", "json", str(output)],
                           text=True, capture_output=True, check=True, timeout=10)
    assert float(json.loads(probe.stdout)["format"]["duration"]) == pytest.approx(2, abs=.05)
    assert len(updates) >= 3
    assert 0 <= updates[0][0] < 100 and 90 <= updates[-1][0] <= 100
    assert all(left[0] < right[0] for left, right in zip(updates, updates[1:]))
    assert updates[0][2] < updates[-1][2]
    assert all("/2.0 s de vídeo codificados" in message for _percent, message, _at in updates)
    assert "-progress" not in command  # caller's command remains reusable


def test_partial_lines_report_only_valid_monotonic_output_clocks(tmp_path):
    marker = tmp_path / "live-progress"
    source = f"""
import pathlib, sys, time
assert sys.argv[1:7] == ['-nostdin', '-nostats', '-stats_period', '0.5', '-progress', 'pipe:1']
sys.stdout.write('out_time_us=500'); sys.stdout.flush(); time.sleep(.03)
print('000\\nprogress=continue', flush=True)
marker = pathlib.Path({str(marker)!r})
deadline = time.monotonic() + 2
while not marker.exists() and time.monotonic() < deadline: time.sleep(.01)
assert marker.exists(), 'callback was delayed until process exit'
for value in ['250000', 'N/A', '-1', 'nan', '9' * 10000, '1500000']:
    print('out_time_us=' + value + '\\nprogress=continue', flush=True)
print('out_time_us=2000000\\nprogress=end', file=sys.stderr, flush=True)
"""
    updates = []

    def progress(percent, message):
        updates.append((percent, message))
        marker.touch()

    _run_ffmpeg(_program(tmp_path, source), tmp_path / "encode.log", lambda: False,
                progress_cb=progress, duration_seconds_value=2)
    assert [percent for percent, _message in updates] == [25, 75]


def test_progress_end_does_not_invent_an_unreported_clock(tmp_path):
    updates = []
    _run_ffmpeg(_program(tmp_path, "print('progress=end')"), tmp_path / "encode.log",
                lambda: False, progress_cb=lambda *update: updates.append(update),
                duration_seconds_value=2)
    assert updates == []


def test_failure_keeps_bounded_diagnostic_tail_and_legacy_signature(tmp_path):
    source = "import sys\nsys.stderr.write('x' * 2100000 + '\\nreal encoder failure\\n')\nsys.exit(4)"
    log = tmp_path / "encode.log"
    with pytest.raises(RenderExecutionError, match="real encoder failure"):
        _run_ffmpeg(_program(tmp_path, source), log, lambda: False)
    assert log.stat().st_size <= 1024 * 1024
    assert log.read_text().endswith("real encoder failure\n")


@pytest.mark.parametrize("stop", ["cancel", "timeout", "callback_error"])
def test_process_is_reaped_when_cancelled_timed_out_or_callback_fails(tmp_path, stop):
    marker = tmp_path / "pid"
    source = f"""
import os, pathlib, time
pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
print('out_time_us=1000000\\nprogress=continue', flush=True)
time.sleep(30)
"""
    expected_error = {"cancel": RenderJobCancelled, "timeout": RenderExecutionError,
                      "callback_error": RuntimeError}[stop]

    def progress(_percent, _message):
        if stop == "callback_error":
            raise RuntimeError("failed to persist job progress")

    started = time.monotonic()
    with pytest.raises(expected_error):
        _run_ffmpeg(_program(tmp_path, source), tmp_path / "encode.log",
                    lambda: stop == "cancel" and marker.exists(), progress_cb=progress,
                    duration_seconds_value=2, timeout_seconds=.3 if stop == "timeout" else 5)
    assert time.monotonic() - started < 5
    assert marker.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
