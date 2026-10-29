"""Cut a product photo out of its flat studio background (border flood fill, so light print areas stay)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from cortex.config import CortexConfig
from cortex.lessons.common import LessonError

KEY_TOLERANCE = 40  # max channel distance from the background colour
EDGE_ERODE_PX = 3  # shrink the opaque edge past the anti-aliased fringe


def _neighbours_min(a: np.ndarray) -> np.ndarray:
    out = a.copy()
    out[1:] = np.minimum(out[1:], a[:-1])
    out[:-1] = np.minimum(out[:-1], a[1:])
    out[:, 1:] = np.minimum(out[:, 1:], a[:, :-1])
    out[:, :-1] = np.minimum(out[:, :-1], a[:, 1:])
    return out


def background_mask(rgb: np.ndarray, key: str) -> np.ndarray:
    """True where a pixel is the key colour AND connected to the image border through key-coloured pixels."""
    colour = np.array([int(key[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.int16)
    near = np.abs(rgb.astype(np.int16) - colour).max(axis=2) <= KEY_TOLERANCE
    bg = np.zeros_like(near)
    bg[0], bg[-1], bg[:, 0], bg[:, -1] = near[0], near[-1], near[:, 0], near[:, -1]
    while True:
        grown = bg.copy()
        grown[1:] |= bg[:-1]
        grown[:-1] |= bg[1:]
        grown[:, 1:] |= bg[:, :-1]
        grown[:, :-1] |= bg[:, 1:]
        grown &= near
        if (grown == bg).all():
            return bg
        bg = grown


def cutout(cfg: CortexConfig, src: Path, key: str, out: Path) -> Path:
    res = subprocess.run([str(cfg.render.ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(src)], capture_output=True, text=True, timeout=60)
    try:
        w, h = (int(v) for v in res.stdout.strip().split(","))
    except ValueError as exc:
        raise LessonError(f"{src.name}: not a readable image") from exc
    raw = subprocess.run([str(cfg.render.ffmpeg), "-v", "error", "-i", str(src), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=60).stdout
    rgb = np.frombuffer(raw[: w * h * 3], dtype=np.uint8).reshape(h, w, 3)
    alpha = np.where(background_mask(rgb, key), 0, 255).astype(np.uint8)
    for _ in range(EDGE_ERODE_PX):
        alpha = _neighbours_min(alpha)
    rgba = np.dstack([rgb, alpha])
    subprocess.run([str(cfg.render.ffmpeg), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s",
                    f"{w}x{h}", "-i", "-", "-vf", "boxblur=luma_radius=0:chroma_radius=0:alpha_radius=1",
                    str(out)], input=rgba.tobytes(), check=True, timeout=60)
    return out
