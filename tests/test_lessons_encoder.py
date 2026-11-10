from __future__ import annotations

import pytest
from pydantic import ValidationError

import cortex.render.service as render_service
from lessons_media import small_config

from cortex.config import LessonsConfig
from cortex.lessons.common import LessonError, video_encode


def test_cpu_x264_is_grain_tuned_for_the_final(tmp_path):
    cfg = small_config(tmp_path)
    assert cfg.lessons.encoder == "libx264"
    piece, final = video_encode(cfg), video_encode(cfg, final=True)
    assert piece[:2] == ["-c:v", "libx264"] and "14" in map(str, piece)
    assert final[:2] == ["-c:v", "libx264"] and "grain" in final and "19" in map(str, final)
    assert piece[-2:] == final[-2:] == ["-pix_fmt", "yuv420p"]


def test_nvenc_when_the_encoder_opens(tmp_path, monkeypatch):
    monkeypatch.setattr(render_service, "nvenc_unavailable_reason", lambda ffmpeg: None)
    cfg = small_config(tmp_path, encoder="h264_nvenc")
    piece, final = video_encode(cfg), video_encode(cfg, final=True)
    assert piece[:2] == final[:2] == ["-c:v", "h264_nvenc"]
    assert piece[piece.index("-cq") + 1] == 14 and final[final.index("-cq") + 1] == 19


def test_nvenc_that_does_not_open_fails_instead_of_falling_back(tmp_path, monkeypatch):
    monkeypatch.setattr(render_service, "nvenc_unavailable_reason",
                        lambda ffmpeg: "Driver does not support the required nvenc API version")
    cfg = small_config(tmp_path, encoder="h264_nvenc")
    with pytest.raises(LessonError, match="cannot open it: Driver does not support"):
        video_encode(cfg)


def test_unknown_encoder_is_rejected():
    with pytest.raises(ValidationError):
        LessonsConfig(encoder="hevc_vaapi")
