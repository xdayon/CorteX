from __future__ import annotations

import pytest
from pydantic import ValidationError

from cortex.api import RenderRequest
from cortex.render.schemas import RenderSettings, RenderSettingsPatch
from cortex.render.service import _merge_render_settings


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
    assert merged.captions.text_color == "#112233"
    assert merged.captions.karaoke_color == base.captions.karaoke_color
    assert merged.captions.animation.style == "none"
    assert merged.headline.text_color == "#445566"
    assert merged.headline.animation.entrance == "none"
    assert merged.headline.animation.exit == "slide"
