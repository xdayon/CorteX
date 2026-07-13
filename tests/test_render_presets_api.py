from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from cortex.api import ProjectCreate, RenderPresetCreate, RenderPresetUpdate, create_app
from cortex.config import load_config


def _config(tmp_path: Path):
    base = load_config()
    return base.model_copy(update={
        "paths": base.paths.model_copy(update={
            "data_dir": tmp_path,
            "projects_dir": tmp_path / "projects",
            "cache_dir": tmp_path / "cache",
            "output_dir": tmp_path / "output",
            "database": tmp_path / "cortex.sqlite3",
        }),
    })


def _settings() -> dict:
    return {
        "schema_version": 1,
        "encoder": "libx264",
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "captions": {
            "enabled": True, "font_family": "Montserrat", "font_size": 28,
            "words_per_cue": 5, "outline": True, "shadow": True, "karaoke": True,
            "text_color": "#FFFFFF", "karaoke_color": "#2CE4D7",
            "outline_color": "#071012", "shadow_color": "#000000B8",
            "animation": {"style": "fade", "duration_seconds": 0.18},
        },
        "headline": {
            "enabled": True, "text": "", "font_family": "Montserrat",
            "font_size": 48, "duration_seconds": 4, "burst_color": "#11B9AD",
            "strip_color": "#FFFFFFF5", "text_color": "#071012",
            "animation": {"entrance": "fade", "exit": "fade", "duration_seconds": 0.24},
        },
        "subtitles": {"sidecar_srt": True},
        "template": {"quote_burst_opacity": 1, "quote_strip_opacity": 0.96, "quote_padding": 14},
    }


def _route(app, path: str, method: str):
    return next(route.endpoint for route in app.routes if route.path == path and method in route.methods)


def test_render_presets_are_validated_persisted_and_project_scoped(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path))
    create_project = _route(app, "/api/v1/projects", "POST")
    list_presets = _route(app, "/api/v1/projects/{project_id}/render-presets", "GET")
    create_preset = _route(app, "/api/v1/projects/{project_id}/render-presets", "POST")
    update_preset = _route(app, "/api/v1/projects/{project_id}/render-presets/{preset_id}", "PUT")
    first = create_project(ProjectCreate(name="Primeiro"))
    second = create_project(ProjectCreate(name="Segundo"))

    preset = create_preset(
        first.id, RenderPresetCreate(name="Reels padrão", settings=_settings())
    )
    assert preset.settings["encoder"] == "libx264"
    assert list_presets(first.id) == [preset]
    assert list_presets(second.id) == []

    updated_settings = _settings()
    updated_settings["captions"]["font_size"] = 40
    updated = update_preset(
        first.id,
        preset.id,
        RenderPresetUpdate(name="Reels grande", settings=updated_settings),
    )
    assert updated.name == "Reels grande"
    assert updated.settings["captions"]["font_size"] == 40
    with pytest.raises(HTTPException, match="Preset não encontrado") as error:
        update_preset(second.id, preset.id, RenderPresetUpdate(name="Não deve vazar"))
    assert error.value.status_code == 404
