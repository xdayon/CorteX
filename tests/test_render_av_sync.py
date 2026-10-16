"""Many cuts must not let the picture drift ahead of the voice (exact frames per segment)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.edit.schemas import (
    EditPlanDiagnostics,
    EditPlanDocument,
    EditQualityReport,
    EditSegment,
)
from cortex.render.service import _filtergraph

FPS = 30


def _plan(durations: list[float], gap: float = 0.37) -> EditPlanDocument:
    segments, cursor = [], 0.5
    for order, duration in enumerate(durations):
        end = round(cursor + duration, 3)
        segments.append(EditSegment(
            start=cursor, end=end, video_start=cursor, video_end=end,
            audio_start=cursor, audio_end=end, timeline_order=order,
        ))
        cursor = round(end + gap, 3)
    total = round(sum(durations), 6)
    return EditPlanDocument(
        project_id="p", source_asset_id="s", transcript_artifact_id="t",
        analysis_artifact_id="a", input_hash="av-sync", clip_start=segments[0].start,
        clip_end=segments[-1].end, profile="dynamic", segments=segments,
        timeline_duration_seconds=total,
        diagnostics=EditPlanDiagnostics(
            profile="dynamic", waveform_used=False, candidate_pauses=0,
            cuts=len(durations) - 1, saved_seconds=0.0, crossfade=0.0, vad_used=False,
        ),
        quality=EditQualityReport(passed=True, issues=[], profile="dynamic", degraded=False),
    )


def _frame_counts(graph: str) -> list[int]:
    return [int(part.split("trim=end_frame=")[1].split(",")[0])
            for part in graph.split(";") if "trim=end_frame=" in part]


def test_each_segment_gets_its_share_of_the_cumulative_frame_grid() -> None:
    plan = _plan([1.01, 1.02, 1.03])
    graph, _video, _audio = _filtergraph(plan, 180, 320, FPS, -14.0, -1.0)
    # 30.3 -> 30, 60.9 -> 61, 91.8 -> 92: trimming each cut alone would give 30 + 30 + 30.
    assert _frame_counts(graph) == [30, 31, 31]
    assert sum(_frame_counts(graph)) == round(3.06 * FPS)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg indisponível")
def test_sixteen_cuts_keep_video_and_audio_the_same_length(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30000/1001:duration=90",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=90",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(source),
    ], check=True, timeout=120)
    durations = [2.345, 3.127, 1.913, 4.411, 2.718, 3.333, 1.777, 2.222,
                 3.861, 2.049, 4.093, 1.618, 2.999, 3.141, 2.503, 1.866]
    plan = _plan(durations)
    graph, video_label, audio_label = _filtergraph(plan, 180, 320, FPS, -14.0, -1.0)
    output = tmp_path / "cut.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", str(source), "-filter_complex", graph,
        "-map", video_label, "-map", audio_label, "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", str(output),
    ], check=True, timeout=300)
    probe = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-count_frames",
        "-show_entries", "stream=codec_type,duration,nb_read_frames", "-of", "json", str(output),
    ], capture_output=True, text=True, check=True).stdout)
    streams = {stream["codec_type"]: stream for stream in probe["streams"]}
    expected_frames = round(sum(durations) * FPS)
    assert int(streams["video"]["nb_read_frames"]) == expected_frames
    video_seconds = int(streams["video"]["nb_read_frames"]) / FPS
    assert abs(video_seconds - float(streams["audio"]["duration"])) < 1 / FPS
