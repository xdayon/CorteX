from __future__ import annotations

import numpy as np

from cortex.analyze.frame_cache import FrameSampleCache


def test_frame_samples_are_reused_across_service_instances(tmp_path) -> None:
    calls: list[float] = []

    def extract(timestamp: float) -> np.ndarray:
        calls.append(timestamp)
        return np.full((2, 3, 3), round(timestamp), dtype=np.uint8)

    options = {
        "directory": tmp_path, "source_sha256": "a" * 64,
        "width": 640, "decoder_version": "ffmpeg-1", "extractor": extract,
    }
    first, was_cached = FrameSampleCache(**options).get(1.25)
    second, was_cached_again = FrameSampleCache(**options).get(1.25)

    assert not was_cached
    assert was_cached_again
    assert calls == [1.25]
    assert np.array_equal(first, second)


def test_decoder_version_invalidates_frame_sample(tmp_path) -> None:
    calls = 0

    def extract(_timestamp: float) -> np.ndarray:
        nonlocal calls
        calls += 1
        return np.zeros((1, 1, 3), dtype=np.uint8)

    for version in ("ffmpeg-1", "ffmpeg-2"):
        FrameSampleCache(
            directory=tmp_path, source_sha256="b" * 64, width=640,
            decoder_version=version, extractor=extract,
        ).get(2.0)
    assert calls == 2
