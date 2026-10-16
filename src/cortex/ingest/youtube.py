from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import time
from importlib.util import find_spec
from typing import Any, Callable, Protocol


class YoutubeDownloadError(RuntimeError):
    pass


class YoutubeDownloader(Protocol):
    def extract_info(self, url: str, *, download: bool, options: dict[str, Any]) -> dict[str, Any]: ...


class YtDlpDownloader:
    """Thin wrapper around yt_dlp.YoutubeDL, isolated so tests can inject a fake."""

    def extract_info(self, url: str, *, download: bool, options: dict[str, Any]) -> dict[str, Any]:
        import yt_dlp

        if not shutil.which("node"):
            raise YoutubeDownloadError("Download do YouTube requer Node.js no PATH do worker")
        if find_spec("yt_dlp_ejs") is None:
            raise YoutubeDownloadError("Dependência do YouTube ausente: instale o extra youtube do CorteX (yt-dlp[default])")

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=download)
        return info


def build_ydl_options(
    youtube_format: str,
    dest_dir: Path,
    *,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    cookies_from_browser: str | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "format": youtube_format,
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "no_warnings": False,
        "js_runtimes": {"node": {"path": shutil.which("node") or "node"}},
        "socket_timeout": 30,
        "retries": 3,
        "merge_output_format": "mp4",
    }
    if progress_hook is not None:
        options["progress_hooks"] = [progress_hook]
    if cookies_from_browser:
        browser, _, profile = cookies_from_browser.partition(":")
        options["cookiesfrombrowser"] = (
            browser, str(Path(profile).expanduser()) if profile else None, None, None,
        )
    return options


def find_cached_download(dest_dir: Path, video_id: str) -> Path | None:
    if not dest_dir.exists():
        return None
    for match in sorted(dest_dir.glob(f"{video_id}.*")):
        if (match.name in {f"{video_id}.mp4", f"{video_id}.mkv", f"{video_id}.webm"}
                and match.is_file() and match.stat().st_size > 0):
            return match
    return None


def progress_hook_to_fraction(status: dict[str, Any]) -> float | None:
    """Map a yt-dlp progress hook dict to a 0..1 fraction, if computable.

    Fragmented (HLS/DASH) downloads only have a rough ``total_bytes_estimate``
    that can start far too low, so fragment counts win when present.
    """
    if status.get("status") != "downloading":
        return None
    fragment_index, fragment_count = status.get("fragment_index"), status.get("fragment_count")
    if fragment_index is not None and fragment_count and fragment_count > 0:
        return max(0.0, min(1.0, fragment_index / fragment_count))
    total = status.get("total_bytes") or status.get("total_bytes_estimate")
    downloaded = status.get("downloaded_bytes")
    if not total or downloaded is None:
        return None
    return max(0.0, min(1.0, downloaded / total))


def _format_bytes(value: float) -> str:
    if value >= 1e9:
        return f"{value / 1e9:.1f} GB".replace(".", ",")
    return f"{value / 1e6:.0f} MB"


def progress_hook_to_detail(status: dict[str, Any]) -> str | None:
    downloaded = status.get("downloaded_bytes")
    if not downloaded:
        return None
    total = status.get("total_bytes")
    estimated = status.get("total_bytes_estimate")
    if total:
        return f"{_format_bytes(downloaded)} de {_format_bytes(total)}"
    if estimated:
        return f"{_format_bytes(downloaded)} de ~{_format_bytes(estimated)}"
    return f"{_format_bytes(downloaded)} baixados"


class DownloadProgressReporter:
    """yt-dlp progress hook that reports a monotonic fraction, at most once per
    second, with a heartbeat every ``heartbeat_seconds`` even if it stalls.

    The hook fires many times per second; each report is a SQLite write, and a
    stalled fraction must still refresh the job's ``updated_at``.
    """

    def __init__(
        self,
        progress_cb: Callable[[float, str | None], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        min_interval_seconds: float = 1.0,
        heartbeat_seconds: float = 2.0,
    ) -> None:
        self._progress_cb = progress_cb
        self._clock = clock
        self._min_interval = min_interval_seconds
        self._heartbeat = heartbeat_seconds
        self._best = 0.0
        self._last_emit_at: float | None = None
        self._last_emitted_fraction = -1.0

    def __call__(self, status: dict[str, Any]) -> None:
        fraction = progress_hook_to_fraction(status)
        if fraction is None:
            return
        self._best = max(self._best, fraction)
        now = self._clock()
        if self._last_emit_at is not None:
            elapsed = now - self._last_emit_at
            advanced = self._best > self._last_emitted_fraction
            if elapsed < self._min_interval or (not advanced and elapsed < self._heartbeat):
                return
        self._last_emit_at = now
        self._last_emitted_fraction = self._best
        self._progress_cb(self._best, progress_hook_to_detail(status))


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
    progress_cb: Callable[[float, str | None], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    cookies_from_browser: str | None = None,
) -> YoutubeDownloadResult:
    """Resolve `url` to a video id, reuse a cached download if present for
    this project, otherwise download with yt-dlp using the venv install.
    """
    downloader = downloader or YtDlpDownloader()
    dest_dir.mkdir(parents=True, exist_ok=True)

    probe_options = build_ydl_options(
        youtube_format, dest_dir, cookies_from_browser=cookies_from_browser,
    )
    info = downloader.extract_info(url, download=False, options=probe_options)
    video_id = str(info.get("id") or "")
    if not video_id:
        raise YoutubeDownloadError("yt-dlp não retornou um id de vídeo para esta URL")

    cached = find_cached_download(dest_dir, video_id)
    if cached is not None:
        return YoutubeDownloadResult(path=cached, video_id=video_id, title=info.get("title"), cached=True)

    _hook = DownloadProgressReporter(progress_cb, clock=clock) if progress_cb is not None else None

    download_options = build_ydl_options(
        youtube_format, dest_dir, progress_hook=_hook, cookies_from_browser=cookies_from_browser,
    )
    info = downloader.extract_info(url, download=True, options=download_options)
    downloaded = find_cached_download(dest_dir, video_id)
    if downloaded is None:
        raise YoutubeDownloadError("download concluído mas o arquivo não foi encontrado no destino")
    return YoutubeDownloadResult(path=downloaded, video_id=video_id, title=info.get("title"), cached=False)
