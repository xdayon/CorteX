from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from asgi_client import ASGITestClient as TestClient

from cortex.api import create_app
from cortex.config import load_config
from cortex.domain.models import SourceAsset, SourceKind
from cortex.domain.store import DomainStore
from cortex.ingest.ffprobe import probe_media


def _fixture(tmp_path: Path):
    base = load_config()
    config = base.model_copy(update={
        "paths": base.paths.model_copy(update={
            "data_dir": tmp_path,
            "projects_dir": tmp_path / "projects",
            "cache_dir": tmp_path / "cache",
            "output_dir": tmp_path / "output",
            "database": tmp_path / "cortex.sqlite3",
        }),
    })
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Preview fixture")
    source_path = config.paths.projects_dir / project.id / "source" / "clip.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(config.render.ffmpeg), "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        str(source_path),
    ], check=True, timeout=30)
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id,
        kind=SourceKind.UPLOAD,
        original_filename="clip.mp4",
        stored_path=str(source_path.relative_to(config.paths.data_dir)),
        sha256=source_sha,
        size_bytes=source_path.stat().st_size,
        probe=probe_media(config.render.ffprobe, source_path),
    ))
    return config, domain, project, source


def test_source_preview_is_a_real_jpeg(tmp_path: Path) -> None:
    config, _domain, project, source = _fixture(tmp_path)
    client = TestClient(create_app(config))
    source_response = client.get(
        f"/api/v1/projects/{project.id}/sources/{source.id}/preview?time_seconds=0.5"
    )
    assert source_response.status_code == 200, source_response.text
    assert source_response.headers["content-type"] == "image/jpeg"
    assert source_response.content.startswith(b"\xff\xd8")
