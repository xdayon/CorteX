"""Real short renders prove seeks preserve source selection and independent A/V clocks."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import cortex.render.service as render_service
from cortex.domain.models import StageArtifact
from cortex.edit.camera_plan_schemas import CameraEditPlanDocument
from cortex.edit.schemas import EditPlanDocument, EditSegment
from cortex.render.auto_framing import AutoFramingSpan
from cortex.render.schemas import RenderDocument
from cortex.render.service import RenderService, _filtergraph, _source_input_window
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


@pytest.mark.parametrize("audio_start,video_start,switch,expected_color,expected_audio", [
    (10, 12, 12, "blue", 440), (12, 8, 10, "red", 1200),
])
def test_late_reaction_seek_includes_borrowed_video_and_keeps_primary_audio(
    tmp_path, monkeypatch, audio_start, video_start, switch, expected_color, expected_audio,
):
    config = _config(tmp_path)
    config.render.visual_quality_enabled = False
    config.ensure_runtime_dirs()
    domain, source, transcript = _prepare_fixture(
        tmp_path, config, color_switch=switch, audio_switch=switch, total=14,
    )
    template = _build_plan(project_id=source.project_id, source_id=source.id, transcript_id=transcript.id,
                           offset_seconds=0, kind="hard", video_cut_seconds=1, total_seconds=2)
    plan = template.model_copy(update={
        "clip_start": audio_start, "clip_end": audio_start + 1, "timeline_duration_seconds": 1,
        "segments": [EditSegment(start=audio_start, end=audio_start + 1, timeline_order=0)],
    })
    edit = _persist_plan(tmp_path, domain, plan)
    camera = CameraEditPlanDocument.model_validate({
        "project_id": source.project_id, "source_asset_id": source.id,
        "edit_plan_artifact_id": edit.id, "edit_plan_input_hash": edit.input_hash,
        "camera_timeline_artifact_id": "camera", "camera_timeline_input_hash": "camera-hash",
        "identity_index_artifact_id": "identity", "identity_index_input_hash": "identity-hash",
        "visual_quality_artifact_id": "quality", "visual_quality_input_hash": "quality-hash",
        "input_hash": "reaction-camera",
        "shots": [{"edit_segment_order": 0, "video_source_asset_id": source.id,
                   "audio_source_asset_id": source.id, "source_start_us": video_start * 1_000_000,
                   "source_end_us": (video_start + 1) * 1_000_000,
                   "audio_source_start_us": audio_start * 1_000_000,
                   "audio_source_end_us": (audio_start + 1) * 1_000_000,
                   "scene_index": 0, "layout_id": "host", "camera_role": "interviewer_reaction",
                   "intent": "reaction", "visual_origin": "reaction_reuse",
                   "reaction_candidate_id": "candidate", "reaction_candidate_artifact_id": "reactions",
                   "reaction_candidate_input_hash": "reaction-hash", "interviewer_identity_id": "host",
                   "confirmed_identity_ids": ["host"], "visual_quality_usable": True, "evidence": []}],
        "diagnostics": {"shot_count": 1, "speaker_shot_count": 0, "context_shot_count": 0,
                        "fallback_shot_count": 0, "unusable_scene_count": 0, "reaction_shots_blocked_by": []},
        "engine": {"algorithm": "fixture", "algorithm_version": "1", "temporal_reuse_allowed": True},
    })
    camera_path = tmp_path / "camera.json"
    camera_path.write_text(camera.model_dump_json())
    camera_artifact = domain.create_stage_artifact(StageArtifact(project_id=source.project_id,
        stage="camera_edit_plan", schema_version=5, path=str(camera_path), input_hash=camera.input_hash))
    commands = _capture_render(monkeypatch)
    result = RenderService(config, domain).run(edit_plan_artifact=edit, camera_edit_plan_artifact=camera_artifact,
        encoder=None, headline=None, progress_cb=lambda *_: None, should_cancel=lambda: False,
        render_settings=_render_settings())
    doc = RenderDocument.model_validate_json(Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text())
    assert doc.quality.passed, doc.quality.issues
    _assert_window(commands[0], min(audio_start, video_start), max(audio_start, video_start) + 1)
    pixel = _center_pixel(config.render.ffmpeg, Path(doc.output_path), .5)
    assert pixel[2 if expected_color == "blue" else 0] > 200
    assert _dominant_audio_frequency(config.render.ffmpeg, Path(doc.output_path)) == pytest.approx(expected_audio, abs=20)


def test_auto_framing_trims_use_local_clock_without_mutating_spans():
    plan = _build_plan(project_id="project", source_id="source", transcript_id="transcript",
                       offset_seconds=0, kind="hard", video_cut_seconds=1, total_seconds=2)
    plan = plan.model_copy(update={"segments": [EditSegment(start=10, end=12, timeline_order=0)]})
    span = AutoFramingSpan(segment_order=0, scene_index=0, source_start_us=13_000_000,
        source_end_us=15_000_000, visual_origin="reaction_reuse", mode="blurred_background", reason="fixture")
    start, end = _source_input_window(plan, auto_framing=[span])
    assert (start, end) == (10, 15)
    graph, _, _ = _filtergraph(plan, 320, 320, 30, -14, -1, framing_mode="speaker_auto",
                              auto_framing=[span], source_time_offset=start)
    assert "[0:v]trim=start=3.000000:end=5.000000" in graph
    assert "[0:a]atrim=start=0.000000:end=2.000000" in graph
    assert span.source_start_us == 13_000_000
