from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cortex.config import load_config
from cortex.domain.models import SourceAsset, SourceKind
from cortex.domain.store import DomainStore
from cortex.library import EpisodeLibrary, youtube_id

VIDEO_ID = "AbCdEfGh_12"
CANONICAL_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
URL_VARIANTS = [
    f"{CANONICAL_URL}&t=120&list=playlist",
    f"https://youtu.be/{VIDEO_ID}?si=tracking&t=120",
    f"https://www.youtube.com/live/{VIDEO_ID}?si=tracking",
]


@pytest.fixture
def library(tmp_path: Path) -> EpisodeLibrary:
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
    return EpisodeLibrary(config, DomainStore(config.paths.database))


def _source(library: EpisodeLibrary, project_id: str, url: str | None = None) -> SourceAsset:
    stored_path = Path("projects") / project_id / "source" / "podcast.mp4"
    path = library.config.paths.data_dir / stored_path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"catalogue fixture: file presence only, no media decoding"
    path.write_bytes(payload)
    return library.domain.create_source_asset(SourceAsset(
        project_id=project_id,
        kind=SourceKind.YOUTUBE if url else SourceKind.UPLOAD,
        original_filename="podcast.mp4",
        source_url=url,
        stored_path=str(stored_path),
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    ))


def test_watch_short_link_and_live_register_the_same_canonical_episode(library):
    episodes = []
    for url in URL_VARIANTS:
        assert youtube_id(url) == VIDEO_ID
        episodes.append(library.register(url))

    assert {episode.id for episode in episodes} == {VIDEO_ID}
    assert {episode.url for episode in episodes} == {CANONICAL_URL}
    assert all(episode.project_ids == episodes[0].project_ids for episode in episodes)
    assert len(library.entries()) == 1
    assert len(library.domain.list_projects()) == 1


def test_register_is_idempotent_and_persists_participant_count_across_reopening(library):
    first = library.register(URL_VARIANTS[0], participant_count=3)
    reopened = EpisodeLibrary(library.config, DomainStore(library.config.paths.database))
    assert reopened.get(VIDEO_ID).participant_count == 3
    assert reopened.register(URL_VARIANTS[1]).participant_count == 3

    updated = reopened.register(URL_VARIANTS[2], participant_count=2)
    assert updated.id == first.id
    assert updated.project_ids == first.project_ids
    assert library.get(VIDEO_ID).participant_count == 2
    assert len(library.entries()) == 1
    assert len(library.domain.list_projects()) == 1


def test_entries_reconstructs_local_episode_without_moving_existing_source(library):
    project = library.domain.create_project("Podcast local do Dayon")
    source = _source(library, project.id)
    path = library.config.paths.data_dir / source.stored_path

    entries = library.entries()
    assert len(entries) == 1
    episode = entries[0]
    assert episode.id == f"local-{project.id}"
    assert episode.url is None
    assert episode.title == project.name
    assert episode.project_ids == [project.id]
    reopened = EpisodeLibrary(library.config, DomainStore(library.config.paths.database))
    assert reopened.entries() == entries
    assert reopened.get(episode.id) == episode
    assert reopened.domain.list_source_assets(project.id) == [source]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source.sha256
    assert len(reopened.domain.list_projects()) == 1


def test_entries_groups_preexisting_youtube_sources_without_creating_new_projects(library):
    first = library.domain.create_project("Primeira importação")
    second = library.domain.create_project("Outra importação")
    _source(library, first.id, URL_VARIANTS[0])
    _source(library, second.id, URL_VARIANTS[2])

    entries = library.entries()
    assert len(entries) == 1
    assert entries[0].id == VIDEO_ID
    assert entries[0].url == CANONICAL_URL
    assert set(entries[0].project_ids) == {first.id, second.id}
    assert len(library.domain.list_projects()) == 2
    assert library.entries() == entries


@pytest.mark.parametrize("url", [
    "",
    "not a URL",
    f"youtube.com/watch?v={VIDEO_ID}",
    f"ftp://youtube.com/watch?v={VIDEO_ID}",
    f"https://example.com/watch?v={VIDEO_ID}",
    f"https://youtube.com.example.com/watch?v={VIDEO_ID}",
    "https://www.youtube.com/watch",
    "https://youtu.be/",
    "https://www.youtube.com/live/",
    "https://www.youtube.com/watch?v=too_short",
    f"https://youtu.be/{VIDEO_ID}x",
])
def test_invalid_urls_are_rejected_without_persisting_episode_or_project(library, url):
    with pytest.raises(ValueError):
        youtube_id(url)
    with pytest.raises(ValueError):
        library.register(url)
    assert library.entries() == []
    assert library.domain.list_projects() == []
