from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cortex.config import load_config
from cortex.domain.models import SourceAsset, SourceKind
from cortex.domain.store import DomainStore
from cortex.ingest.youtube_metadata import YoutubeMetadata, YoutubeMetadataError
from cortex.jobs import JobStore
from cortex.library import Episode, EpisodeLibrary, youtube_id

VIDEO_ID = "AbCdEfGh_12"
CANONICAL_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
URL_VARIANTS = [
    f"{CANONICAL_URL}&t=120&list=playlist",
    f"https://youtu.be/{VIDEO_ID}?si=tracking&t=120",
    f"https://www.youtube.com/live/{VIDEO_ID}?si=tracking",
]


@pytest.fixture(autouse=True)
def no_metadata_network(monkeypatch):
    def unexpected_fetch(*_args, **_kwargs):
        pytest.fail("Metadata network access must be explicit and mocked in tests")
    monkeypatch.setattr("cortex.library.fetch_youtube_metadata", unexpected_fetch)


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
    JobStore(config.paths.database)
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


def test_explicit_metadata_refresh_persists_catalogue_before_video_download(library, monkeypatch):
    episode = library.register(URL_VARIANTS[0], participant_count=3)
    calls = []

    def fetch(video_id):
        calls.append(video_id)
        return YoutubeMetadata(
            title="Consciência e sociedade | Dayon",
            channel_name="Podcast do anfitrião",
            channel_url="https://www.youtube.com/@anfitriao",
            thumbnail_url=f"https://i.ytimg.com/vi/{VIDEO_ID}/hqdefault.jpg",
        )

    monkeypatch.setattr("cortex.library.fetch_youtube_metadata", fetch)
    updated = library.refresh_metadata(episode.id)
    assert calls == [VIDEO_ID]
    assert updated.metadata_updated_at is not None
    assert updated.metadata_error is None
    reopened = EpisodeLibrary(library.config, DomainStore(library.config.paths.database))
    assert reopened.get(episode.id) == updated
    assert reopened.register(URL_VARIANTS[1]).title == updated.title
    detail = reopened.detail(reopened.entries()[0])
    assert detail["title"] == updated.title
    assert detail["channel_name"] == "Podcast do anfitrião"
    assert detail["thumbnail_url"] == updated.thumbnail_url
    assert detail["source"] is None
    assert detail["jobs"] == []
    assert calls == [VIDEO_ID]


def test_reconcile_and_detail_keep_metadata_title_instead_of_download_filename(library, monkeypatch):
    episode = library.register(CANONICAL_URL)
    monkeypatch.setattr("cortex.library.fetch_youtube_metadata", lambda _id: YoutubeMetadata("Título real"))
    library.refresh_metadata(episode.id)
    _source(library, episode.project_ids[0], URL_VARIANTS[2])
    entry = library.entries()[0]
    assert entry.title == "Título real"
    assert library.detail(entry)["title"] == "Título real"
    assert library.detail(entry)["source"]["original_filename"] == "podcast.mp4"


def test_failed_metadata_refresh_preserves_previous_success_and_exposes_error(library, monkeypatch):
    episode = library.register(CANONICAL_URL)
    monkeypatch.setattr("cortex.library.fetch_youtube_metadata", lambda _id: YoutubeMetadata("Título persistido"))
    successful = library.refresh_metadata(episode.id)

    def fail(_id):
        raise YoutubeMetadataError("YouTube indisponível")

    monkeypatch.setattr("cortex.library.fetch_youtube_metadata", fail)
    failed = library.refresh_metadata(episode.id)
    assert failed.title == successful.title
    assert failed.metadata_updated_at == successful.metadata_updated_at
    assert library.entries()[0].metadata_error == "YouTube indisponível"
    assert library.detail(failed)["metadata_error"] == "YouTube indisponível"
    monkeypatch.setattr("cortex.library.fetch_youtube_metadata", lambda _id: YoutubeMetadata("Título atualizado"))
    assert library.refresh_metadata(episode.id).metadata_error is None


def test_first_metadata_failure_keeps_undownloaded_episode_in_catalogue(library, monkeypatch):
    episode = library.register(CANONICAL_URL)

    def fail(_id):
        raise YoutubeMetadataError("Vídeo privado ou indisponível")

    monkeypatch.setattr("cortex.library.fetch_youtube_metadata", fail)
    failed = library.refresh_metadata(episode.id)
    assert failed.metadata_updated_at is None
    assert failed.metadata_error == "Vídeo privado ou indisponível"
    assert library.entries() == [failed]
    assert library.domain.list_source_assets(episode.project_ids[0]) == []


def test_refresh_preserves_changes_made_while_network_request_is_running(library, monkeypatch):
    episode = library.register(CANONICAL_URL)

    def fetch(_id):
        library.set_archived(episode.id)
        library.register(CANONICAL_URL, participant_count=3)
        return YoutubeMetadata("Novo título")

    monkeypatch.setattr("cortex.library.fetch_youtube_metadata", fetch)
    updated = library.refresh_metadata(episode.id)
    assert updated.archived is True
    assert updated.participant_count == 3
    assert updated.title == "Novo título"


def test_archive_persists_across_polling_and_reopening_without_deleting_media(library):
    project = library.domain.create_project("Importação local obsoleta")
    source = _source(library, project.id)
    episode = library.entries()[0]
    library.set_archived(episode.id)
    reopened = EpisodeLibrary(library.config, DomainStore(library.config.paths.database))
    assert reopened.entries() == []
    assert reopened.entries() == []
    assert reopened.entries(include_archived=True)[0].archived is True
    assert (library.config.paths.data_dir / source.stored_path).is_file()
    assert reopened.domain.get_source_asset(source.id) == source
    reopened.set_archived(episode.id, False)
    assert reopened.entries()[0].id == episode.id


def test_local_catalogue_duplicate_stays_hidden_when_grouped_youtube_is_archived(library):
    project = library.domain.create_project("Importação prévia")
    source = _source(library, project.id)
    local_entry = library.entries()[0]
    grouped = library.register(CANONICAL_URL)
    grouped.project_ids.append(project.id)
    library.save(grouped)
    assert [entry.id for entry in library.entries()] == [VIDEO_ID]
    library.set_archived(VIDEO_ID)
    assert library.entries() == []
    assert {entry.id for entry in library.entries(include_archived=True)} == {VIDEO_ID, local_entry.id}
    assert library.domain.get_source_asset(source.id) == source


def test_legacy_episode_defaults_and_local_metadata_rejection(library):
    legacy = Episode.model_validate({
        "id": "local-legacy", "title": "Local", "project_ids": [],
    })
    assert legacy.archived is False
    assert legacy.channel_name is legacy.metadata_updated_at is legacy.metadata_error is None
    library.save(legacy)
    with pytest.raises(ValueError, match="Episódio local"):
        library.refresh_metadata(legacy.id)
