from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.render import quick_preview
from cortex.render.quick_preview import build_quick_preview, quick_preview_key

from test_clips_api import KEYS, P, _world
from test_export_batches import _config

FPS = 30
PIECES = [(1.0, 3.34), (5.1, 7.77), (9.0, 10.55)]

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg indisponível",
)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30000/1001:duration=14",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=14",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(source),
    ], check=True, timeout=120)
    return source


def _probe(path: Path) -> dict:
    probe = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-count_frames",
        "-show_entries", "stream=codec_type,codec_name,width,height,duration,nb_read_frames",
        "-of", "json", str(path),
    ], capture_output=True, text=True, check=True).stdout)
    return {stream["codec_type"]: stream for stream in probe["streams"]}


def test_three_pieces_render_vertical_in_sync_and_cache(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    source = _source(tmp_path)
    out_dir = tmp_path / "quick"
    output = build_quick_preview(config, source, "a" * 64, PIECES, out_dir)
    streams = _probe(output)
    total = sum(end - start for start, end in PIECES)
    assert (streams["video"]["codec_name"], streams["video"]["width"], streams["video"]["height"]) \
        == ("h264", 360, 640)
    frames = int(streams["video"]["nb_read_frames"])
    assert frames == round(total * FPS)
    assert abs(frames / FPS - float(streams["audio"]["duration"])) < 1 / FPS

    mtime = output.stat().st_mtime_ns
    calls: list[list[str]] = []
    real_run = subprocess.run
    monkeypatch.setattr(
        quick_preview.subprocess, "run",
        lambda command, *a, **k: calls.append(command) or real_run(command, *a, **k),
    )
    again = build_quick_preview(config, source, "a" * 64, PIECES, out_dir)
    assert again == output and again.stat().st_mtime_ns == mtime and calls == []
    assert quick_preview_key("a" * 64, PIECES) != quick_preview_key("b" * 64, PIECES)
    assert list(out_dir.glob(".*tmp*")) == []


def test_endpoint_rejects_unknown_clip_run_and_missing_source(tmp_path: Path) -> None:
    client, _, project, run, *_ = _world(tmp_path)
    base = f"{P}/projects/{project.id}/runs/{run.id}/quick-preview"
    assert client.get(base, params={"clip_key": "9:1:2:fast"}).status_code == 404
    assert client.get(
        f"{P}/projects/{project.id}/runs/nope/quick-preview", params={"clip_key": KEYS[0]},
    ).status_code == 404
    assert client.get(base, params={"clip_key": KEYS[0]}).status_code == 410
