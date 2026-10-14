from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.domain.models import SourceAsset, SourceKind, StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.edit.schemas import (
    EditPlanDiagnostics,
    EditPlanDocument,
    EditQualityReport,
    EditSegment,
    EditTransition,
)
from cortex.ingest.ffprobe import probe_media
from cortex.render.face_focus import FaceFocusSpan
from cortex.render.schemas import RenderDocument, RenderFramingSettings, RenderSettings
from cortex.render.service import (
    RenderPreconditionError,
    RenderService,
    _filtergraph,
    _focused_geometry,
    _source_input_window,
    _teaser_input_window,
)
from cortex.transcribe.schemas import EngineInfo, TranscriptDocument

from test_render import _center_pixel, _config, _dominant_audio_frequency
from test_render_jl_cut import _render_settings

COLORS = ["red", "lime", "blue", "yellow", "cyan", "magenta", "white", "orange"]
RGB = {
    "red": (255, 0, 0), "lime": (0, 255, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
    "cyan": (0, 255, 255), "magenta": (255, 0, 255), "white": (255, 255, 255),
    "orange": (255, 165, 0),
}
FREQUENCIES = [220, 330, 440, 550, 660, 770, 880, 990]
HARD = EditTransition(video="cut", audio="hard_concat", duration=0.0, kind="hard")


def _plan(*segments: EditSegment, total: float, project_id="p", source_id="s", transcript_id="t"):
    return EditPlanDocument(
        project_id=project_id, source_asset_id=source_id, transcript_artifact_id=transcript_id,
        analysis_artifact_id="analysis", input_hash="teaser-plan", clip_start=0.0, clip_end=8.0,
        profile="dynamic", segments=list(segments), timeline_duration_seconds=total,
        diagnostics=EditPlanDiagnostics(
            profile="dynamic", waveform_used=True, candidate_pauses=0, cuts=len(segments) - 1,
            saved_seconds=0.0, crossfade=0.0, vad_used=True,
        ),
        quality=EditQualityReport(passed=True, issues=[], profile="dynamic", degraded=False),
    )


def _teaser_first_plan(**kwargs) -> EditPlanDocument:
    return _plan(
        EditSegment(start=6.0, end=7.0, timeline_order=0, transition=HARD, piece_index=2,
                    teaser=True),
        EditSegment(start=0.0, end=2.0, timeline_order=1, piece_index=0),
        total=3.0, **kwargs,
    )


def test_input_windows_keep_teaser_out_of_the_shared_decoder() -> None:
    plan = _teaser_first_plan()
    assert _source_input_window(plan) == (0.0, 2.0)
    assert _teaser_input_window(plan) == (6.0, 7.0)
    assert _teaser_input_window(_plan(
        EditSegment(start=0.0, end=2.0, timeline_order=0), total=2.0,
    )) is None


def test_teaser_after_body_is_refused() -> None:
    plan = _plan(
        EditSegment(start=0.0, end=2.0, timeline_order=0, transition=HARD),
        EditSegment(start=6.0, end=7.0, timeline_order=1, teaser=True),
        total=3.0,
    )
    with pytest.raises(RenderPreconditionError, match="antes do corpo"):
        _teaser_input_window(plan)


def test_filtergraph_decodes_teaser_from_second_input_with_own_offset() -> None:
    graph, _video, _audio = _filtergraph(
        _teaser_first_plan(), 320, 320, 30, -14.0, -1.0,
        source_time_offset=0.0, teaser_time_offset=6.0,
    )
    assert "[1:v]trim=start=0.000000:end=1.000000,setpts=PTS-STARTPTS" in graph
    assert "[1:a]atrim=start=0.000000:end=1.000000" in graph
    assert "[0:v]trim=start=0.000000:end=2.000000" in graph
    assert "[0:a]atrim=start=0.000000:end=2.000000" in graph
    assert graph.count("[1:v]") == 1 and graph.count("[0:v]") == 1
    assert "[6.000000" not in graph and "start=6.000000" not in graph


def test_filtergraph_body_offset_and_teaser_offset_are_independent() -> None:
    plan = _plan(
        EditSegment(start=60.0, end=61.5, timeline_order=0, transition=HARD, teaser=True,
                    piece_index=3),
        EditSegment(start=10.0, end=14.0, timeline_order=1, piece_index=0),
        total=5.5,
    )
    graph, _v, _a = _filtergraph(
        plan, 320, 320, 30, -14.0, -1.0, source_time_offset=10.0, teaser_time_offset=60.0,
    )
    assert "[1:v]trim=start=0.000000:end=1.500000" in graph
    assert "[0:v]trim=start=0.000000:end=4.000000" in graph


def test_filtergraph_face_spans_of_teaser_segment_use_second_input() -> None:
    source, canvas = (640, 360), (320, 320)
    geometry = _focused_geometry("blurred_background", source, *canvas, 2.0, 0.25)
    spans = [
        FaceFocusSpan(segment_order=order, scene_index=0, source_start=start, source_end=end,
                      focus_x=0.25, reason="all_faces", face_count=1, sample_times=[start])
        for order, start, end in ((0, 6.0, 7.0), (1, 0.0, 2.0))
    ]
    graph, _v, _a = _filtergraph(
        _teaser_first_plan(), *canvas, 30, -14.0, -1.0, framing_mode="blurred_background",
        source_size=source, framing_zoom=2.0, face_spans=spans,
        face_geometries=[geometry, geometry], face_punches=[None, None],
        source_time_offset=0.0, teaser_time_offset=6.0,
    )
    assert "[1:v]trim=start=0.000000:end=1.000000" in graph
    assert "[0:v]trim=start=0.000000:end=2.000000" in graph


def _make_source(ffmpeg: Path, path: Path) -> None:
    command = [str(ffmpeg), "-y", "-v", "error"]
    for color in COLORS:
        command += ["-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=30:d=1"]
    for frequency in FREQUENCIES:
        command += ["-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=1"]
    count = len(COLORS)
    video = "".join(f"[{i}:v]" for i in range(count))
    audio = "".join(f"[{count + i}:a]" for i in range(count))
    command += [
        "-filter_complex",
        f"{video}concat=n={count}:v=1:a=0[v];{audio}concat=n={count}:v=0:a=1[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-g", "30", "-c:a", "aac", str(path),
    ]
    subprocess.run(command, check=True, timeout=120)


def _closest_color(pixel: tuple[int, int, int]) -> str:
    return min(RGB, key=lambda name: sum((a - b) ** 2 for a, b in zip(RGB[name], pixel)))


def _slice_audio(ffmpeg: Path, media: Path, start: float, duration: float, out: Path) -> Path:
    subprocess.run([
        str(ffmpeg), "-y", "-v", "error", "-ss", str(start), "-t", str(duration),
        "-i", str(media), "-vn", "-c:a", "aac", str(out),
    ], check=True, timeout=60)
    return out


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg and ffprobe required")
def test_real_render_opens_with_teaser_then_plays_body_from_its_start(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Teaser render")
    source_dir = tmp_path / "projects" / project.id / "source"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "source.mp4"
    _make_source(config.render.ffmpeg, source_path)
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="source.mp4",
        stored_path=str(source_path.relative_to(tmp_path)),
        sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        size_bytes=source_path.stat().st_size, probe=probe_media(config.render.ffprobe, source_path),
    ))
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(TranscriptDocument(
        language="pt", duration_seconds=8.0,
        engine=EngineInfo(
            model="fixture", requested_device="cpu", effective_device="cpu",
            requested_compute_type="int8", effective_compute_type="int8",
            batch_size=1, vad=True,
        ), segments=[],
    ).model_dump_json(), encoding="utf-8")
    transcript = domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project.id, source_asset_id=source.id, path=str(transcript_path),
        audio_sha256="audio", engine="fixture", model="fixture", device="cpu",
        compute_type="int8", language="pt", vad=True, batch_size=1, duration_seconds=8.0,
    ))
    plan = _teaser_first_plan(
        project_id=project.id, source_id=source.id, transcript_id=transcript.id,
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    plan_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="edit_plan", path=str(plan_path), input_hash=plan.input_hash,
    ))
    settings = _render_settings().model_copy(update={
        "framing": RenderFramingSettings(mode="vertical_crop"),
        "transitions": _render_settings().transitions.model_copy(
            update={"piece_junction": "cut", "teaser": True},
        ),
    })
    assert isinstance(settings, RenderSettings)

    result = RenderService(config, domain).run(
        edit_plan_artifact=plan_artifact, encoder=None, headline=None,
        progress_cb=lambda *_a: None, should_cancel=lambda: False, render_settings=settings,
    )
    document = RenderDocument.model_validate_json(
        Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text()
    )
    output = Path(document.output_path)
    ffmpeg = config.render.ffmpeg

    assert document.teaser is not None and document.teaser.applied
    assert (document.teaser.source_start, document.teaser.source_end) == (6.0, 7.0)
    assert document.teaser.input_seek_seconds == 6.0
    assert document.overlays.junction_times == [1.0]

    assert [_closest_color(_center_pixel(ffmpeg, output, t)) for t in (0.2, 0.8)] == ["white"] * 2
    assert _closest_color(_center_pixel(ffmpeg, output, 1.2)) == "red"
    assert _closest_color(_center_pixel(ffmpeg, output, 2.5)) == "lime"

    streams = probe_media(config.render.ffprobe, output)["streams"]
    durations = {s["codec_type"]: float(s["duration"]) for s in streams}
    assert durations["video"] == pytest.approx(3.0, abs=0.04)
    assert durations["audio"] == pytest.approx(3.0, abs=0.06)
    assert abs(durations["video"] - durations["audio"]) < 0.06

    head = _slice_audio(ffmpeg, output, 0.1, 0.7, tmp_path / "head.m4a")
    body = _slice_audio(ffmpeg, output, 1.2, 0.7, tmp_path / "body.m4a")
    assert _dominant_audio_frequency(ffmpeg, head) == pytest.approx(FREQUENCIES[6], rel=0.05)
    assert _dominant_audio_frequency(ffmpeg, body) == pytest.approx(FREQUENCIES[0], rel=0.05)


def test_captions_follow_timeline_order_with_teaser_first() -> None:
    from cortex.render.captions import build_timeline_words
    from cortex.transcribe.schemas import TranscriptSegment, TranscriptWord

    words = [
        TranscriptWord(start=0.2, end=0.6, word="comeco", probability=0.9),
        TranscriptWord(start=6.2, end=6.8, word="final", probability=0.9),
    ]
    transcript = TranscriptDocument(
        language="pt", duration_seconds=8.0,
        engine=EngineInfo(
            model="fixture", requested_device="cpu", effective_device="cpu",
            requested_compute_type="int8", effective_compute_type="int8",
            batch_size=1, vad=True,
        ),
        segments=[TranscriptSegment(
            id=0, start=0.2, end=6.8, text="comeco final", avg_logprob=-0.1,
            no_speech_prob=0.01, words=words,
        )],
    )
    timeline = build_timeline_words(_teaser_first_plan(), transcript)
    assert [(w.text, w.start) for w in timeline] == [("final", 0.2), ("comeco", 1.2)]
