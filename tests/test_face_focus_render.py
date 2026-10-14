from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cortex.analyze.face_detect import FaceDetectionError
from cortex.edit.schemas import EditSegment
from cortex.ingest.ffprobe import probe_media
from cortex.render import face_focus
from cortex.render.face_focus import FaceFocusSpan
from cortex.render.schemas import (
    RenderCanvasSettings,
    RenderDocument,
    RenderFramingSettings,
    RenderFramingSettingsPatch,
    RenderPunchInSettings,
    RenderSettingsPatch,
)
from cortex.render.service import (
    RenderPreconditionError,
    RenderService,
    _filtergraph,
    _focused_geometry,
    _merge_render_settings,
    _resolve_segment_punch_in,
)

from test_render import _config
from test_render_input_window import _capture_render, _persist_plan
from test_render_jl_cut import _build_plan, _prepare_fixture, _render_settings
from test_render_punch_in import _one_segment_plan

SOURCE = (640, 360)
CANVAS = (320, 320)


def test_focus_mode_schema_default_patch_and_gating():
    assert RenderFramingSettings().focus_mode == "manual"
    merged = _merge_render_settings(_render_settings(), RenderSettingsPatch(
        framing=RenderFramingSettingsPatch(mode="blurred_background", focus_mode="faces"),
    ))
    assert merged.framing.focus_mode == "faces"
    with pytest.raises(ValidationError, match="foco automático"):
        RenderFramingSettings(mode="face_static_crop", focus_mode="faces")
    with pytest.raises(ValidationError):
        RenderFramingSettings(mode="speaker_auto", focus_mode="faces")
    assert RenderFramingSettings(mode="vertical_crop", focus_mode="faces").focus_mode == "faces"


def _span(order, start, end, focus, scene=0):
    return FaceFocusSpan(
        segment_order=order, scene_index=scene, source_start=start, source_end=end,
        focus_x=focus, reason="all_faces", face_count=1, sample_times=[start],
    )


def _geo(focus):
    return _focused_geometry("blurred_background", SOURCE, *CANVAS, 2.0, focus)


def test_filtergraph_single_span_uses_the_span_geometry():
    geometry = _geo(0.25)
    graph, _v, _a = _filtergraph(
        _one_segment_plan(), *CANVAS, 30, -14.0, -1.0, framing_mode="blurred_background",
        source_size=SOURCE, framing_zoom=2.0, face_spans=[_span(0, 0.0, 1.0, 0.25)],
        face_geometries=[geometry], face_punches=[None],
    )
    assert f"crop={geometry.w}:{geometry.h}:{geometry.x}:{geometry.y}" in graph
    assert "facefocus" not in graph and "end_frame" not in graph


def test_filtergraph_split_segment_frame_counts_sum_and_punch_per_span():
    left, right = _geo(0.25), _geo(0.75)
    assert left.x != right.x
    punch_settings = RenderPunchInSettings(enabled=True, scale=1.2, alternate_on_jump_cuts=False)
    punches = [
        _resolve_segment_punch_in(
            0, punch_settings, framing_mode="blurred_background", canvas_width=CANVAS[0],
            canvas_height=CANVAS[1], static_face_crop_map=None, geometry=geometry,
        )
        for geometry in (left, right)
    ]
    assert all(p.applied for p in punches)
    spans = [_span(0, 0.0, 0.4, 0.25), _span(0, 0.4, 1.0, 0.75, scene=1)]
    graph, _v, _a = _filtergraph(
        _one_segment_plan(), *CANVAS, 30, -14.0, -1.0, framing_mode="blurred_background",
        source_size=SOURCE, framing_zoom=2.0, face_spans=spans,
        face_geometries=[left, right], face_punches=punches,
    )
    assert "trim=end_frame=12" in graph and "trim=end_frame=18" in graph  # 12 + 18 == 30
    assert "concat=n=2:v=1:a=0[v0]" in graph
    assert "trim=start=0.000000:end=0.400000" in graph and "trim=start=0.400000:end=1.000000" in graph
    for punch in punches:
        assert f"crop={punch.crop_width}:{punch.crop_height}:{punch.anchor_x}:{punch.anchor_y}" in graph
    assert punches[0].anchor_x != punches[1].anchor_x
    assert punches[0].crop_width < left.w


def test_vertical_crop_focus_moves_the_cover_crop():
    left = _focused_geometry("vertical_crop", SOURCE, 320, 568, 1.0, 0.1)
    right = _focused_geometry("vertical_crop", SOURCE, 320, 568, 1.0, 0.9)
    assert left.x == 0 and right.x == SOURCE[0] - right.w and left.w == right.w
    plan = _one_segment_plan()
    graph, _v, _a = _filtergraph(
        plan, 320, 568, 30, -14.0, -1.0, framing_mode="vertical_crop", source_size=SOURCE,
        face_spans=[_span(0, 0.0, 1.0, 0.9)], face_geometries=[right], face_punches=[None],
    )
    assert f"crop={right.w}:{right.h}:{right.x}:{right.y}" in graph


