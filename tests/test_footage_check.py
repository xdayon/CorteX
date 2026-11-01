"""Borrowed footage is decoded and matched to the camera groups by appearance (real FFmpeg)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.analyze.camera_map import CameraGroup, CameraMapDocument, CameraShot
from cortex.analyze.footage_check import FootageChecker
from cortex.paths import cache_dir

from test_render import _config

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg required")


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    """Bars 0-2, pattern 2-4, bars 4-6, colour bands 6-8: keyframes only at 0, 2 and 4, so the
    cut to the bands at 6 s is invisible to a keyframe-only map."""
    path = tmp_path_factory.mktemp("footage") / "cuts.mp4"
    inputs = ["smptebars=s=320x180:r=25:d=2", "testsrc2=s=320x180:r=25:d=2", "smptebars=s=320x180:r=25:d=2",
              "rgbtestsrc=s=320x180:r=25:d=2"]
    command = [FFMPEG, "-y", "-v", "error"]
    for item in inputs:
        command += ["-f", "lavfi", "-t", "2", "-i", item]
    command += ["-filter_complex", "[0][1][2][3]concat=n=4:v=1:a=0,format=yuv420p", "-c:v", "libx264", "-g", "1000",
                "-sc_threshold", "0", "-force_key_frames", "0,2,4", str(path)]
    subprocess.run(command, check=True, timeout=120)
    return path


def test_frames_land_on_the_group_that_looks_alike_and_results_are_cached(tmp_path, video, monkeypatch):
    config = _config(tmp_path)
    thumbs = cache_dir(config, "p") / "camera_map" / "h"
    thumbs.mkdir(parents=True)
    groups = []
    for group, at in ((0, 0.5), (1, 2.5), (2, 6.5)):
        path = thumbs / f"g{group}.jpg"
        subprocess.run([FFMPEG, "-v", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1", str(path)], check=True)
        groups.append(CameraGroup(id=group, sample_time=at, airtime_seconds=2, airtime_share=0.3, shot_count=1,
                                  median_shot_seconds=2, faces=1, thumbnail_path=str(path.relative_to(tmp_path))))
    # The map believes 4-8 s is one shot of bars (the cut to the bands at 6 s has no keyframe).
    document = CameraMapDocument(algorithm_version="1", project_id="p", source_asset_id="s", source_sha256="x",
                                 duration_seconds=8, keyframe_count=3, groups=groups,
                                 shots=[CameraShot(start=0, end=2, group=0), CameraShot(start=2, end=4, group=1),
                                        CameraShot(start=4, end=8, group=0)])
    checker = FootageChecker(config, document, "map", video)
    assert checker.groups([(4.2, 1.5), (2.2, 1.5), (5.2, 1.6)]) == [[0, 0, 0], [1, 1, 1], [0, 2, 2]]
    verify = checker.verifier({0: "interviewers", 1: "subject", 2: "wide"})
    assert verify([("reaction", 4.2, 1.5), ("reaction", 2.2, 1.5), ("reaction", 5.2, 1.6), ("wide", 6.2, 1.5)]) == [
        True, False, False, True]
    # Cached per map: another checker answers without decoding, and new roles apply.
    monkeypatch.setattr(FootageChecker, "_decode", lambda *_a: pytest.fail("decoded again"))
    again = FootageChecker(config, document, "map", video)
    assert again.verifier({0: "subject", 2: "wide"})([("reaction", 4.2, 1.5), ("wide", 6.2, 1.5)]) == [False, True]


def test_without_thumbnails_nothing_borrowed_is_confirmed(tmp_path, video):
    document = CameraMapDocument(algorithm_version="1", project_id="p", source_asset_id="s", source_sha256="x",
                                 duration_seconds=8, keyframe_count=1, shots=[CameraShot(start=0, end=8, group=0)],
                                 groups=[CameraGroup(id=0, sample_time=0, airtime_seconds=8, airtime_share=1, shot_count=1,
                                                     median_shot_seconds=8, faces=1, thumbnail_path="missing.jpg")])
    checker = FootageChecker(_config(tmp_path), document, "map", Path(video))
    assert checker.verifier({0: "interviewers"})([("reaction", 1.0, 1.5)]) == [False]
