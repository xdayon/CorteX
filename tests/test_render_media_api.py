from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import StageArtifact
from cortex.render.schemas import (
    RenderDocument,
    RenderEngineInfo,
    RenderQualityPublicationReport,
    RenderQualityReport,
)


def _config(tmp_path: Path) -> CortexConfig:
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


def _document(project_id: str, output_path: Path) -> RenderDocument:
    return RenderDocument(
        project_id=project_id,
        source_asset_id="source",
        edit_plan_artifact_id="plan",
        input_hash="a" * 64,
        output_path=str(output_path),
        output_sha256="b" * 64,
        output_size_bytes=max(1, output_path.stat().st_size if output_path.exists() else 1),
        timeline_duration_seconds=1.0,
        segment_count=1,
        engine=RenderEngineInfo(
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
            requested_encoder="libx264",
            effective_encoder="libx264",
            width=1080,
            height=1920,
            fps=30,
        ),
        quality=RenderQualityReport(
            passed=True,
            expected_duration_seconds=1.0,
            actual_duration_seconds=1.0,
            duration_delta_seconds=0.0,
            has_video=True,
            has_audio=True,
            issues=[],
        ),
    )


def _media_endpoint(app: FastAPI) -> Any:
    route = next(
        route
        for route in app.routes
        if route.path == "/api/v1/projects/{project_id}/renders/{artifact_id}/media"
    )
    return route.endpoint


def _subtitles_endpoint(app: FastAPI) -> Any:
    route = next(
        route
        for route in app.routes
        if route.path == "/api/v1/projects/{project_id}/renders/{artifact_id}/subtitles"
    )
    return route.endpoint


def _report_endpoint(app: FastAPI) -> Any:
    route = next(
        route
        for route in app.routes
        if route.path == "/api/v1/projects/{project_id}/renders/{artifact_id}/report"
    )
    return route.endpoint


def _publication() -> RenderQualityPublicationReport:
    return RenderQualityPublicationReport(
        publish_ready=False,
        reasons=["full_decode_failed"],
        loudness={"target_lufs": -14.0},
        visual={"visual_analysis_performed": True},
        captions={"cue_count": 0},
        safe_zones={"version": 1},
        encoder={"requested": "libx264", "effective": "libx264"},
        dimensions={"width": 1080, "height": 1920},
        hashes={"input_hash": "a" * 64},
        provenance={"ffmpeg": "ffmpeg"},
    )


def test_render_media_is_served_only_from_its_project_render_directory(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = create_app(config)
    domain = app.state.domain
    project = domain.create_project("Media")
    other_project = domain.create_project("Other")
    render_dir = config.paths.projects_dir / project.id / "renders"
    render_dir.mkdir(parents=True)
    media_path = render_dir / "result.mp4"
    media_path.write_bytes(b"fake-mp4")
    manifest_path = render_dir / "result.json"
    manifest_path.write_text(_document(project.id, media_path).model_dump_json(), encoding="utf-8")
    artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id,
        stage="render",
        path=str(manifest_path),
        input_hash="render-input",
    ))
    endpoint = _media_endpoint(app)

    response = endpoint(project.id, artifact.id)
    assert isinstance(response, FileResponse)
    assert Path(response.path).read_bytes() == b"fake-mp4"
    assert response.media_type == "video/mp4"
    assert response.headers["content-disposition"].endswith(
        f'filename="cortex-render-{artifact.id[:12]}.mp4"'
    )

    with pytest.raises(HTTPException) as exc_info:
        endpoint(other_project.id, artifact.id)
    assert exc_info.value.status_code == 404

    wrong_stage = domain.create_stage_artifact(StageArtifact(
        project_id=project.id,
        stage="edit_plan",
        path=str(manifest_path),
        input_hash="wrong-stage",
    ))
    with pytest.raises(HTTPException) as exc_info:
        endpoint(project.id, wrong_stage.id)
    assert exc_info.value.status_code == 404

    missing_manifest = domain.create_stage_artifact(StageArtifact(
        project_id=project.id,
        stage="render",
        path=str(render_dir / "missing.json"),
        input_hash="missing-manifest",
    ))
    with pytest.raises(HTTPException) as exc_info:
        endpoint(project.id, missing_manifest.id)
    assert exc_info.value.status_code == 410

    outside_media = tmp_path / "outside.mp4"
    outside_media.write_bytes(b"secret")
    manifest_path.write_text(
        _document(project.id, outside_media).model_dump_json(), encoding="utf-8"
    )
    with pytest.raises(HTTPException) as exc_info:
        endpoint(project.id, artifact.id)
    assert exc_info.value.status_code == 410

    manifest_path.write_text(
        _document(project.id, render_dir / "gone.mp4").model_dump_json(), encoding="utf-8"
    )
    with pytest.raises(HTTPException) as exc_info:
        endpoint(project.id, artifact.id)
    assert exc_info.value.status_code == 410


def test_render_quality_report_is_downloadable_for_blocked_artifact(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = create_app(config)
    domain = app.state.domain
    project = domain.create_project("Report")
    render_dir = config.paths.projects_dir / project.id / "renders"
    render_dir.mkdir(parents=True)
    media_path = render_dir / "blocked.mp4"
    media_path.write_bytes(b"preview")
    document = _document(project.id, media_path).model_copy(update={
        "quality": _document(project.id, media_path).quality.model_copy(update={
            "passed": False,
            "issues": ["full_decode_failed"],
        }),
        "publication": _publication(),
    })
    manifest_path = render_dir / "blocked.json"
    manifest_path.write_text(document.model_dump_json(), encoding="utf-8")
    artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id,
        stage="render",
        path=str(manifest_path),
        input_hash="blocked-report",
    ))

    response = _report_endpoint(app)(project.id, artifact.id)

    assert isinstance(response, FileResponse)
    assert Path(response.path) == manifest_path
    assert response.media_type == "application/json"
    assert "cortex-quality-report" in response.headers["content-disposition"]


def test_render_subtitles_are_served_only_from_project_render_directory(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = create_app(config)
    domain = app.state.domain
    project = domain.create_project("Subtitles")
    render_dir = config.paths.projects_dir / project.id / "renders"
    render_dir.mkdir(parents=True)
    media_path = render_dir / "result.mp4"
    media_path.write_bytes(b"fake-mp4")
    subtitles_path = render_dir / "result.srt"
    subtitles_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nLegenda real\n", encoding="utf-8"
    )
    document = _document(project.id, media_path).model_copy(update={
        "subtitles_path": str(subtitles_path),
        "subtitles_sha256": "c" * 64,
    })
    manifest_path = render_dir / "result.json"
    manifest_path.write_text(document.model_dump_json(), encoding="utf-8")
    artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="render", path=str(manifest_path), input_hash="render-srt",
    ))

    response = _subtitles_endpoint(app)(project.id, artifact.id)

    assert isinstance(response, FileResponse)
    assert response.media_type == "application/x-subrip"
    assert Path(response.path).read_text(encoding="utf-8").endswith("Legenda real\n")
    assert response.headers["content-disposition"].endswith(
        f'filename="cortex-render-{artifact.id[:12]}.srt"'
    )

    outside = tmp_path / "outside.srt"
    outside.write_text("secret", encoding="utf-8")
    manifest_path.write_text(
        document.model_copy(update={"subtitles_path": str(outside)}).model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(HTTPException) as exc_info:
        _subtitles_endpoint(app)(project.id, artifact.id)
    assert exc_info.value.status_code == 410
