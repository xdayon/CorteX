from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from cortex.analyze.face_detect import FaceDetectionError

FRAME_EXTRACTION_WIDTH = 640


def _parse_ppm(data: bytes) -> np.ndarray:
    """Parse a P6 (binary RGB) PPM buffer, as produced by ffmpeg's ppm muxer.

    PPM is a trivial, dependency-free way to learn the exact width/height
    ffmpeg picked for ``scale=640:-2`` (which rounds to the nearest even
    number) without guessing at swscale's rounding rules ourselves.
    """
    if not data.startswith(b"P6"):
        raise FaceDetectionError("saída do ffmpeg não é um PPM (P6) válido")
    idx = 2
    values: list[int] = []
    while len(values) < 3:
        while idx < len(data) and data[idx : idx + 1].isspace():
            idx += 1
        if idx < len(data) and data[idx : idx + 1] == b"#":
            while idx < len(data) and data[idx : idx + 1] != b"\n":
                idx += 1
            continue
        start = idx
        while idx < len(data) and not data[idx : idx + 1].isspace():
            idx += 1
        if start == idx:
            raise FaceDetectionError("cabeçalho PPM malformado")
        values.append(int(data[start:idx]))
    idx += 1
    width, height, _maxval = values
    pixel_bytes = data[idx : idx + width * height * 3]
    if len(pixel_bytes) != width * height * 3:
        raise FaceDetectionError("dados de pixel PPM truncados")
    return np.frombuffer(pixel_bytes, dtype=np.uint8).reshape(height, width, 3)


def _extract_frame_rgb(
    ffmpeg: str | Path, source_path: Path, timestamp: float, width: int = FRAME_EXTRACTION_WIDTH,
) -> np.ndarray:
    command = [
        str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, timestamp):.3f}", "-i", str(source_path),
        "-frames:v", "1", "-vf", f"scale={width}:-2",
        "-pix_fmt", "rgb24", "-f", "image2pipe", "-vcodec", "ppm", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FaceDetectionError(f"extração de frame via ffmpeg falhou: {exc}") from exc
    if result.returncode != 0 or not result.stdout:
        detail = (result.stderr or b"").decode(errors="replace")[-2000:]
        raise FaceDetectionError(f"extração de frame via ffmpeg falhou ({result.returncode}): {detail}")
    return _parse_ppm(result.stdout)
