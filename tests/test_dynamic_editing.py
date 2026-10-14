"""Tests for the dry-cut ("corte seco") dynamic editing profile (Gate: dynamic
default). Covers the requirements behind the rewrite: VAD no longer vetoes
cuts, one unsafe boundary drops only that cut instead of collapsing the whole
plan, high-confidence fillers get removed, overlapping approximate_edl ranges
are unioned instead of raising, and hard cuts render without A/V drift.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.analyze.fillers import Filler
from cortex.analyze.schemas import VadInterval
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.edit.boundary import EnergyTrack
from cortex.edit.planner import plan_safe_segments, remove_fillers
from cortex.edit.profiles import PROFILES
from cortex.edit.quality import validate_edit_plan
from cortex.edit.schemas import (
    EditPlanDiagnostics,
    EditPlanDocument,
    EditQualityReport,
    EditSegment,
    EditTransition,
    JlSettings,
)
from cortex.edit.service import EditPlanPreconditionError, resolve_semantic_ranges
from cortex.ingest.ffprobe import probe_media
from cortex.render.schemas import (
    RenderCanvasSettings,
    RenderCaptionSettings,
    RenderDocument,
    RenderHeadlineSettings,
    RenderSettings,
    RenderSubtitleSettings,
)
from cortex.render.service import RenderService
from cortex.transcribe.schemas import EngineInfo, TranscriptDocument, TranscriptWord

from test_render import _config

DYNAMIC = PROFILES["dynamic"]


def _word(start: float, end: float, text: str = "x") -> TranscriptWord:
    return TranscriptWord(start=start, end=end, word=text, probability=0.99)


def _word_containing(words: list[TranscriptWord], t: float, guard: float = 0.015) -> bool:
    return any(w.start + guard < t < w.end - guard for w in words)


# ---------------------------------------------------------------------------
# (a) multiple cuts from 0.3-0.6s gaps; no boundary inside a word; residual
# silence at each join stays within pre_roll + post_roll + a small tolerance.
# ---------------------------------------------------------------------------
def test_multiple_gaps_produce_multiple_clean_cuts() -> None:
    phrase_starts = [0.0, 1.30, 2.70, 4.20, 5.80]
    words: list[TranscriptWord] = []
    phrase_bounds: list[tuple[float, float]] = []
    for phrase_start in phrase_starts:
        w1 = _word(phrase_start, phrase_start + 0.30)
        w2 = _word(phrase_start + 0.35, phrase_start + 0.65)
        w3 = _word(phrase_start + 0.70, phrase_start + 1.00)
        words.extend([w1, w2, w3])
        phrase_bounds.append((w1.start, w3.end))

    segments, diagnostics = plan_safe_segments(words, 0.0, 7.0, DYNAMIC, energy=None, vad_intervals=None)

    assert diagnostics["candidate_pauses"] == 4
    assert diagnostics["cuts"] == 4
    assert len(segments) == 5

    for segment in segments:
        assert not _word_containing(words, segment["start"])
        assert not _word_containing(words, segment["end"])

    tolerance = DYNAMIC.pre_roll + DYNAMIC.post_roll + 0.02
    for (_, phrase_end), (next_start, _) in zip(phrase_bounds, phrase_bounds[1:]):
        joining = [
            (segment, following)
            for segment, following in zip(segments, segments[1:])
            if segment["end"] <= phrase_end + tolerance and following["start"] >= phrase_end
        ]
        assert joining, "expected a cut between these two phrases"
        segment, following = joining[0]
        residual = (segment["end"] - phrase_end) + (next_start - following["start"])
        assert 0.0 <= residual <= tolerance


# ---------------------------------------------------------------------------
# (b) a VAD interval covering the whole clip must not veto any cut.
# ---------------------------------------------------------------------------
def test_vad_covering_whole_clip_does_not_block_cuts() -> None:
    words = [
        _word(0.0, 0.45), _word(0.55, 1.0),
        _word(1.6, 2.05), _word(2.15, 2.6),
        _word(3.2, 3.65), _word(3.75, 4.2),
    ]
    vad = [VadInterval(start=0.0, end=100.0, duration=100.0, start_sample=0, end_sample=1)]

    segments, diagnostics = plan_safe_segments(words, 0.0, 4.5, DYNAMIC, energy=None, vad_intervals=vad)

    assert diagnostics["vad_used"] is True
    assert diagnostics["cuts"] >= 1
    assert len(segments) >= 2


# ---------------------------------------------------------------------------
# (c) one unsafe boundary drops only that cut, never the whole plan.
# ---------------------------------------------------------------------------
def test_one_unsafe_boundary_drops_only_that_cut() -> None:
    words = [_word(0.0, 0.5), _word(2.0, 2.5), _word(5.0, 5.5)]
    segments = [
        {"start": 0.0, "end": 1.0, "timeline_order": 0,
         "transition": {"video": "cut", "audio": "hard_concat", "duration": 0.0, "kind": "hard"}},
        # This segment's "end" lands inside the word at 2.0-2.5 with no
        # energy track available to snap it to a safe point.
        {"start": 1.0, "end": 2.2, "timeline_order": 1,
         "transition": {"video": "cut", "audio": "hard_concat", "duration": 0.0, "kind": "hard"}},
        {"start": 2.5, "end": 4.0, "timeline_order": 2,
         "transition": {"video": "cut", "audio": "hard_concat", "duration": 0.0, "kind": "hard"}},
        {"start": 4.0, "end": 6.0, "timeline_order": 3},
    ]

    repaired, report = validate_edit_plan(
        segments, words, DYNAMIC, clip_start=0.0, clip_end=6.0, energy=None, vad_intervals=None,
    )

    assert report["degraded"] is False
    assert report["passed"] is True
    assert any(issue["code"] == "cut_dropped_unsafe_boundary" for issue in report["issues"])
    # Only the unsafe cut (between the 2nd and 3rd original segments) drops:
    # 4 segments / 3 cuts become 3 segments / 2 cuts, not 1 segment / 0 cuts.
    assert len(repaired) == 3
    assert repaired[0]["start"] == pytest.approx(0.0)
    assert repaired[0]["end"] == pytest.approx(1.0)
    assert repaired[1]["start"] == pytest.approx(1.0)
    assert repaired[1]["end"] == pytest.approx(4.0)
    assert repaired[2]["start"] == pytest.approx(4.0)
    assert repaired[2]["end"] == pytest.approx(6.0)


# ---------------------------------------------------------------------------
# (d) high-confidence fillers are removed; lower-confidence ones are kept.
# ---------------------------------------------------------------------------
def test_high_confidence_filler_removed_low_confidence_kept() -> None:
    words = [
        _word(0.0, 0.5, "prev"),
        _word(0.6, 0.8, "eh"),
        _word(0.9, 1.4, "next"),
        _word(1.6, 1.8, "e"),
        _word(1.9, 2.0, "tail"),
    ]
    fillers = [
        Filler(word="eh", start=0.6, end=0.8, kind="hesitation", confidence=0.9),
        Filler(word="é", start=1.6, end=1.8, kind="hesitation", confidence=0.7),
    ]
    energy = EnergyTrack(origin=0.0, frame_seconds=0.01, rms=[1.0] * 300)
    segments = [{"start": 0.0, "end": 2.0}]

    result, removed = remove_fillers(segments, words, fillers, DYNAMIC, energy)

    assert removed == 1
    assert len(result) == 2
    # The "eh" filler span (0.6-0.8) is cut out...
    assert result[0]["end"] <= 0.65
    assert result[1]["start"] >= 0.75
    # ...but the "é" span (1.6-1.8, confidence below 0.8) survives untouched
    # inside the trailing kept piece.
    assert result[1]["start"] <= 1.6
    assert result[1]["end"] >= 1.8
    assert result[0]["transition"]["kind"] == "hard"


# ---------------------------------------------------------------------------
# (e) overlapping approximate_edl ranges are unioned, not rejected.
# ---------------------------------------------------------------------------
def test_overlapping_approximate_edl_is_unioned() -> None:
    entries = [
        {"source_start": 1.0, "source_end": 3.0},
        {"source_start": 2.5, "source_end": 4.0},
        {"source_start": 6.0, "source_end": 7.0},
    ]

    ranges = resolve_semantic_ranges(entries, start=0.0, end=10.0)

    assert ranges == [(1.0, 4.0), (6.0, 7.0)]


def test_approximate_edl_clamps_partial_overlap_and_drops_empty() -> None:
    entries = [
        {"source_start": -5.0, "source_end": 1.0},   # clamps to [0, 1)
        {"source_start": 9.5, "source_end": 20.0},    # clamps to [9.5, 10)
        {"source_start": 10.0, "source_end": 11.0},   # fully outside -> dropped
    ]

    ranges = resolve_semantic_ranges(entries, start=0.0, end=10.0)

    assert ranges == [(0.0, 1.0), (9.5, 10.0)]


def test_approximate_edl_raises_only_when_nothing_remains() -> None:
    with pytest.raises(EditPlanPreconditionError):
        resolve_semantic_ranges(
            [{"source_start": 20.0, "source_end": 21.0}], start=0.0, end=10.0,
        )


# ---------------------------------------------------------------------------
# (f) real ffmpeg render of a >=3-segment dynamic (hard-cut) plan: output
# video and audio durations must equal the planned timeline, not drift.
# ---------------------------------------------------------------------------
def _make_synthetic_source(ffmpeg: Path, path: Path, total_seconds: float) -> None:
    subprocess.run([
        str(ffmpeg), "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={total_seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={total_seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path),
    ], check=True, timeout=60)


def _stream_duration(ffprobe: Path, path: Path, stream: str) -> float:
    result = subprocess.run(
        [
            str(ffprobe), "-v", "error", "-select_streams", stream,
            "-show_entries", "stream=duration", "-of", "csv=p=0", str(path),
        ],
        capture_output=True, check=True, timeout=30, text=True,
    )
    return float(result.stdout.strip().splitlines()[0])


def _render_settings() -> RenderSettings:
    return RenderSettings(
        encoder="libx264", canvas=RenderCanvasSettings(width=320, height=320, fps=30),
        captions=RenderCaptionSettings(
            enabled=False, font_family="Montserrat", font_size=28, words_per_cue=3, outline=False,
        ),
        headline=RenderHeadlineSettings(
            enabled=False, font_family="Montserrat", font_size=36, duration_seconds=1.0,
        ),
        subtitles=RenderSubtitleSettings(sidecar_srt=False),
    )


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg and ffprobe required")
def test_real_ffmpeg_render_of_hard_cut_plan_has_no_av_drift(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Dynamic dry-cut render fixture")
    source_dir = tmp_path / "projects" / project.id / "source"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "source.mp4"
    total_seconds = 5.0
    _make_synthetic_source(config.render.ffmpeg, source_path, total_seconds)

    import hashlib

    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="source.mp4",
        stored_path=str(source_path.relative_to(tmp_path)),
        sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        size_bytes=source_path.stat().st_size, probe=probe_media(config.render.ffprobe, source_path),
    ))
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(TranscriptDocument(
        language="pt", duration_seconds=total_seconds,
        engine=EngineInfo(
            model="fixture", requested_device="cpu", effective_device="cpu",
            requested_compute_type="int8", effective_compute_type="int8",
            batch_size=1, vad=True,
        ), segments=[],
    ).model_dump_json(), encoding="utf-8")
    transcript = domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project.id, source_asset_id=source.id, path=str(transcript_path),
        audio_sha256="audio", engine="fixture", model="fixture", device="cpu",
        compute_type="int8", language="pt", vad=True, batch_size=1, duration_seconds=total_seconds,
    ))

    hard = EditTransition(video="cut", audio="hard_concat", duration=0.0, kind="hard")
    bounds = [(0.0, 1.2), (1.5, 3.0), (3.3, 4.5)]
    expected_total = sum(b[1] - b[0] for b in bounds)
    segments = [
        EditSegment(
            start=seg_start, end=seg_end, timeline_order=index,
            transition=hard if index < len(bounds) - 1 else None,
        )
        for index, (seg_start, seg_end) in enumerate(bounds)
    ]
    plan = EditPlanDocument(
        project_id=project.id, source_asset_id=source.id,
        transcript_artifact_id=transcript.id, analysis_artifact_id="analysis",
        input_hash="dynamic-hard-cut", clip_start=0.0, clip_end=total_seconds, profile="dynamic",
        segments=segments, timeline_duration_seconds=expected_total,
        diagnostics=EditPlanDiagnostics(
            profile="dynamic", waveform_used=False, candidate_pauses=2, cuts=2,
            saved_seconds=total_seconds - expected_total, crossfade=0.0, vad_used=False,
        ),
        quality=EditQualityReport(passed=True, issues=[], profile="dynamic", degraded=False),
        jl_settings=JlSettings(enabled=False, max_offset_seconds=0.0, requested_by="disabled"),
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    plan_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="edit_plan", path=str(plan_path), input_hash=plan.input_hash,
    ))

    result = RenderService(config, domain).run(
        edit_plan_artifact=plan_artifact, encoder=None, headline=None,
        progress_cb=lambda *_a: None, should_cancel=lambda: False, render_settings=_render_settings(),
    )
    document = RenderDocument.model_validate_json(
        Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text()
    )
    assert document.quality.passed is True

    output = Path(document.output_path)
    frame = 1.0 / 30.0
    tolerance = max(frame, 0.02)
    video_duration = _stream_duration(config.render.ffprobe, output, "v:0")
    audio_duration = _stream_duration(config.render.ffprobe, output, "a:0")
    assert video_duration == pytest.approx(expected_total, abs=tolerance)
    assert audio_duration == pytest.approx(expected_total, abs=tolerance)
    assert audio_duration == pytest.approx(video_duration, abs=tolerance)


def test_absorb_short_segments_bridges_near_piece_and_drops_isolated_one():
    from cortex.edit.planner import absorb_short_segments

    segments = [
        {"start": 0.0, "end": 3.0}, {"start": 3.5, "end": 3.97},  # 0.47 s, 0.5 s gap -> joins previous
        {"start": 10.0, "end": 14.0}, {"start": 20.0, "end": 20.4},  # isolated 0.4 s -> dropped
        {"start": 25.0, "end": 28.0},
    ]
    result = absorb_short_segments(segments)
    assert [(s["start"], s["end"]) for s in result] == [(0.0, 3.97), (10.0, 14.0), (25.0, 28.0)]
    assert all(s["end"] - s["start"] >= 0.6 for s in result)
