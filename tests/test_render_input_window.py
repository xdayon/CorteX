"""Real short renders prove seeks preserve source selection and independent A/V clocks."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import cortex.render.service as render_service
from cortex.domain.models import StageArtifact
from cortex.edit.schemas import EditPlanDocument
from cortex.render.schemas import RenderDocument
from cortex.render.service import RenderService
from test_render import _center_pixel, _config, _dominant_audio_frequency
from test_render_jl_cut import _build_plan, _prepare_fixture, _render_settings

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg and ffprobe required"
)


def _persist_plan(tmp_path, domain, plan):
    path = tmp_path / "edit.json"
    path.write_text(plan.model_dump_json(), encoding="utf-8")
    return domain.create_stage_artifact(StageArtifact(
        project_id=plan.project_id, stage="edit_plan", path=str(path), input_hash=plan.input_hash,
    ))


def _capture_render(monkeypatch):
    commands = []
    original = render_service._run_ffmpeg

    def run(command, log_path, should_cancel, **kwargs):
        if "-filter_complex" in command:
            commands.append(command)
        return original(command, log_path, should_cancel, **kwargs)

    monkeypatch.setattr(render_service, "_run_ffmpeg", run)
    return commands


def _assert_window(command, start, end):
    input_position = command.index("-i")
    assert command.index("-ss") < input_position
    assert command.index("-t") < input_position
    assert float(command[command.index("-ss") + 1]) == pytest.approx(start)
    assert float(command[command.index("-t") + 1]) == pytest.approx(end - start)


@pytest.mark.parametrize("kind,offset", [("hard", 0.0), ("j_cut", -0.3), ("l_cut", 0.3)])
def test_late_source_seek_preserves_video_audio_and_cache(tmp_path, monkeypatch, kind, offset):
    config = _config(tmp_path)
    config.render.visual_quality_enabled = False  # Solid colors are deliberate temporal markers.
    config.ensure_runtime_dirs()
    domain, source, transcript = _prepare_fixture(
        tmp_path, config, color_switch=11, audio_switch=11 + offset, total=14,
    )
    plan = _build_plan(project_id=source.project_id, source_id=source.id, transcript_id=transcript.id,
                       offset_seconds=offset, kind=kind, video_cut_seconds=1, total_seconds=2)
    payload = plan.model_dump()
    payload.update(clip_start=10, clip_end=12)
    for segment in payload["segments"]:
        for clock in ("start", "end", "video_start", "video_end", "audio_start", "audio_end"):
            segment[clock] += 10
    plan = EditPlanDocument.model_validate(payload)
    edit = _persist_plan(tmp_path, domain, plan)
    commands = _capture_render(monkeypatch)
    args = dict(edit_plan_artifact=edit, encoder=None, headline=None,
                progress_cb=lambda *_: None, should_cancel=lambda: False, render_settings=_render_settings())
    result = RenderService(config, domain).run(**args)
    doc = RenderDocument.model_validate_json(Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text())
    assert doc.quality.passed, doc.quality.issues
    assert doc.quality.actual_duration_seconds == pytest.approx(2, abs=.04)
    assert len(commands) == 1
    _assert_window(commands[0], 10, 12)
    output = Path(doc.output_path)
    assert _center_pixel(config.render.ffmpeg, output, .5)[0] > 200
    assert _center_pixel(config.render.ffmpeg, output, 1.5)[2] > 200
    # Sample either side of the declared audio change, including the A/V divergence.
    for index, (timestamp, expected) in enumerate(((.2, 440), (1 + offset + .05, 1200))):
        sample = tmp_path / f"tone-{index}.wav"
        subprocess.run([str(config.render.ffmpeg), "-y", "-v", "error", "-ss", str(timestamp),
                        "-i", str(output), "-t", "0.15", "-map", "0:a:0", str(sample)],
                       check=True, timeout=30)
        assert _dominant_audio_frequency(config.render.ffmpeg, sample) == pytest.approx(expected, abs=20)
    assert doc.transitions[0].effective_kind == kind
    assert doc.publication.provenance["input_seek_seconds"] == "10.000000"
    assert Path(edit.path).read_text() == plan.model_dump_json()
    again = RenderService(config, domain).run(**args)
    assert again["cached"] and again["render_artifact_id"] == result["render_artifact_id"]
    assert len(commands) == 1
