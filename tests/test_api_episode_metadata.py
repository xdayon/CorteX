from __future__ import annotations

from pathlib import Path

import pytest

from asgi_client import ASGITestClient
from cortex.api import create_app
from cortex.config import load_config
from cortex.domain.models import SourceAsset, SourceKind
from cortex.ingest.youtube_metadata import YoutubeMetadata, YoutubeMetadataError

VIDEO_ID = "AbCdEfGh_12"
EPISODES = "/api/v1/episodes"


@pytest.fixture
def application(tmp_path: Path, monkeypatch):
    def unexpected_fetch(*_args, **_kwargs):
        pytest.fail("Metadata requests must be explicit and mocked")

    monkeypatch.setattr("cortex.library.fetch_youtube_metadata", unexpected_fetch)
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
    app = create_app(config)
    return app, ASGITestClient(app)


def _register(client):
    response = client.post(EPISODES, json={"url": f"https://youtu.be/{VIDEO_ID}?si=tracking"})
    assert response.status_code == 201, response.text
    return response.json()


def test_register_and_poll_do_not_refresh_metadata(application):
    _app, client = application
    episode = _register(client)
    assert episode["metadata_updated_at"] is None
    assert episode["thumbnail_url"] is None
    assert episode["archived"] is False
    assert client.get(EPISODES).json() == [episode]
    assert client.get(f"{EPISODES}?include_archived=true").json() == [episode]
    assert client.get(f"{EPISODES}/{VIDEO_ID}").json() == episode


def test_explicit_refresh_persists_metadata_before_download_and_survives_reopening(application, monkeypatch):
    app, client = application
    _register(client)
    calls = []

    def fetch(video_id):
        calls.append(video_id)
        return YoutubeMetadata(
            "Podcast com Dayon", "Canal do podcast", "https://www.youtube.com/@podcast",
            f"https://i.ytimg.com/vi/{VIDEO_ID}/hqdefault.jpg",
        )

    monkeypatch.setattr("cortex.library.fetch_youtube_metadata", fetch)
    response = client.post(f"{EPISODES}/{VIDEO_ID}/metadata")
    assert response.status_code == 200, response.text
    episode = response.json()
    assert episode["title"] == "Podcast com Dayon"
    assert episode["channel_name"] == "Canal do podcast"
    assert episode["thumbnail_url"].endswith("hqdefault.jpg")
    assert episode["metadata_updated_at"]
    assert episode["metadata_error"] is None
    assert episode["source"] is None
    assert episode["jobs"] == []
    reopened = ASGITestClient(create_app(app.state.config))
    assert reopened.get(EPISODES).json() == [episode]
    assert calls == [VIDEO_ID]


def test_refresh_failure_returns_visible_persisted_error_without_losing_episode(application, monkeypatch):
    _app, client = application
    original = _register(client)

    def fail(_id):
        raise YoutubeMetadataError("YouTube indisponível")

    monkeypatch.setattr("cortex.library.fetch_youtube_metadata", fail)
    response = client.post(f"{EPISODES}/{VIDEO_ID}/metadata")
    assert response.status_code == 200
    assert response.json()["metadata_error"] == "YouTube indisponível"
    assert response.json()["title"] == original["title"]
    assert client.get(EPISODES).json() == [response.json()]


def test_archive_hides_episode_and_restores_without_deleting_project(application):
    app, client = application
    episode = _register(client)
    response = client.request("PATCH", f"{EPISODES}/{VIDEO_ID}", json={"archived": True})
    assert response.status_code == 200
    assert response.json()["archived"] is True
    assert client.get(EPISODES).json() == []
    assert client.get(f"{EPISODES}?include_archived=true").json() == [response.json()]
    assert client.get(f"{EPISODES}/{VIDEO_ID}").json() == response.json()
    assert app.state.domain.get_project(episode["project_ids"][0]).id == episode["project_ids"][0]
    restored = client.request("PATCH", f"{EPISODES}/{VIDEO_ID}", json={"archived": False})
    assert restored.status_code == 200
    assert client.get(EPISODES).json() == [episode]


@pytest.mark.parametrize("payload", [{}, {"archived": "true"}, {"archived": True, "title": "unexpected"}])
def test_archive_patch_accepts_only_explicit_boolean_field(application, payload):
    _app, client = application
    episode = _register(client)
    response = client.request("PATCH", f"{EPISODES}/{VIDEO_ID}", json=payload)
    assert response.status_code == 422
    assert client.get(f"{EPISODES}/{VIDEO_ID}").json() == episode


def test_metadata_and_patch_unknown_episodes_return_404(application):
    _app, client = application
    assert client.post(f"{EPISODES}/missing/metadata").status_code == 404
    assert client.request("PATCH", f"{EPISODES}/missing", json={"archived": True}).status_code == 404


def test_local_episode_cannot_refresh_youtube_metadata(application):
    app, client = application
    project = app.state.domain.create_project("Arquivo local")
    app.state.domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD,
        stored_path="existing-source.mp4", sha256="a" * 64, size_bytes=1,
    ))
    episode = client.get(EPISODES).json()[0]
    response = client.post(f"{EPISODES}/{episode['id']}/metadata")
    assert response.status_code == 400
    assert "Episódio local" in response.json()["detail"]
