from pathlib import Path
import hashlib
import shutil
import subprocess
from uuid import uuid4

import numpy as np
import pytest
from pydantic import ValidationError

from cortex.render.schemas import RenderFramingSettings, RenderFramingSettingsPatch, RenderDocument, RenderCanvasSettings, RenderSettingsPatch
from cortex.render.service import RenderService, _merge_render_settings
from test_render import _config
from test_render_jl_cut import _prepare_fixture, _build_plan, _render_settings
from test_render_input_window import _persist_plan


def test_framing_position_defaults_validates_and_merges():
    assert RenderFramingSettings().position_y == .5
    for position in (0, .35, 1):
        settings = _merge_render_settings(_render_settings(), RenderSettingsPatch.model_validate({
            "framing": {"mode": "blurred_background", "position_y": position},
        }))
        assert settings.framing.position_y == position
    for position in (-.01, 1.01, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            RenderFramingSettings(position_y=position)
        with pytest.raises(ValidationError):
            RenderFramingSettingsPatch(position_y=position)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_real_foreground_moves_background_stays_and_settings_are_cached(tmp_path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    config.render.visual_quality_enabled = False  # Static color is an intentional fixture.
    domain, source, transcript = _prepare_fixture(tmp_path, config, color_switch=1, audio_switch=1, total=2)
    source_path = (config.paths.data_dir / source.stored_path).with_name("bands.mp4")
    # Three horizontal bands reveal foreground position against the same blurred background.
    subprocess.run([str(config.render.ffmpeg), "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=red:s=320x180:r=30:d=2,drawbox=x=0:y=60:w=320:h=60:color=lime:t=fill,drawbox=x=0:y=120:w=320:h=60:color=blue:t=fill",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2", "-c:v", "libx264",
        "-preset", "ultrafast", "-c:a", "aac", str(source_path)], check=True, timeout=30)
    source = domain.create_source_asset(source.model_copy(update={
        "id": uuid4().hex, "stored_path": str(source_path.relative_to(config.paths.data_dir)),
        "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(), "size_bytes": source_path.stat().st_size,
    }))
    transcript = domain.create_transcript_artifact(transcript.model_copy(update={
        "id": uuid4().hex, "source_asset_id": source.id,
    }))
    plan = _build_plan(project_id=source.project_id, source_id=source.id, transcript_id=transcript.id,
                       offset_seconds=0, kind="hard", video_cut_seconds=1, total_seconds=2)
    edit = _persist_plan(tmp_path, domain, plan)
    settings = _render_settings().model_copy(update={"canvas": RenderCanvasSettings(width=320, height=568, fps=30)})
    frames, manifests = [], []
    for position in (0, 1):
        settings.framing = RenderFramingSettings(mode="blurred_background", position_y=position)
        arguments = dict(edit_plan_artifact=edit, encoder=None, headline=None, render_settings=settings,
                         progress_cb=lambda *_: None, should_cancel=lambda: False)
        result = RenderService(config, domain).run(**arguments)
        doc = RenderDocument.model_validate_json(Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text())
        assert doc.quality.passed, doc.quality.issues
        assert doc.requested_settings.framing.position_y == position
        assert doc.effective_settings.framing.position_y == position
        raw = subprocess.run([str(config.render.ffmpeg), "-v", "error", "-ss", "0.5", "-i", doc.output_path,
            "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True, timeout=30).stdout
        frames.append(np.frombuffer(raw, dtype=np.uint8).reshape(568, 320, 3).astype(float))
        manifests.append(doc)
        cached = RenderService(config, domain).run(**arguments)
        assert cached["cached"] and cached["render_artifact_id"] == result["render_artifact_id"]
    assert manifests[0].input_hash != manifests[1].input_hash
    # Top foreground's middle band is green; moving it down reveals red background.
    assert frames[0][90, 160, 1] > 220
    assert frames[1][90, 160, 0] > frames[1][90, 160, 1] + 150
    # Background is visible in both renders throughout the middle of the portrait.
    assert np.mean(np.abs(frames[0][220:345] - frames[1][220:345])) < 3
    settings.framing = RenderFramingSettings(mode="vertical_crop", position_y=1)
    result = RenderService(config, domain).run(edit_plan_artifact=edit, encoder=None, headline=None,
        render_settings=settings, progress_cb=lambda *_: None, should_cancel=lambda: False)
    doc = RenderDocument.model_validate_json(Path(domain.get_stage_artifact(result["render_artifact_id"]).path).read_text())
    assert doc.requested_settings.framing.position_y == 1
    assert doc.effective_settings.framing.position_y == .5
