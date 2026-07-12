from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cortex.analyze.scene_detect import build_scenes, parse_scene_cuts, scdet_command
from cortex.analyze.scene_schemas import SceneIndexDocument
from cortex.analyze.scene_service import SceneIndexService
from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import JobStatus
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine


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


def _build_multishot_video(path: Path, ffmpeg: str) -> None:
    """Concatenate four visually distinct segments with known cut points.

    Expected cuts (source timeline seconds): ~2.0, ~3.5, ~6.0.
    """
    workdir = path.parent
    seg1, seg2, seg3, seg4 = (workdir / f"seg{i}.mp4" for i in range(1, 5))
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "color=c=red:s=320x240:d=2:r=25", str(seg1),
    ], check=True, timeout=30)
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "color=c=blue:s=320x240:d=1.5:r=25", str(seg2),
    ], check=True, timeout=30)
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "color=c=green:s=320x240:d=2.5:r=25", str(seg3),
    ], check=True, timeout=30)
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc=s=320x240:d=1.8:r=25", str(seg4),
    ], check=True, timeout=30)
    list_path = workdir / "list.txt"
    list_path.write_text(
        "\n".join(f"file '{seg.name}'" for seg in (seg1, seg2, seg3, seg4)), encoding="utf-8"
    )
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(list_path), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True, timeout=30)


# -- 1. Pure parser --------------------------------------------------------

def test_parse_scene_cuts_dedupes_and_sorts_by_time() -> None:
    stderr = """
    [Parsed_scdet_0] lavfi.scd.score: 15.625, lavfi.scd.time: 6.04
    [Parsed_scdet_0] lavfi.scd.score: 15.625, lavfi.scd.time: 2
    [Parsed_scdet_0] lavfi.scd.score: 20.0, lavfi.scd.time: 2
    """
    cuts = parse_scene_cuts(stderr)
    assert cuts == [(2.0, 20.0), (6.04, 15.625)]


def test_build_scenes_creates_contiguous_segments() -> None:
    cuts = [(2.0, 15.6), (3.5, 15.6), (6.0, 27.4)]
    scenes = build_scenes(cuts, duration=7.8)
    assert scenes == [
        {"index": 0, "start": 0.0, "end": 2.0},
        {"index": 1, "start": 2.0, "end": 3.5},
        {"index": 2, "start": 3.5, "end": 6.0},
        {"index": 3, "start": 6.0, "end": 7.8},
    ]


def test_build_scenes_with_no_cuts_returns_single_scene() -> None:
    assert build_scenes([], duration=5.0) == [{"index": 0, "start": 0.0, "end": 5.0}]


# -- 2. Real ffmpeg scdet against a synthetic multi-shot video ------------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_scdet_detects_known_cut_points(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    media_path = tmp_path / "multishot.mp4"
    _build_multishot_video(media_path, ffmpeg)

    result = subprocess.run(
        scdet_command(ffmpeg, media_path, 10.0), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    cuts = parse_scene_cuts(result.stderr)
    cut_times = sorted(time for time, _ in cuts)
    assert len(cut_times) == 3

    expected = [2.0, 3.5, 6.0]
    for expected_time in expected:
        assert any(abs(actual - expected_time) <= 0.2 for actual in cut_times), (
            f"expected a cut near {expected_time}s, got {cut_times}"
        )


# -- 3. Service: detection, persistence and cache hit ----------------------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_scene_index_service_detects_and_caches(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Scene index fixture")

    source_dir = config.paths.projects_dir / project.id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    media_path = source_dir / "multishot.mp4"
    _build_multishot_video(media_path, str(config.render.ffmpeg))

    asset = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="multishot.mp4",
        stored_path=str(media_path.relative_to(config.paths.data_dir)),
        sha256="fake-sha256", size_bytes=media_path.stat().st_size,
    ))

    service = SceneIndexService(config, domain)
    progress: list[float] = []

    first = service.run(
        source_asset=asset, threshold=10.0,
        progress_cb=lambda value, _message: progress.append(value),
        should_cancel=lambda: False,
    )
    assert first["cached"] is False
    assert progress == sorted(progress) and progress[-1] == 100.0

    document = SceneIndexDocument.model_validate_json(Path(first["scene_index_path"]).read_text())
    assert document.schema_version == 1
    assert document.cut_count == 3
    assert document.engine.filter == "scdet"
    assert document.engine.threshold_requested == 10.0
    cut_times = sorted(cut.time for cut in document.cuts)
    for expected_time in (2.0, 3.5, 6.0):
        assert any(abs(actual - expected_time) <= 0.2 for actual in cut_times)
    assert len(domain.list_stage_artifacts(project.id, "scene_index")) == 1

    second = service.run(
        source_asset=asset, threshold=10.0,
        progress_cb=lambda _value, _message: None, should_cancel=lambda: False,
    )
    assert second["cached"] is True
    assert second["scene_index_artifact_id"] == first["scene_index_artifact_id"]
    assert len(domain.list_stage_artifacts(project.id, "scene_index")) == 1


# -- 4. API endpoints --------------------------------------------------------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_scene_index_endpoint_creates_job_and_get_returns_document(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Scene index API fixture")

    source_dir = config.paths.projects_dir / project.id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    media_path = source_dir / "multishot.mp4"
    _build_multishot_video(media_path, str(config.render.ffmpeg))

    asset = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="multishot.mp4",
        stored_path=str(media_path.relative_to(config.paths.data_dir)),
        sha256="fake-sha256", size_bytes=media_path.stat().st_size,
    ))

    client = TestClient(create_app(config))
    response = client.post(
        f"/api/v1/projects/{project.id}/scenes",
        json={"source_asset_id": asset.id, "scene_threshold": 10.0},
    )
    assert response.status_code == 201, response.text
    queued = response.json()
    assert queued["type"] == "scene_analysis" and queued["status"] == "queued"

    jobs = JobStore(config.paths.database)
    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued["id"])
    assert final.status == JobStatus.SUCCEEDED, final.error
    assert final.result is not None and final.result["cached"] is False

    artifact_id = final.result["scene_index_artifact_id"]
    fetched = client.get(f"/api/v1/projects/{project.id}/scenes/{artifact_id}")
    assert fetched.status_code == 200, fetched.text
    payload = fetched.json()
    assert payload["artifact"]["stage"] == "scene_index"
    assert payload["document"]["cut_count"] == 3


def test_scene_index_endpoint_rejects_missing_source(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Scene index missing source")
    asset = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="ghost.mp4",
        stored_path="source/ghost.mp4", sha256="fake-sha256", size_bytes=100,
    ))
    client = TestClient(create_app(config))
    response = client.post(
        f"/api/v1/projects/{project.id}/scenes",
        json={"source_asset_id": asset.id},
    )
    assert response.status_code == 409, response.text
