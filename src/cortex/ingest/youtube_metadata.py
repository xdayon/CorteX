"""Explicit, small YouTube oEmbed requests; never download video or embed HTML."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

MAX_RESPONSE_BYTES = 64 * 1024
METADATA_TIMEOUT_SECONDS = 10.0


class YoutubeMetadataError(RuntimeError):
    pass


@dataclass(frozen=True)
class YoutubeMetadata:
    title: str
    channel_name: str | None = None
    channel_url: str | None = None
    thumbnail_url: str | None = None


def _text(value: object, limit: int = 1000) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split())[:limit] or None


def _web_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlparse(value)
        if (parsed.scheme in {"http", "https"} and parsed.hostname
                and not parsed.username and not parsed.password):
            return value
    except ValueError:
        pass
    return None


def fetch_youtube_metadata(
    video_id: str, *, timeout_seconds: float = METADATA_TIMEOUT_SECONDS,
) -> YoutubeMetadata:
    """Fetch only a bounded JSON response from the fixed public oEmbed endpoint."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise YoutubeMetadataError("ID de vídeo inválido para metadados")
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
        raise ValueError("Timeout de metadados deve estar entre 0 e 30 segundos")
    query = urlencode({"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"})
    request = Request(
        f"https://www.youtube.com/oembed?{query}",
        headers={"Accept": "application/json", "User-Agent": "CorteX/0.1"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise YoutubeMetadataError(
            f"YouTube não forneceu metadados (HTTP {exc.code}); o episódio foi mantido."
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise YoutubeMetadataError(
            "Não foi possível consultar metadados do YouTube; verifique a conexão e tente novamente."
        ) from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise YoutubeMetadataError("Resposta de metadados do YouTube excedeu o limite permitido")
    try:
        document = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise YoutubeMetadataError("YouTube retornou metadados JSON inválidos") from exc
    if not isinstance(document, dict) or not (title := _text(document.get("title"))):
        raise YoutubeMetadataError("YouTube retornou metadados sem título válido")
    return YoutubeMetadata(
        title=title,
        channel_name=_text(document.get("author_name")),
        channel_url=_web_url(document.get("author_url")),
        thumbnail_url=_web_url(document.get("thumbnail_url")),
    )
