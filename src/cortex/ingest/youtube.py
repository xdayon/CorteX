from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol


class YoutubeDownloadError(RuntimeError):
    pass


class YoutubeDownloader(Protocol):
    def extract_info(self, url: str, *, download: bool, options: dict[str, Any]) -> dict[str, Any]: ...


class YtDlpDownloader:
    """Thin wrapper around yt_dlp.YoutubeDL, isolated so tests can inject a fake."""

    def extract_info(self, url: str, *, download: bool, options: dict[str, Any]) -> dict[str, Any]:
        import yt_dlp

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=download)
        return info


def build_ydl_options(
    youtube_format: str,
    dest_dir: Path,
    *,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "format": youtube_format,
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    if progress_hook is not None:
        options["progress_hooks"] = [progress_hook]
    return options


def find_cached_download(dest_dir: Path, video_id: str) -> Path | None:
    if not dest_dir.exists():
        return None
    for match in sorted(dest_dir.glob(f"{video_id}.*")):
        if match.is_file() and match.stat().st_size > 0 and not match.name.endswith(".part"):
            return match
    return None


def progress_hook_to_fraction(status: dict[str, Any]) -> float | None:
    """Map a yt-dlp progress hook dict to a 0..1 fraction, if computable."""
    if status.get("status") != "downloading":
        return None
    total = status.get("total_bytes") or status.get("total_bytes_estimate")
    downloaded = status.get("downloaded_bytes")
    if not total or downloaded is None:
        return None
    return max(0.0, min(1.0, downloaded / total))


@dataclass(frozen=True)
class YoutubeDownloadResult:
    path: Path
    video_id: str
    title: str | None
    cached: bool


def download_youtube_source(
    url: str,
    dest_dir: Path,
    *,
    youtube_format: str,
    downloader: YoutubeDownloader | None = None,
    progress_cb: Callable[[float], None] | None = None,
) -> YoutubeDownloadResult:
    """Resolve `url` to a video id, reuse a cached download if present for
    this project, otherwise download with yt-dlp using the venv install.
    """
    downloader = downloader or YtDlpDownloader()
    dest_dir.mkdir(parents=True, exist_ok=True)

    probe_options = build_ydl_options(youtube_format, dest_dir)
    info = downloader.extract_info(url, download=False, options=probe_options)
    video_id = str(info.get("id") or "")
    if not video_id:
        raise YoutubeDownloadError("yt-dlp não retornou um id de vídeo para esta URL")

    cached = find_cached_download(dest_dir, video_id)
    if cached is not None:
        return YoutubeDownloadResult(path=cached, video_id=video_id, title=info.get("title"), cached=True)

    def _hook(status: dict[str, Any]) -> None:
        if progress_cb is None:
            return
        fraction = progress_hook_to_fraction(status)
        if fraction is not None:
            progress_cb(fraction)

    download_options = build_ydl_options(youtube_format, dest_dir, progress_hook=_hook)
    info = downloader.extract_info(url, download=True, options=download_options)
    downloaded = find_cached_download(dest_dir, video_id)
    if downloaded is None:
        raise YoutubeDownloadError("download concluído mas o arquivo não foi encontrado no destino")
    return YoutubeDownloadResult(path=downloaded, video_id=video_id, title=info.get("title"), cached=False)
