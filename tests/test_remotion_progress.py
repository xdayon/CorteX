from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from cortex.domain.store import DomainStore
from cortex.render import remotion
from cortex.render.schemas import RenderSettings

from conftest import requires_remotion_e2e
from test_render import _config


def _event(percent=25, **updates):
    return {"type": "cortex.remotion.progress", "schemaVersion": 1, "percent": percent,
            "renderedFrames": 5, "encodedFrames": 0, "totalFrames": 20, **updates}


def _command(script):
    return [sys.executable, "-u", "-c", script]


def test_progress_stream_handles_partial_lines_and_ignores_noise_or_regressions(tmp_path):
    events = [json.dumps(_event(value)) for value in (25, 20, 25, 75)]
    script = f"""
import sys, time
print('Preparing compositor')
for event in {events!r}:
    sys.stdout.write(event[:17]); sys.stdout.flush()
    time.sleep(.02)
    print(event[17:])
"""
    updates = []
    log = tmp_path / "render.log"
    remotion._run_command(_command(script), log, lambda: False,
                          lambda percent, message: updates.append((percent, message)))
    assert [percent for percent, _message in updates] == [25, 75]
    assert updates[0][1] == "Remotion: 5/20 quadros renderizados; 0/20 codificados"
    assert "Preparing compositor" in log.read_text()


def test_progress_callback_arrives_before_renderer_finishes(tmp_path):
    marker = tmp_path / "callback-seen"
    script = f"""
import pathlib, sys, time
marker = pathlib.Path({str(marker)!r})
print({json.dumps(_event())!r})
deadline = time.monotonic() + 2
while not marker.exists() and time.monotonic() < deadline:
    time.sleep(.02)
sys.exit(0 if marker.exists() else 3)
"""
    remotion._run_command(_command(script), tmp_path / "render.log", lambda: False,
                          lambda _percent, _message: marker.touch())
    assert marker.exists()


@pytest.mark.parametrize("event", [
    {}, [], _event(float("nan")), _event(True), _event(101),
    _event(-1), _event(schemaVersion=2), _event(renderedFrames=21),
    _event(encodedFrames="5"), _event(totalFrames=0), _event(totalFrames=True),
    _event(10 ** 1000), _event(schemaVersion=True),
])
def test_invalid_progress_cannot_reach_job_state(event):
    assert remotion._progress_message(json.dumps(event).encode()) is None


def test_large_lines_are_discarded_and_error_log_preserves_bounded_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(remotion, "MAX_REMOTION_LOG_BYTES", 16384)
    event = json.dumps(_event())
    script = f"""
import sys
sys.stdout.write('x' * 100000 + {event!r} + '\\n')
print({event!r})
print('specific-renderer-failure', file=sys.stderr)
sys.exit(2)
"""
    updates = []
    log = tmp_path / "render.log"
    with pytest.raises(remotion.RemotionOverlayError, match="specific-renderer-failure"):
        remotion._run_command(_command(script), log, lambda: False,
                              lambda percent, _message: updates.append(percent))
    assert updates == [25]
    assert log.stat().st_size <= 16384
    assert log.read_text().endswith("specific-renderer-failure\n")


def test_silent_success_does_not_invent_progress(tmp_path):
    updates = []
    remotion._run_command(_command("pass"), tmp_path / "render.log", lambda: False,
                          lambda *update: updates.append(update))
    assert updates == []


@pytest.mark.parametrize("stop", ["cancel", "timeout", "callback_error"])
def test_running_renderer_is_stopped_and_reaped(tmp_path, monkeypatch, stop):
    marker = tmp_path / "pid"
    event = json.dumps(_event())
    script = f"""
import os, pathlib, time
pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
print({event!r})
time.sleep(30)
"""
    if stop == "timeout":
        monkeypatch.setattr(remotion, "REMOTION_TIMEOUT_SECONDS", .4)
    error = {"cancel": remotion.RemotionOverlayCancelled,
             "timeout": remotion.RemotionOverlayError,
             "callback_error": RuntimeError}[stop]

    def callback(_percent, _message):
        if stop == "callback_error":
            raise RuntimeError("job callback failed")

    started = time.monotonic()
    with pytest.raises(error):
        remotion._run_command(_command(script), tmp_path / "render.log",
                              lambda: stop == "cancel" and marker.exists(), callback)
    assert time.monotonic() - started < 5
    assert marker.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


@requires_remotion_e2e
def test_real_remotion_reports_frame_progress_and_persists_cached_overlay(tmp_path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Remotion frame progress")
    settings = RenderSettings.model_validate({
        "encoder": "libx264", "canvas": {"width": 320, "height": 568, "fps": 30},
        "captions": {"enabled": False, "font_family": "Montserrat", "font_size": 28,
                     "words_per_cue": 3, "outline": False},
        "headline": {"enabled": True, "font_family": "Montserrat", "font_size": 28,
                     "text": "Religiões, consciência e ação", "duration_seconds": 1},
        "subtitles": {"sidecar_srt": False},
    })
    updates = []
    service = remotion.RemotionOverlayService(config, domain)
    args = dict(project_id=project.id, output_dir=tmp_path / "overlay", settings=settings,
                duration_seconds_value=1, cues=[], should_cancel=lambda: False,
                progress_cb=lambda percent, message: updates.append((percent, message)))
    artifact, manifest, cached = service.run(**args)
    assert not cached and Path(artifact.path).is_file() and Path(manifest.output_path).is_file()
    assert updates and all("/30 quadros" in message for _percent, message in updates)
    assert any(percent < 100 for percent, _message in updates)
    assert updates[-1][0] == 100
    updates.clear()
    cached_artifact, _manifest, cached = service.run(**args)
    assert cached and cached_artifact.id == artifact.id
    assert updates == [(100, "Legendas e headline em cache reutilizadas")]
