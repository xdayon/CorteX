"""Scene inserts replace only the picture: the clip's audio and captions go on untouched."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from array import array
from pathlib import Path

import pytest

import cortex.render.service as render_service
from cortex.edit.schemas import EditSegment, EditTransition
from cortex.render.schemas import RenderDocument, RenderFramingSettings, RenderInsert
from cortex.render.service import (
    RenderPreconditionError,
    RenderService,
    _AppliedInsert,
    _filtergraph,
    _resolve_inserts,
)

from test_face_focus_render import CANVAS, SOURCE, _geo, _span
from test_render import _center_pixel, _config
from test_render_input_window import _persist_plan
from test_render_jl_cut import _build_plan, _prepare_fixture, _render_settings
from test_render_punch_in import _one_segment_plan

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg and ffprobe required"
)


def _insert(anchor, duration=0.5, source_start=2.5, kind="reaction", **extra) -> RenderInsert:
    return RenderInsert(
        kind=kind, anchor=anchor, duration=duration, source_start=source_start, **extra,
    )


def _setup(tmp_path):
    """Red for 0-2 s, blue for 2-4 s; a two-segment plan cut at 1 s inside the red part."""
    config = _config(tmp_path)
    config.render.visual_quality_enabled = False
    config.ensure_runtime_dirs()
    domain, source, transcript = _prepare_fixture(
        tmp_path, config, color_switch=2, audio_switch=2, total=4,
    )
    plan = _build_plan(
        project_id=source.project_id, source_id=source.id, transcript_id=transcript.id,
        offset_seconds=0, kind="hard", video_cut_seconds=1.0, total_seconds=2.0,
    )
    return config, domain, _persist_plan(tmp_path, domain, plan), plan


def _render(config, domain, edit, inserts, *, purpose="export", framing=None):
    settings = _render_settings()
    settings.inserts = inserts
    if framing is not None:
        settings.framing = framing
    result = RenderService(config, domain).run(
        edit_plan_artifact=edit, encoder=None, headline=None, render_settings=settings,
        progress_cb=lambda *_: None, should_cancel=lambda: False, purpose=purpose,
    )
    return RenderDocument.model_validate_json(
        Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text()
    )


def _pcm(config, media) -> array:
    raw = subprocess.run([
        str(config.render.ffmpeg), "-v", "error", "-i", str(media), "-map", "0:a:0",
        "-f", "s16le", "-ac", "1", "-ar", "48000", "-",
    ], capture_output=True, check=True, timeout=60).stdout
    samples = array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    return samples


def _video_frames(config, media) -> int:
    out = subprocess.run([
        str(config.render.ffprobe), "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(media),
    ], capture_output=True, text=True, check=True, timeout=60).stdout
    return int(out.strip())


@needs_ffmpeg
def test_real_render_swaps_only_the_picture_and_keeps_the_audio(tmp_path):
    config, domain, edit, plan = _setup(tmp_path)
    plain = _render(config, domain, edit, [])
    inserts = [_insert(0.3, 0.5), _insert(1.2, 0.5, source_start=2.6)]
    document = _render(config, domain, edit, inserts)
    assert document.quality.passed, document.quality.issues
    media = Path(document.output_path)

    def color(timestamp):
        return _center_pixel(config.render.ffmpeg, media, timestamp)

    for inside in (0.55, 1.45):
        red, _g, blue = color(inside)
        assert blue > 200 and red < 60, (inside, color(inside))
    for outside in (0.1, 0.95, 1.1, 1.9):
        red, _g, blue = color(outside)
        assert red > 200 and blue < 60, (outside, color(outside))

    frames = _video_frames(config, media)
    assert abs(frames - _video_frames(config, Path(plain.output_path))) <= 1
    assert abs(frames - plan.timeline_duration_seconds * 30) <= 1

    with_insert, without = _pcm(config, media), _pcm(config, Path(plain.output_path))
    assert len(with_insert) == len(without)
    assert max(abs(a - b) for a, b in zip(with_insert, without)) <= 8

    assert [(i.applied, i.segment_order, i.reason) for i in document.inserts] == [
        (True, 0, None), (True, 1, None),
    ]
    assert [i.output_start for i in document.inserts] == pytest.approx([0.3, 1.2])
    assert [i.effective_duration for i in document.inserts] == [0.5, 0.5]
    assert [i.focus_x for i in document.inserts] == [0.5, 0.5]
    assert document.input_hash != plain.input_hash

    draft = _render(config, domain, edit, inserts, purpose="draft")
    assert [i.applied for i in draft.inserts] == [True, True]
    assert _center_pixel(config.render.ffmpeg, Path(draft.output_path), 0.55)[2] > 200


@needs_ffmpeg
def test_rules_outside_plan_clipped_overlap_and_outside_source_still_render(tmp_path):
    config, domain, edit, _plan = _setup(tmp_path)
    document = _render(config, domain, edit, [
        _insert(5.0),                                   # outside the plan
        _insert(0.4, 1.0),                              # 0.6 s fit before the segment end
        _insert(0.45, 0.5, source_start=3.0),           # overlaps the clipped one
        _insert(1.2, 0.5, source_start=3.9),            # footage past the source end
        _insert(0.95, 1.0, source_start=3.0),           # only 0.05 s fit before the cut
    ])
    assert document.quality.passed, document.quality.issues
    assert [(i.applied, i.reason) for i in document.inserts] == [
        (False, "outside_plan"), (True, "clipped_to_segment"), (False, "overlaps_insert"),
        (False, "outside_source"), (False, "outside_plan"),
    ]
    clipped = document.inserts[1]
    assert clipped.effective_duration == pytest.approx(0.6)
    assert clipped.requested_duration == 1.0
    media = Path(document.output_path)
    assert _center_pixel(config.render.ffmpeg, media, 0.7)[2] > 200
    assert _center_pixel(config.render.ffmpeg, media, 1.5)[0] > 200


def test_resolve_inserts_accounts_for_crossfades_and_skips_the_teaser():
    plan = _one_segment_plan().model_copy(update={"segments": [
        EditSegment(start=6.0, end=7.0, timeline_order=0, teaser=True,
                    transition=EditTransition(video="cut", audio="hard_concat", duration=0.0, kind="hard")),
        EditSegment(start=0.0, end=2.0, timeline_order=1,
                    transition=EditTransition(video="fade", audio="tri", duration=0.4, kind="crossfade")),
        EditSegment(start=3.0, end=5.0, timeline_order=2),
    ]})
    resolved = _resolve_inserts(
        plan, [_insert(6.2), _insert(3.5, source_start=0.0), _insert(1.0)], source_duration=10.0,
    )
    assert [(i.applied, i.reason) for i in resolved] == [
        (False, "outside_plan"), (True, None), (True, None),
    ]
    # teaser 1.0 s, then the 2.0 s segment, whose 0.4 s crossfade overlaps the next one
    assert resolved[1].output_start == pytest.approx(1.0 + 2.0 - 0.4 + 0.5)
    assert resolved[2].output_start == pytest.approx(1.0 + 1.0)


def test_no_inserts_leave_the_cache_payload_untouched(tmp_path, monkeypatch):
    config, domain, edit, _plan = _setup(tmp_path)
    payloads = []
    real = json.dumps

    def spy(obj, *args, **kwargs):
        if isinstance(obj, dict) and "input_window" in obj:
            payloads.append(obj)
        return real(obj, *args, **kwargs)

    monkeypatch.setattr(render_service.json, "dumps", spy)
    plain = _render(config, domain, edit, [])
    assert "inserts_version" not in payloads[-1] and "inserts" not in payloads[-1]
    assert "inserts" not in payloads[-1]["render_settings"]
    assert plain.inserts == []
    _render(config, domain, edit, [_insert(0.3)])
    assert payloads[-1]["inserts_version"] == 1 and len(payloads[-1]["inserts"]) == 1


def _pieces(graph, index=0):
    return [
        int(frames) for frames in re.findall(
            rf"\[inspan{index}_\d+\]tpad=stop_mode=clone:stop_duration=1,trim=end_frame=(\d+)", graph,
        )
    ]


def test_wide_insert_uses_the_blurred_full_frame_even_for_a_vertical_crop_clip():
    wide = _AppliedInsert("wide", 0, 0.3, 0.5, 2.0, None)
    graph, _v, _a = _filtergraph(
        _one_segment_plan(), *CANVAS, 30, -14.0, -1.0, framing_mode="vertical_crop",
        source_size=SOURCE, inserts=[wide],
    )
    piece = next(part for part in graph.split(";") if part.startswith("[1:v]"))
    assert "trim=start=0.000000:end=0.500000" in piece
    assert re.search(r"\[1:v\].*split=2\[inspan0_1_bgraw\]", graph)
    assert "gblur" in graph and f"scale={CANVAS[0]}:{CANVAS[1]}:force_original_aspect_ratio=decrease" in graph
    base = next(part for part in graph.split(";") if part.startswith("[0:v]trim=start=0.000000:end=0.300000"))
    assert "force_original_aspect_ratio=increase" in base  # the base keeps its vertical crop
    # The wide piece neither crops its foreground nor follows the clip's vertical position.
    fg = next(part for part in graph.split(";") if part.startswith("[inspan0_1_fgraw]"))
    assert "crop=" not in fg


def test_wide_insert_ignores_zoom_and_position_of_a_blurred_clip():
    wide = _AppliedInsert("wide", 0, 0.3, 0.5, 2.0, None)
    graph, _v, _a = _filtergraph(
        _one_segment_plan(), *CANVAS, 30, -14.0, -1.0, framing_mode="blurred_background",
        source_size=SOURCE, framing_zoom=2.0, framing_position_y=0.2, inserts=[wide],
    )
    fg = next(part for part in graph.split(";") if part.startswith("[inspan0_1_fgraw]"))
    assert "crop=" not in fg
    overlay = graph[graph.index("[inspan0_1_bg][inspan0_1_fg]"):].split("[inspan0_1]")[0]
    assert "(H-h)/2" in overlay and "0.200000" not in overlay


def test_reaction_insert_keeps_the_clip_framing_around_its_own_focus():
    reaction = _AppliedInsert("reaction", 0, 0.3, 0.5, 2.0, 0.9)
    graph, _v, _a = _filtergraph(
        _one_segment_plan(), *CANVAS, 30, -14.0, -1.0, framing_mode="blurred_background",
        source_size=SOURCE, framing_zoom=2.0, inserts=[reaction], punch_ins={},
    )
    geometry = _geo(0.9)
    assert f"crop={geometry.w}:{geometry.h}:{geometry.x}:{geometry.y}" in graph


@pytest.mark.parametrize("teaser", [False, True])
def test_filtergraph_pieces_sum_to_the_segment_frames_and_inserts_use_their_own_input(teaser):
    plan = _one_segment_plan()
    spans = [_span(0, 0.0, 0.4, 0.25), _span(0, 0.4, 1.0, 0.75, scene=1)]
    geometries = [_geo(0.25), _geo(0.75)]
    segments = [plan.segments[0]]
    if teaser:
        segments.insert(0, EditSegment(start=5.0, end=6.0, timeline_order=0, teaser=True))
        segments[1] = segments[1].model_copy(update={"timeline_order": 1})
        spans = [_span(0, 5.0, 6.0, 0.5), *(span.model_copy(update={"segment_order": 1}) for span in spans)]
        geometries = [_geo(0.5), *geometries]
    plan = plan.model_copy(update={
        "segments": segments, "timeline_duration_seconds": 2.0 if teaser else 1.0,
    })
    order = 1 if teaser else 0
    graph, _v, _a = _filtergraph(
        plan, *CANVAS, 30, -14.0, -1.0, framing_mode="blurred_background", source_size=SOURCE,
        framing_zoom=2.0, face_spans=spans, face_geometries=geometries, face_punches=[None] * len(spans),
        inserts=[_AppliedInsert("reaction", order, 0.3, 0.5, 2.0, 0.5)],
    )
    index = 1 if teaser else 0
    pieces = _pieces(graph, index)
    assert pieces == [9, 15, 6] and sum(pieces) == 30
    insert_input = 2 if teaser else 1  # base, (teaser), then the inserts
    insert_part = next(
        part for part in graph.split(";") if part.startswith(f"[{insert_input}:v]trim=start=0.000000")
    )
    assert "end=0.500000" in insert_part
    assert f"[{insert_input + 1}:v]" not in graph
    assert f"concat=n=3:v=1:a=0[vraw{index}]" in graph
    assert f"trim=end_frame=30,setpts=PTS-STARTPTS[v{index}]" in graph
    # Audio never reads the insert input.
    assert all(f"[{insert_input}:a]" not in part for part in graph.split(";"))


def test_filtergraph_without_inserts_is_unchanged():
    plan = _one_segment_plan()
    assert _filtergraph(plan, *CANVAS, 30, -14.0, -1.0) == _filtergraph(
        plan, *CANVAS, 30, -14.0, -1.0, inserts=[],
    )


@needs_ffmpeg
def test_reaction_face_failure_fails_the_render_clearly(tmp_path, monkeypatch):
    config, domain, edit, _plan = _setup(tmp_path)

    def broken(**kwargs):
        if isinstance(kwargs["segments"][0], render_service._FootageWindow):
            raise render_service.FaceFocusError("modelo indisponível")
        return [_span(item.timeline_order, item.video_start, item.video_end, 0.5)
                for item in kwargs["segments"]]

    monkeypatch.setattr(render_service, "resolve_face_focus", broken)
    with pytest.raises(RenderPreconditionError, match="cena inserida"):
        _render(
            config, domain, edit, [_insert(0.3)],
            framing=RenderFramingSettings(mode="blurred_background", zoom=2.0, focus_mode="faces"),
        )
