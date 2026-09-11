from pathlib import Path

import pytest

from asgi_client import ASGITestClient
from cortex.api import create_app
from cortex.config import CortexConfig


def studio_config(tmp_path: Path, build: Path | None) -> CortexConfig:
    return CortexConfig.model_validate({
        "app": {"studio_dir": build},
        "paths": {"data_dir": tmp_path / "data", "projects_dir": tmp_path / "projects",
                  "cache_dir": tmp_path / "cache", "output_dir": tmp_path / "output",
                  "database": tmp_path / "db.sqlite"},
    })


def test_built_studio_and_api_share_origin_without_exposing_private_files(tmp_path):
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text("<html>CorteX Studio</html>")
    (build / "app.js").write_text("const studio = true;")
    secret = tmp_path / "private.txt"
    secret.write_text("private media metadata")
    (build / "escape.txt").symlink_to(secret)
    client = ASGITestClient(create_app(studio_config(tmp_path, build)))
    assert "CorteX Studio" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/v1/health").status_code == 200
    for path in ("/api/v1/missing", "/private.txt", "/escape.txt", "/%2e%2e/private.txt"):
        response = client.get(path)
        assert response.status_code == 404
        assert "private media metadata" not in response.text
        assert "CorteX Studio" not in response.text


def test_missing_build_fails_explicitly_and_api_only_mode_still_works(tmp_path):
    with pytest.raises(ValueError, match="Studio não compilado"):
        create_app(studio_config(tmp_path, tmp_path / "missing"))
    client = ASGITestClient(create_app(studio_config(tmp_path, None)))
    assert client.get("/").status_code == 404
    assert client.get("/api/v1/health").status_code == 200