def _two_scene_source(config, domain, tmp_path, source):
    path = (config.paths.data_dir / source.stored_path).with_name("two_scenes.mp4")
    scene = "color=c={a}:size=640x360:rate=30:duration=1,drawbox=x=320:y=0:w=320:h=360:color={b}:t=fill"
    subprocess.run([
        str(config.render.ffmpeg), "-y", "-v", "error",
        "-f", "lavfi", "-i", scene.format(a="red", b="green"),
        "-f", "lavfi", "-i", scene.format(a="blue", b="white"),
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path),
    ], check=True, timeout=60)
    return domain.create_source_asset(source.model_copy(update={
        "id": uuid4().hex, "stored_path": str(path.relative_to(config.paths.data_dir)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size,
        "probe": probe_media(config.render.ffprobe, path),
    }))


def _pixel(ffmpeg, media, timestamp, x, y):
    out = subprocess.run([
        str(ffmpeg), "-v", "error", "-ss", str(timestamp), "-i", str(media), "-frames:v", "1",
        "-vf", f"crop=1:1:{x}:{y},format=rgb24", "-f", "rawvideo", "-",
    ], capture_output=True, check=True, timeout=30).stdout
    return tuple(out[:3])


def _stream_duration(config, media, stream):
    out = subprocess.run([
        str(config.render.ffprobe), "-v", "error", "-select_streams", stream,
        "-show_entries", "stream=duration", "-of", "csv=p=0", str(media),
    ], capture_output=True, text=True, check=True, timeout=30).stdout
    return float(out.strip())


def _mock_faces(monkeypatch, side_of):
    monkeypatch.setattr(face_focus, "load_session", lambda: object())
    monkeypatch.setattr(face_focus, "_extract_frame_rgb", lambda ffmpeg, source, timestamp: timestamp)

    def detect(session, frame):
        x = 0.1 if side_of(frame) == "left" else 0.8
        return [{"x": x, "y": 0.2, "width": 0.1, "height": 0.3, "score": 0.95}]

    monkeypatch.setattr(face_focus, "detect_faces", detect)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_real_render_crops_each_scene_around_its_own_face(tmp_path, monkeypatch):
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
    commands = _capture_render(monkeypatch)

    def run(*, purpose="export", punch=False):
        settings = _render_settings().model_copy(update={"canvas": RenderCanvasSettings(width=320, height=320, fps=30)})
        settings.framing = RenderFramingSettings(
            mode="blurred_background", zoom=2.0, focus_mode="faces",
            punch_in=RenderPunchInSettings(enabled=punch, scale=1.2, alternate_on_jump_cuts=False),
        )
        result = RenderService(config, domain).run(
            edit_plan_artifact=edit, encoder=None, headline=None, render_settings=settings,
            progress_cb=lambda *_: None, should_cancel=lambda: False, purpose=purpose,
        )
        document = RenderDocument.model_validate_json(
            Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text())
        return result, document

    _result, document = run()
    assert document.quality.passed, document.quality.issues
    media = Path(document.output_path)
    assert _pixel(config.render.ffmpeg, media, 0.5, 160, 160)[0] > 200  # left half: red
    white = _pixel(config.render.ffmpeg, media, 1.5, 160, 160)
    assert min(white) > 200, white  # right half: white
    assert abs(_stream_duration(config, media, "v:0") - 2.0) < 0.05
    assert abs(_stream_duration(config, media, "a:0") - 2.0) < 0.1
    info = document.face_focus
    assert (info.requested_mode, info.effective_mode) == ("faces", "faces")
    assert info.algorithm_version == face_focus.FACE_FOCUS_ALGORITHM_VERSION
    assert [round(span.focus_x, 2) for span in info.spans] == [0.25, 0.75]
    assert [span.reason for span in info.spans] == ["all_faces", "all_faces"]
    assert round(info.spans[0].source_end, 1) == 1.0
    assert document.requested_settings.framing.focus_mode == "faces"
    assert "end_frame=30" in " ".join(commands[-1])

    cached_result, _doc = run()
    assert cached_result["cached"]

    draft_result, draft = run(purpose="draft")
    assert draft.purpose == "draft"
    assert [span.focus_x for span in draft.face_focus.spans] == [span.focus_x for span in info.spans]

    _r, punched = run(punch=True)
    graph = " ".join(commands[-1])
    assert [p.applied for p in punched.punch_ins] == [True]
    assert graph.count("crop=") >= 4  # background crop + two punched foreground crops

    shutil.rmtree(tmp_path / "projects" / source.project_id / "cache" / "face_focus")
    _mock_faces(monkeypatch, lambda timestamp: "right")
    _r, swapped = run()
    assert swapped.input_hash != document.input_hash
    assert [round(span.focus_x, 2) for span in swapped.face_focus.spans] == [0.75, 0.75]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_detection_failure_fails_the_render_with_a_clear_error(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain, source, transcript = _prepare_fixture(tmp_path, config, color_switch=1, audio_switch=1, total=2)
    plan = _build_plan(project_id=source.project_id, source_id=source.id, transcript_id=transcript.id,
                       offset_seconds=0, kind="hard", video_cut_seconds=1, total_seconds=2)
    edit = _persist_plan(tmp_path, domain, plan)
    monkeypatch.setattr(face_focus, "load_session", lambda: object())
    monkeypatch.setattr(face_focus, "_extract_frame_rgb", lambda *_: 0)

    def broken(session, frame):
        raise FaceDetectionError("modelo indisponível")

    monkeypatch.setattr(face_focus, "detect_faces", broken)
    settings = _render_settings()
    settings.framing = RenderFramingSettings(mode="blurred_background", zoom=2.0, focus_mode="faces")
    with pytest.raises(RenderPreconditionError, match="detectar rostos"):
        RenderService(config, domain).run(
            edit_plan_artifact=edit, encoder=None, headline=None, render_settings=settings,
            progress_cb=lambda *_: None, should_cancel=lambda: False,
        )
