"""Real pixels distinguish zooming content from enlarging its fitted window."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.domain.store import DomainStore
from cortex.edit.schemas import EditSegment
from cortex.ingest.ffprobe import probe_media
from cortex.render.schemas import RenderDocument
from cortex.render.service import RenderService

from test_render_punch_in import (
    _config, _make_plan_artifact, _make_transcript, _pixel_at, _settings, _stripe_source,
)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="real ffmpeg/ffprobe required")
def test_blurred_zoom_keeps_video_window_and_audio_clocks_fixed(tmp_path: Path):
    config = _config(tmp_path)
    config.render.visual_quality_enabled = False  # Deliberately static color fixture.
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Fixed blurred video window")
    source = _stripe_source(config, domain, tmp_path, project.id, duration=1)
    transcript = _make_transcript(domain, tmp_path, project.id, source.id, duration=1)
    plan = _make_plan_artifact(
        domain, tmp_path, project.id, source.id, transcript.id,
        segments=[EditSegment(start=0, end=1, timeline_order=0)], duration=1,
    )
    plan_before = Path(plan.path).read_bytes()
    documents = []
    for enabled in (False, True):
        settings = _settings(scale=1.15, alternate=False)
        settings.canvas.height = 568
        settings.framing.mode = "blurred_background"
        settings.framing.punch_in.enabled = enabled
        result = RenderService(config, domain).run(
            edit_plan_artifact=plan, encoder=None, headline=None, render_settings=settings,
            progress_cb=lambda *_: None, should_cancel=lambda: False,
        )
        artifact = domain.get_stage_artifact(result["render_artifact_id"])
        documents.append(RenderDocument.model_validate_json(Path(artifact.path).read_text()))

    baseline, zoomed = documents
    # A square source fitted to 320x568 occupies y=[124,444). Outside this
    # window the red stripe remains visible only as diffuse background color.
    # The previous implementation enlarged the window to roughly [100,468),
    # replacing these outside pixels with solid blue foreground.
    for y in (116, 452):
        plain = _pixel_at(config.render.ffmpeg, Path(baseline.output_path), .5, 8, y)
        zoom = _pixel_at(config.render.ffmpeg, Path(zoomed.output_path), .5, 8, y)
        assert plain[0] > 25 and zoom[0] > 25, (y, plain, zoom)
        assert max(abs(a - b) for a, b in zip(plain, zoom)) < 12, (y, plain, zoom)

    # Just inside both edges, and in the center, the foreground remains in
    # place while the 1.15x source crop removes its leftmost 16-pixel stripe.
    for y in (132, 284, 436):
        plain = _pixel_at(config.render.ffmpeg, Path(baseline.output_path), .5, 8, y)
        zoom = _pixel_at(config.render.ffmpeg, Path(zoomed.output_path), .5, 8, y)
        assert plain[0] > plain[2] + 150, (y, plain)
        assert zoom[2] > zoom[0] + 150 and zoom[0] < 20, (y, zoom)

    clocks = []
    audio_hashes = []
    for document in documents:
        assert document.timeline_duration_seconds == 1
        probe = probe_media(config.render.ffprobe, Path(document.output_path))
        video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
        assert (int(video["width"]), int(video["height"])) == (320, 568)
        clocks.append({stream["codec_type"]: (stream.get("start_time"), stream.get("duration"))
                       for stream in probe["streams"]})
        audio_hashes.append(subprocess.run([
            str(config.render.ffmpeg), "-v", "error", "-i", document.output_path,
            "-map", "0:a:0", "-f", "hash", "-",
        ], capture_output=True, check=True, timeout=15).stdout)
    assert clocks[0] == clocks[1]
    assert audio_hashes[0] == audio_hashes[1]
    assert Path(plan.path).read_bytes() == plan_before
    assert zoomed.punch_ins[0].applied
    assert zoomed.punch_ins[0].effective_scale == pytest.approx(1.15)
