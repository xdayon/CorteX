from __future__ import annotations

import math
import struct
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TECHNICAL_QUALITY_VERSION = 1
PTS_BACKWARD_TOLERANCE_SECONDS = 0.5
DTS_BACKWARD_TOLERANCE_SECONDS = 0.001
AV_SYNC_TOLERANCE_SECONDS = 0.04
CLIPPING_SAMPLE_THRESHOLD = 0.999
WAVEFORM_JUMP_THRESHOLD = 0.95
MAX_AUDIO_CHANNELS = 2
INVERTED_PHASE_CORRELATION = -0.95


class TechnicalQualityError(RuntimeError):
    pass


class TechnicalQualityCancelled(TechnicalQualityError):
    pass


class RenderTechnicalQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = TECHNICAL_QUALITY_VERSION
    full_decode_passed: bool
    faststart: bool
    moov_offset: int | None = Field(default=None, ge=0)
    mdat_offset: int | None = Field(default=None, ge=0)
    pts_discontinuity_count: int = Field(ge=0)
    dts_discontinuity_count: int = Field(ge=0)
    av_sync_delta_seconds: float | None = Field(default=None, ge=0)
    audio_channel_count: int | None = Field(default=None, ge=0)
    audio_peak_amplitude: float | None = Field(default=None, ge=0)
    clipped_sample_count: int = Field(ge=0)
    waveform_jump_count: int = Field(ge=0)
    channel_phase_correlation: float | None = Field(default=None, ge=-1, le=1)
    thresholds: dict[str, float | int]
    engine: dict[str, str]


def _top_level_mp4_atoms(path: Path) -> list[tuple[str, int]]:
    atoms: list[tuple[str, int]] = []
    size = path.stat().st_size
    offset = 0
    with path.open("rb") as handle:
        while offset + 8 <= size:
            handle.seek(offset)
            header = handle.read(16)
            if len(header) < 8:
                break
            atom_size = int.from_bytes(header[:4], "big")
            atom_type = header[4:8].decode("ascii", errors="replace")
            header_size = 8
            if atom_size == 1:
                if len(header) < 16:
                    break
                atom_size = int.from_bytes(header[8:16], "big")
                header_size = 16
            elif atom_size == 0:
                atom_size = size - offset
            if atom_size < header_size or offset + atom_size > size:
                break
            atoms.append((atom_type, offset))
            offset += atom_size
    return atoms


def _decode_fully(ffmpeg: Path, media_path: Path) -> tuple[bool, str]:
    with tempfile.TemporaryFile() as error_log:
        try:
            result = subprocess.run(
                [str(ffmpeg), "-v", "error", "-i", str(media_path), "-map", "0:v:0?",
                 "-map", "0:a:0?", "-f", "null", "-"],
                stdout=subprocess.DEVNULL,
                stderr=error_log,
                timeout=600,
                check=False,
            )
            passed = result.returncode == 0
        except subprocess.TimeoutExpired:
            passed = False
        error_log.seek(0, 2)
        length = error_log.tell()
        error_log.seek(max(0, length - 8192))
        detail = error_log.read().decode(errors="replace")
    return passed and not detail.strip(), detail


def _packet_discontinuities(
    ffprobe: Path, media_path: Path, stream: str, should_cancel: Callable[[], bool]
) -> tuple[int, int]:
    command = [
        str(ffprobe), "-v", "error", "-select_streams", stream,
        "-show_entries", "packet=pts_time,dts_time", "-of", "csv=p=0",
        str(media_path),
    ]
    with tempfile.TemporaryFile() as error_log:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=error_log, text=True,
        )
        assert process.stdout is not None
        previous_pts: float | None = None
        previous_dts: float | None = None
        pts_count = 0
        dts_count = 0
        started = time.monotonic()
        try:
            for line in process.stdout:
                if should_cancel():
                    process.terminate()
                    raise TechnicalQualityCancelled("quality gate cancelado")
                if time.monotonic() - started > 600:
                    process.kill()
                    raise TechnicalQualityError("ffprobe excedeu o timeout")
                values = line.strip().split(",")
                for index, previous_name in ((0, "pts"), (1, "dts")):
                    if index >= len(values) or values[index] in {"", "N/A"}:
                        continue
                    try:
                        current = float(values[index])
                    except ValueError:
                        continue
                    previous = previous_pts if previous_name == "pts" else previous_dts
                    tolerance = (
                        PTS_BACKWARD_TOLERANCE_SECONDS
                        if previous_name == "pts" else DTS_BACKWARD_TOLERANCE_SECONDS
                    )
                    if previous is not None and current < previous - tolerance:
                        if previous_name == "pts":
                            pts_count += 1
                        else:
                            dts_count += 1
                    if previous_name == "pts":
                        previous_pts = current
                    else:
                        previous_dts = current
            return_code = process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
        if return_code != 0:
            raise TechnicalQualityError(f"ffprobe de packets falhou para {stream}")
    return pts_count, dts_count


