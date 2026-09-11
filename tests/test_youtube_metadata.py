from __future__ import annotations

from io import BytesIO
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from cortex.ingest.youtube_metadata import (
    MAX_RESPONSE_BYTES, METADATA_TIMEOUT_SECONDS,
    YoutubeMetadataError, fetch_youtube_metadata,
)

VIDEO_ID = "AbCdEfGh_12"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def unexpected_request(*_args, **_kwargs):
        pytest.fail("Every oEmbed transport must be mocked")
    monkeypatch.setattr("cortex.ingest.youtube_metadata.urlopen", unexpected_request)


def test_oembed_uses_fixed_endpoint_timeout_and_bounded_json_read(monkeypatch):
    calls = []
    reads = []

    class Response(BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    def transport(request, *, timeout):
        calls.append((request, timeout))
        return Response(json.dumps({
            "title": " Consciência\n e sociedade ", "author_name": " Canal do podcast ",
            "author_url": "https://www.youtube.com/@podcast",
            "thumbnail_url": f"https://i.ytimg.com/vi/{VIDEO_ID}/hqdefault.jpg",
            "html": "<iframe>ignored</iframe>",
        }).encode())

    monkeypatch.setattr("cortex.ingest.youtube_metadata.urlopen", transport)
    metadata = fetch_youtube_metadata(VIDEO_ID)
    request, timeout = calls[0]
    parsed = urlparse(request.full_url)
    assert parsed.scheme == "https"
    assert parsed.hostname == "www.youtube.com"
    assert parsed.path == "/oembed"
    assert parse_qs(parsed.query) == {
        "url": [f"https://www.youtube.com/watch?v={VIDEO_ID}"], "format": ["json"],
    }
    assert timeout == METADATA_TIMEOUT_SECONDS
    assert reads == [MAX_RESPONSE_BYTES + 1]
    assert metadata.title == "Consciência e sociedade"
    assert metadata.channel_name == "Canal do podcast"
    assert metadata.channel_url == "https://www.youtube.com/@podcast"
    assert metadata.thumbnail_url.endswith("/hqdefault.jpg")
    assert not hasattr(metadata, "html")


@pytest.mark.parametrize("payload", [b"not json", b"[]", b"{}", b'{"title":"  "}', b"\xff"])
def test_invalid_metadata_response_has_explicit_error(monkeypatch, payload):
    monkeypatch.setattr("cortex.ingest.youtube_metadata.urlopen", lambda *_args, **_kwargs: BytesIO(payload))
    with pytest.raises(YoutubeMetadataError):
        fetch_youtube_metadata(VIDEO_ID)


def test_oversized_response_is_rejected(monkeypatch):
    monkeypatch.setattr("cortex.ingest.youtube_metadata.urlopen", lambda *_args, **_kwargs: BytesIO(b"x" * (MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(YoutubeMetadataError, match="excedeu"):
        fetch_youtube_metadata(VIDEO_ID)


@pytest.mark.parametrize("error", [TimeoutError(), URLError("offline"), HTTPError("", 404, "Not found", {}, None)])
def test_network_failures_are_converted_without_echoing_remote_response(monkeypatch, error):
    def transport(*_args, **_kwargs):
        raise error

    monkeypatch.setattr("cortex.ingest.youtube_metadata.urlopen", transport)
    with pytest.raises(YoutubeMetadataError):
        fetch_youtube_metadata(VIDEO_ID)


def test_optional_urls_are_filtered_and_missing_channel_is_allowed(monkeypatch):
    payload = json.dumps({
        "title": "Podcast", "author_url": "javascript:alert(1)",
        "thumbnail_url": "https://username:password@example.com/image.jpg",
    }).encode()
    monkeypatch.setattr("cortex.ingest.youtube_metadata.urlopen", lambda *_args, **_kwargs: BytesIO(payload))
    metadata = fetch_youtube_metadata(VIDEO_ID)
    assert metadata.channel_name is metadata.channel_url is metadata.thumbnail_url is None


def test_invalid_video_id_fails_before_network():
    with pytest.raises(YoutubeMetadataError, match="ID de vídeo inválido"):
        fetch_youtube_metadata("https://example.com")


@pytest.mark.parametrize("timeout", [0, -1, 31, float("nan"), float("inf")])
def test_timeout_must_remain_bounded(timeout):
    with pytest.raises(ValueError, match="Timeout"):
        fetch_youtube_metadata(VIDEO_ID, timeout_seconds=timeout)
