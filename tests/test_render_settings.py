from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cortex.api import RenderRequest
from cortex.config import load_config
from cortex.render.schemas import RenderSettings, RenderSettingsPatch
from cortex.render.service import _export_render_files, _merge_render_settings


def _payload() -> dict:
    return {
        "schema_version": 1,
        "encoder": "libx264",
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "captions": {
            "enabled": True,
            "font_family": "Montserrat",
            "font_size": 32,
            "words_per_cue": 5,
            "outline": True,
            "shadow": True,
            "karaoke": True,
        },
        "headline": {
            "enabled": True,
            "text": "Headline real",
            "font_family": "Montserrat",
            "font_size": 48,
            "duration_seconds": 4,
        },
        "subtitles": {"sidecar_srt": True},
    }


def test_render_settings_are_versioned_and_reject_unsupported_encoder() -> None:
    settings = RenderSettings.model_validate(_payload())
    assert settings.schema_version == 1
    assert settings.framing.schema_version == 1
    assert settings.framing.mode == "vertical_crop"

    invalid = _payload()
    invalid["encoder"] = "h265_nvenc"
    with pytest.raises(ValidationError):
        RenderSettings.model_validate(invalid)

    future = _payload()
    future["schema_version"] = 2
    with pytest.raises(ValidationError):
        RenderSettings.model_validate(future)


def test_render_request_rejects_mixed_legacy_and_versioned_settings() -> None:
    with pytest.raises(ValidationError, match="não pode ser combinado"):
        RenderRequest(
            edit_plan_artifact_id="plan",
            encoder="libx264",
            render_settings=RenderSettings.model_validate(_payload()),
        )


def test_render_request_accepts_explicit_export_directory() -> None:
    request = RenderRequest(
        edit_plan_artifact_id="plan",
        render_settings=RenderSettings.model_validate(_payload()),
        export_directory="cortes/aprovados",
    )
    assert request.export_directory == "cortes/aprovados"


def test_face_static_crop_request_requires_complete_explicit_identity_chain() -> None:
    settings = RenderSettings.model_validate({
        **_payload(), "framing": {"mode": "face_static_crop"},
    })
    with pytest.raises(ValidationError, match="face_static_crop exige"):
        RenderRequest(edit_plan_artifact_id="plan", render_settings=settings)
    with pytest.raises(ValidationError, match="face_static_crop exige"):
        RenderRequest(
            edit_plan_artifact_id="plan", render_settings=settings,
            face_index_artifact_id="faces",
        )
    request = RenderRequest(
        edit_plan_artifact_id="plan", render_settings=settings,
        face_index_artifact_id="faces", identity_index_artifact_id="identities",
        target_identity_id="identity-me",
    )
    assert request.target_identity_id == "identity-me"


def test_export_render_files_copies_video_and_subtitles_atomically(tmp_path: Path) -> None:
    base = load_config()
    config = base.model_copy(update={
        "paths": base.paths.model_copy(update={"output_dir": tmp_path / "exports"}),
    })
    video = tmp_path / "render.mp4"
    subtitles = tmp_path / "render.srt"
    video.write_bytes(b"video")
    subtitles.write_text("subtitle", encoding="utf-8")

    exported = _export_render_files(
        config,
        output_path=video,
        subtitles_path=subtitles,
        export_directory="cliente-a",
        input_hash="a" * 64,
    )

    assert Path(exported["export_path"]).read_bytes() == b"video"
    assert Path(exported["export_subtitles_path"]).read_text(encoding="utf-8") == "subtitle"
    assert Path(exported["export_path"]).parent == (tmp_path / "exports" / "cliente-a").resolve()


def test_render_settings_validate_colors_and_animation_enums() -> None:
    payload = _payload()
    payload["captions"]["text_color"] = "#abcdef"
    payload["captions"]["animation"] = {"style": "pop", "duration_seconds": 0.2}
    payload["headline"]["animation"] = {"entrance": "slide", "exit": "fade", "duration_seconds": 0.3}
    settings = RenderSettings.model_validate(payload)
    assert settings.captions.text_color == "#ABCDEF"
    assert settings.captions.animation.style == "pop"
    assert settings.headline.animation.entrance == "slide"

    payload["captions"]["text_color"] = "blue"
    with pytest.raises(ValidationError):
        RenderSettings.model_validate(payload)

    payload = _payload()
    payload["captions"]["animation"] = {"style": "zoom", "duration_seconds": 0.2}
    with pytest.raises(ValidationError):
        RenderSettings.model_validate(payload)


def test_render_settings_patch_merges_nested_fields_field_by_field() -> None:
    base = RenderSettings.model_validate(_payload())
    patch = RenderSettingsPatch.model_validate({
        "framing": {"mode": "blurred_background"},
        "captions": {
            "text_color": "#112233",
            "animation": {"style": "none", "duration_seconds": 0.15},
        },
        "headline": {
            "text_color": "#445566",
            "animation": {"entrance": "none", "exit": "slide", "duration_seconds": 0.4},
        },
    })
    merged = _merge_render_settings(base, patch)
    assert merged.framing.mode == "blurred_background"
    assert merged.framing.schema_version == 1
    assert merged.captions.text_color == "#112233"
    assert merged.captions.karaoke_color == base.captions.karaoke_color
    assert merged.captions.animation.style == "none"
    assert merged.headline.text_color == "#445566"
    assert merged.headline.animation.entrance == "none"
    assert merged.headline.animation.exit == "slide"