def _analyze_audio_pcm(
    ffmpeg: Path, media_path: Path, should_cancel: Callable[[], bool]
) -> tuple[float, int, int, float | None]:
    process = subprocess.Popen(
        [str(ffmpeg), "-v", "error", "-i", str(media_path), "-map", "0:a:0",
         "-ac", "2", "-ar", "48000", "-f", "f32le", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    peak = 0.0
    clipped = 0
    jumps = 0
    previous = [0.0, 0.0]
    sum_left = sum_right = sum_left_sq = sum_right_sq = sum_cross = 0.0
    count = 0
    started = time.monotonic()
    try:
        while chunk := process.stdout.read(64 * 1024):
            if should_cancel():
                process.terminate()
                raise TechnicalQualityCancelled("quality gate cancelado")
            if time.monotonic() - started > 600:
                process.kill()
                raise TechnicalQualityError("decode PCM excedeu o timeout")
            usable = len(chunk) - (len(chunk) % 8)
            for left, right in struct.iter_unpack("<ff", chunk[:usable]):
                peak = max(peak, abs(left), abs(right))
                clipped += int(abs(left) >= CLIPPING_SAMPLE_THRESHOLD)
                clipped += int(abs(right) >= CLIPPING_SAMPLE_THRESHOLD)
                jumps += int(abs(left - previous[0]) >= WAVEFORM_JUMP_THRESHOLD)
                jumps += int(abs(right - previous[1]) >= WAVEFORM_JUMP_THRESHOLD)
                previous[0], previous[1] = left, right
                sum_left += left
                sum_right += right
                sum_left_sq += left * left
                sum_right_sq += right * right
                sum_cross += left * right
                count += 1
        return_code = process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    if return_code != 0:
        raise TechnicalQualityError("decode PCM falhou")
    correlation = None
    if count:
        covariance = sum_cross - (sum_left * sum_right / count)
        variance_left = sum_left_sq - (sum_left * sum_left / count)
        variance_right = sum_right_sq - (sum_right * sum_right / count)
        denominator = math.sqrt(max(0.0, variance_left * variance_right))
        if denominator > 0:
            correlation = max(-1.0, min(1.0, covariance / denominator))
    return peak, clipped, jumps, correlation


def _stream_duration(stream: dict[str, Any]) -> float | None:
    value = stream.get("duration")
    if value in (None, "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def measure_technical_quality(
    *, ffmpeg: Path, ffprobe: Path, media_path: Path, probe: dict[str, Any],
    should_cancel: Callable[[], bool],
) -> tuple[RenderTechnicalQualityReport, list[str]]:
    issues: list[str] = []
    atoms = _top_level_mp4_atoms(media_path)
    offsets = {name: offset for name, offset in atoms}
    moov_offset = offsets.get("moov")
    mdat_offset = offsets.get("mdat")
    faststart = moov_offset is not None and mdat_offset is not None and moov_offset < mdat_offset
    if not faststart:
        issues.append("faststart_missing")

    decode_ok, _decode_detail = _decode_fully(ffmpeg, media_path)
    if not decode_ok:
        issues.append("full_decode_failed")

    pts_count = dts_count = 0
    streams = probe.get("streams", [])
    for selector in ("v:0", "a:0"):
        if any(item.get("codec_type") == ("video" if selector[0] == "v" else "audio") for item in streams):
            try:
                pts, dts = _packet_discontinuities(ffprobe, media_path, selector, should_cancel)
                pts_count += pts
                dts_count += dts
            except TechnicalQualityCancelled:
                raise
            except TechnicalQualityError:
                issues.append("timestamp_analysis_failed")
    if pts_count:
        issues.append("pts_discontinuity_detected")
    if dts_count:
        issues.append("dts_discontinuity_detected")

    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    video_duration = _stream_duration(video) if video else None
    audio_duration = _stream_duration(audio) if audio else None
    sync_delta = (
        abs(video_duration - audio_duration)
        if video_duration is not None and audio_duration is not None else None
    )
    if sync_delta is not None and sync_delta > AV_SYNC_TOLERANCE_SECONDS:
        issues.append("av_sync_out_of_tolerance")

    channels = int(audio.get("channels", 0)) if audio else None
    peak = correlation = None
    clipped = jumps = 0
    if audio:
        if channels is None or channels < 1 or channels > MAX_AUDIO_CHANNELS:
            issues.append("audio_channels_invalid")
        try:
            peak, clipped, jumps, correlation = _analyze_audio_pcm(
                ffmpeg, media_path, should_cancel
            )
            if clipped:
                issues.append("audio_clipping_detected")
            if jumps:
                issues.append("audio_waveform_jump_detected")
            if correlation is not None and correlation <= INVERTED_PHASE_CORRELATION:
                issues.append("audio_channels_inverted")
        except TechnicalQualityCancelled:
            raise
        except TechnicalQualityError:
            issues.append("audio_analysis_failed")

    report = RenderTechnicalQualityReport(
        full_decode_passed=decode_ok,
        faststart=faststart,
        moov_offset=moov_offset,
        mdat_offset=mdat_offset,
        pts_discontinuity_count=pts_count,
        dts_discontinuity_count=dts_count,
        av_sync_delta_seconds=round(sync_delta, 6) if sync_delta is not None else None,
        audio_channel_count=channels,
        audio_peak_amplitude=round(peak, 6) if peak is not None else None,
        clipped_sample_count=clipped,
        waveform_jump_count=jumps,
        channel_phase_correlation=round(correlation, 6) if correlation is not None else None,
        thresholds={
            "pts_backward_tolerance_seconds": PTS_BACKWARD_TOLERANCE_SECONDS,
            "dts_backward_tolerance_seconds": DTS_BACKWARD_TOLERANCE_SECONDS,
            "av_sync_tolerance_seconds": AV_SYNC_TOLERANCE_SECONDS,
            "clipping_sample_threshold": CLIPPING_SAMPLE_THRESHOLD,
            "waveform_jump_threshold": WAVEFORM_JUMP_THRESHOLD,
            "maximum_audio_channels": MAX_AUDIO_CHANNELS,
            "inverted_phase_correlation": INVERTED_PHASE_CORRELATION,
        },
        engine={"ffmpeg": str(ffmpeg), "ffprobe": str(ffprobe), "mode": "software"},
    )
    return report, issues
