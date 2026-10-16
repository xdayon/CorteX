from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from cortex.edit.schemas import EditSegment
from cortex.render import face_focus
from cortex.render import service as render_service
from cortex.render.schemas import (
    RenderCanvasSettings,
    RenderDocument,
    RenderFramingSettings,
    RenderPunchInSettings,
)
from cortex.render.service import RenderService, _encoder_args, _filtergraph

from test_face_focus_render import _mock_faces, _two_scene_source
from test_render import _config
from test_render_input_window import _capture_render, _persist_plan
from test_render_jl_cut import _build_plan, _prepare_fixture, _render_settings
from test_render_punch_in import _one_segment_plan

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required"
)

SOURCE = (640, 360)


@pytest.mark.parametrize("mode", ["blurred_background", "vertical_crop"])
def test_fast_scaling_is_draft_only_and_applies_to_every_scale(mode):
    kwargs = dict(framing_mode=mode, source_size=SOURCE, framing_zoom=1.5 if mode == "blurred_background" else 1.0)
    plan = _one_segment_plan()
    export_graph, _v, _a = _filtergraph(plan, 320, 568, 30, -14.0, -1.0, **kwargs)
    draft_graph, _v, _a = _filtergraph(plan, 320, 568, 30, -14.0, -1.0, scale_flags="fast_bilinear", **kwargs)
    assert "flags=" not in export_graph
    scales = [part for part in draft_graph.split("scale=")[1:]]
    assert scales and all(part.split(",")[0].split(";")[0].endswith(":flags=fast_bilinear") for part in scales)
    assert draft_graph.replace(":flags=fast_bilinear", "") == export_graph


def test_export_encoder_args_are_unchanged():
    assert _encoder_args("libx264") == ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    assert _encoder_args("h264_nvenc") == ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "18"]
    assert _encoder_args("libx264", draft=True)[3] == "ultrafast"


def _faces_fixture(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.render.visual_quality_enabled = False
    config.ensure_runtime_dirs()
    domain, source, transcript = _prepare_fixture(tmp_path, config, color_switch=1, audio_switch=1, total=2)
    source = _two_scene_source(config, domain, tmp_path, source)
    transcript = domain.create_transcript_artifact(transcript.model_copy(update={
        "id": uuid4().hex, "source_asset_id": source.id,
    }))
    plan = _build_plan(project_id=source.project_id, source_id=source.id, transcript_id=transcript.id,
                       offset_seconds=0, kind="hard", video_cut_seconds=1, total_seconds=2)
    plan = plan.model_copy(update={"segments": [EditSegment(start=0.0, end=2.0, timeline_order=0)]})
    edit = _persist_plan(tmp_path, domain, plan)
    _mock_faces(monkeypatch, lambda timestamp: "left" if timestamp < 1.0 else "right")

    def run(*, purpose="export", headline=None):
        settings = _render_settings().model_copy(update={"canvas": RenderCanvasSettings(width=320, height=320, fps=30)})
        settings.framing = RenderFramingSettings(
            mode="blurred_background", zoom=2.0, focus_mode="faces",
            punch_in=RenderPunchInSettings(enabled=False),
        )
        result = RenderService(config, domain).run(
            edit_plan_artifact=edit, encoder=None, headline=headline, render_settings=settings,
            progress_cb=lambda *_: None, should_cancel=lambda: False, purpose=purpose,
        )
        document = RenderDocument.model_validate_json(
            Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text())
        return result, document

    return run


def test_timings_are_recorded_in_manifest_and_artifact_metadata(tmp_path, monkeypatch):
    run = _faces_fixture(tmp_path, monkeypatch)
    for purpose in ("draft", "export"):
        result, document = run(purpose=purpose)
        expected = {"plan_load", "face_focus", "ffmpeg_encode", "loudness_check",
                    "loudness_correction", "qa", "total"}
        assert expected <= set(document.timings)
        assert all(value >= 0 for value in document.timings.values())
        assert document.timings["total"] >= document.timings["ffmpeg_encode"] > 0
        assert result["timings"] == document.timings


def test_face_focus_analysis_is_reused_across_drafts_and_export(tmp_path, monkeypatch):
    run = _faces_fixture(tmp_path, monkeypatch)
    detections = []
    original_detect = face_focus.detect_faces
    monkeypatch.setattr(face_focus, "detect_faces", lambda s, f: detections.append(f) or original_detect(s, f))
    scene_runs = []
    original_run = face_focus.subprocess.run
    monkeypatch.setattr(face_focus.subprocess, "run", lambda *a, **k: ("scdet" in " ".join(a[0]) and scene_runs.append(a)) or original_run(*a, **k))

    _r, first = run(purpose="draft")
    assert detections and len(scene_runs) == 1
    analysed = (len(detections), len(scene_runs))

    _r, second = run(purpose="draft", headline="Outro título")
    _r, export = run(purpose="export")
    assert (len(detections), len(scene_runs)) == analysed
    spans = [(s.source_start, s.source_end, s.focus_x) for s in first.face_focus.spans]
    assert spans == [(s.source_start, s.source_end, s.focus_x) for s in second.face_focus.spans]
    assert spans == [(s.source_start, s.source_end, s.focus_x) for s in export.face_focus.spans]


def test_draft_uses_fast_scaling_and_skips_loudness_correction(tmp_path, monkeypatch):
    run = _faces_fixture(tmp_path, monkeypatch)
    commands = _capture_render(monkeypatch)
    corrections = []
    monkeypatch.setattr(render_service, "_measure_loudness", lambda *_: (-30.0, -8.0))
    monkeypatch.setattr(render_service, "_normalize_render_loudness", lambda *a, **k: corrections.append(a))

    _result, draft = run(purpose="draft")
    assert "flags=fast_bilinear" in " ".join(commands[-1])
    assert not corrections
    assert draft.timings["loudness_correction"] == 0
    assert "loudness_out_of_tolerance" in draft.quality.warnings
    assert "loudness_out_of_tolerance" not in draft.quality.issues

    _result, export = run(purpose="export")
    assert "flags=" not in " ".join(commands[-1])
    assert len(corrections) == 1
    assert "loudness_out_of_tolerance" in export.quality.issues
