from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.ingest.ffprobe import probe_media
from cortex.render.technical_quality import measure_technical_quality
from cortex.render.service import _measure_loudness, _normalize_render_loudness


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg and ffprobe required",
)


def _media(tmp_path: Path, name: str, *, faststart: bool, audio_filter: str) -> Path:
    path = tmp_path / name
    command = [
        shutil.which("ffmpeg") or "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=1",
        "-f", "lavfi", "-i", audio_filter,
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
    ]
    if faststart:
        command.extend(["-movflags", "+faststart"])
    command.append(str(path))
    subprocess.run(command, check=True, timeout=60)
    return path


def _measure(path: Path):
    ffmpeg = Path(shutil.which("ffmpeg") or "ffmpeg")
    ffprobe = Path(shutil.which("ffprobe") or "ffprobe")
    return measure_technical_quality(
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        media_path=path,
        probe=probe_media(ffprobe, path),
        should_cancel=lambda: False,
    )


def test_technical_quality_accepts_decodable_faststart_media(tmp_path: Path) -> None:
    path = _media(
        tmp_path,
        "valid.mp4",
        faststart=True,
        audio_filter="sine=frequency=440:sample_rate=48000:duration=1",
    )

    report, issues = _measure(path)

    assert report.full_decode_passed is True
    assert report.faststart is True
    assert report.moov_offset is not None and report.mdat_offset is not None
    assert report.moov_offset < report.mdat_offset
    assert report.pts_discontinuity_count == 0
    assert report.dts_discontinuity_count == 0
    assert report.audio_channel_count == 1
    assert issues == []


def test_technical_quality_blocks_mp4_without_faststart(tmp_path: Path) -> None:
    path = _media(
        tmp_path,
        "slow-start.mp4",
        faststart=False,
        audio_filter="sine=frequency=440:sample_rate=48000:duration=1",
    )

    report, issues = _measure(path)

    assert report.faststart is False
    assert "faststart_missing" in issues


def test_technical_quality_detects_clipping_jumps_and_inverted_stereo(tmp_path: Path) -> None:
    path = _media(
        tmp_path,
        "bad-audio.mp4",
        faststart=True,
        audio_filter=(
            "aevalsrc=sgn(sin(2*PI*1000*t))|-sgn(sin(2*PI*1000*t)):"
            "s=48000:d=1"
        ),
    )

    report, issues = _measure(path)

    assert report.audio_channel_count == 2
    assert report.clipped_sample_count > 0
    assert report.waveform_jump_count > 0
    assert report.channel_phase_correlation is not None
    assert report.channel_phase_correlation <= -0.95
    assert "audio_clipping_detected" in issues
    assert "audio_waveform_jump_detected" in issues
    assert "audio_channels_inverted" in issues


def test_technical_quality_blocks_truncated_file_on_full_decode(tmp_path: Path) -> None:
    original = _media(
        tmp_path,
        "original.mp4",
        faststart=True,
        audio_filter="sine=frequency=440:sample_rate=48000:duration=1",
    )
    probe = probe_media(Path(shutil.which("ffprobe") or "ffprobe"), original)
    truncated = tmp_path / "truncated.mp4"
    payload = original.read_bytes()
    truncated.write_bytes(payload[: max(1024, len(payload) // 2)])

    report, issues = measure_technical_quality(
        ffmpeg=Path(shutil.which("ffmpeg") or "ffmpeg"),
        ffprobe=Path(shutil.which("ffprobe") or "ffprobe"),
        media_path=truncated,
        probe=probe,
        should_cancel=lambda: False,
    )

    assert report.full_decode_passed is False
    assert "full_decode_failed" in issues


def test_technical_quality_rejects_more_than_stereo_channels(tmp_path: Path) -> None:
    path = _media(
        tmp_path,
        "surround.mp4",
        faststart=True,
        audio_filter="anullsrc=channel_layout=5.1:sample_rate=48000:d=1",
    )

    report, issues = _measure(path)

    assert report.audio_channel_count == 6
    assert "audio_channels_invalid" in issues


def test_two_pass_loudness_correction_hits_publish_target(tmp_path: Path) -> None:
    ffmpeg = Path(shutil.which("ffmpeg") or "ffmpeg")
    path = _media(
        tmp_path,
        "loud.mp4",
        faststart=True,
        audio_filter="sine=frequency=440:sample_rate=48000:duration=1",
    )
    before, _ = _measure_loudness(ffmpeg, path)
    assert abs(before - (-14.0)) > 1.0

    _normalize_render_loudness(
        ffmpeg,
        path,
        target_lufs=-14.0,
        true_peak_dbfs=-1.0,
        log_path=tmp_path / "loudnorm.log",
        should_cancel=lambda: False,
    )

    after, true_peak = _measure_loudness(ffmpeg, path)
    assert after == pytest.approx(-14.0, abs=0.5)
    assert true_peak <= -1.0
