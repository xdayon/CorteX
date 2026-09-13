from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np


class FrameSampleCache:
    """Content-addressed, cross-stage cache for reduced RGB frame samples."""

    def __init__(
        self, *, directory: Path, source_sha256: str, width: int,
        decoder_version: str, extractor: Callable[[float], np.ndarray],
    ) -> None:
        self.directory = directory
        self.source_sha256 = source_sha256
        self.width = width
        self.decoder_version = decoder_version
        self.extractor = extractor

    def _path(self, timestamp: float) -> Path:
        payload = json.dumps({
            "version": 1, "source": self.source_sha256, "width": self.width,
            "decoder": self.decoder_version, "timestamp": round(timestamp, 3),
        }, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return self.directory / f"{digest}.npz"

    def get(self, timestamp: float) -> tuple[np.ndarray, bool]:
        path = self._path(timestamp)
        if path.is_file():
            try:
                with np.load(path, allow_pickle=False) as saved:
                    frame = saved["frame"]
                if frame.dtype == np.uint8 and frame.ndim == 3 and frame.shape[2] == 3:
                    return frame, True
            except (OSError, ValueError, KeyError):
                path.unlink(missing_ok=True)
        frame = self.extractor(timestamp)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as output:
            np.savez_compressed(output, frame=frame)
        temporary.replace(path)
        return frame, False
