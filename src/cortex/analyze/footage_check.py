"""Checks that borrowed footage shows the camera the map says it does.

The camera map groups keyframes, and a camera cut without a keyframe (common in YouTube
masters) leaves part of a mapped shot showing another camera: a "reaction" could be the
subject himself. Before borrowed footage is suggested, a few small frames of its window
are decoded and matched to the nearest camera group by appearance (the same features the
map clusters on, taken from the group thumbnails); every frame must land on a group with
the expected role. Only the chosen candidates are decoded, in parallel, and the nearest
groups are cached per camera map, so a later role change needs no decoding.
"""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from cortex.analyze.camera_map import FRAME_HEIGHT, FRAME_WIDTH, CameraMapDocument, frame_features, thumbnail_file
from cortex.config import CortexConfig
from cortex.paths import cache_dir

FOOTAGE_CHECK_VERSION = "1"
SAMPLE_FPS = 2.0            # 3-5 frames for a 1.2-2.5 s insert
EDGE = 0.1                  # skip the very first/last frame of the window
WORKERS = 4
TIMEOUT_SECONDS = 60
EXPECTED_ROLE = {"reaction": "interviewers", "wide": "wide"}


def _load_thumbnail(path: Path) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as image:
        return np.asarray(image.convert("RGB").resize((FRAME_WIDTH, FRAME_HEIGHT), Image.BILINEAR))


class FootageChecker:
    def __init__(self, config: CortexConfig, document: CameraMapDocument, artifact_id: str, media_path: Path):
        self._ffmpeg = str(config.render.ffmpeg)
        self._media = media_path
        self._references: list[tuple[int, np.ndarray]] = []
        for group in document.groups:
            thumbnail = thumbnail_file(config, document.project_id, group)
            if thumbnail is not None:
                self._references.append((group.id, frame_features(_load_thumbnail(thumbnail))))
        self._cache_path = cache_dir(config, document.project_id) / "scene_footage" / f"{artifact_id}-v{FOOTAGE_CHECK_VERSION}.json"
        try:
            self._cache: dict[str, list[int]] = json.loads(self._cache_path.read_text())
        except (OSError, ValueError):
            self._cache = {}

    @staticmethod
    def _key(start: float, duration: float) -> str:
        return f"{start:.3f}+{duration:.3f}"

    def _decode(self, start: float, duration: float) -> list[int]:
        """Nearest group of each sampled frame; empty when nothing could be decoded."""
        command = [
            self._ffmpeg, "-hide_banner", "-loglevel", "error", "-threads", "2",
            "-ss", f"{start + EDGE:.3f}", "-t", f"{max(0.2, duration - 2 * EDGE):.3f}", "-i", str(self._media),
            "-an", "-vf", f"fps={SAMPLE_FPS},scale={FRAME_WIDTH}:{FRAME_HEIGHT}", "-pix_fmt", "rgb24",
            "-f", "rawvideo", "-",
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return []
        size = FRAME_WIDTH * FRAME_HEIGHT * 3
        if result.returncode or len(result.stdout) < size:
            return []
        frames = np.frombuffer(result.stdout[: len(result.stdout) // size * size], dtype=np.uint8)
        nearest = []
        for frame in frames.reshape(-1, FRAME_HEIGHT, FRAME_WIDTH, 3):
            features = frame_features(frame)
            nearest.append(min(self._references, key=lambda ref: float(np.linalg.norm(features - ref[1])))[0])
        return nearest

    def groups(self, windows: Sequence[tuple[float, float]]) -> list[list[int]]:
        """Nearest groups per window (cached; the missing ones decoded in parallel)."""
        if not self._references:
            return [[] for _ in windows]
        missing = sorted({self._key(*w): w for w in windows if self._key(*w) not in self._cache}.items())
        if missing:
            with ThreadPoolExecutor(max_workers=min(WORKERS, len(missing))) as pool:
                for (key, _window), found in zip(missing, pool.map(lambda item: self._decode(*item[1]), missing)):
                    self._cache[key] = found
            self._save()
        return [self._cache.get(self._key(*w), []) for w in windows]

    def _save(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            on_disk = json.loads(self._cache_path.read_text()) if self._cache_path.is_file() else {}
        except (OSError, ValueError):
            on_disk = {}
        merged = {**on_disk, **self._cache}
        temporary = self._cache_path.with_suffix(f".{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(merged, separators=(",", ":")))
            temporary.replace(self._cache_path)
        except OSError:
            temporary.unlink(missing_ok=True)

    def verifier(self, roles: dict[int, str]):
        """The ``verify`` callable of ``suggest_scenes``: True when every frame shows a
        group with the role the insert needs."""
        def verify(items: Sequence[tuple[str, float, float]]) -> list[bool]:
            found = self.groups([(start, duration) for _kind, start, duration in items])
            return [bool(groups) and all(roles.get(g) == EXPECTED_ROLE[kind] for g in groups)
                    for (kind, _start, _duration), groups in zip(items, found)]
        return verify
