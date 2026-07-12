from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from cortex.ingest.ffprobe import duration_seconds, probe_media


class NormalizeError(RuntimeError):
    pass


@dataclass(frozen=True)
class NormalizedAudio:
    path: Path
    duration_seconds: float
    cached: bool


def extract_normalized_audio(
    ffmpeg: str | Path,
    ffprobe: str | Path,
    source_path: str | Path,
    *,
    source_sha256: str,
    cache_dir: Path,
) -> NormalizedAudio:
    """Extract a mono 16 kHz PCM s16le WAV from `source_path`, once per source.

    Cached under `cache_dir/audio-<sha256-12>.wav`. This is the sole input to
    the transcription engine — clips are never re-extracted per segment.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    audio_path = cache_dir / f"audio-{source_sha256[:12]}.wav"
    if audio_path.exists() and audio_path.stat().st_size > 0:
        probe = probe_media(ffprobe, audio_path)
        return NormalizedAudio(path=audio_path, duration_seconds=duration_seconds(probe), cached=True)

    tmp_path = audio_path.with_suffix(".wav.tmp")
    command = [
        str(ffmpeg), "-y",
        "-i", str(source_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-f", "wav",
        "-acodec", "pcm_s16le",
        str(tmp_path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        tmp_path.unlink(missing_ok=True)
        raise NormalizeError(f"ffmpeg não pôde ser executado: {exc}") from exc
    if completed.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        stderr = (completed.stderr or "").strip()[-2000:]
        raise NormalizeError(f"ffmpeg falhou ao normalizar áudio ({completed.returncode}): {stderr}")

    tmp_path.replace(audio_path)
    probe = probe_media(ffprobe, audio_path)
    return NormalizedAudio(path=audio_path, duration_seconds=duration_seconds(probe), cached=False)
