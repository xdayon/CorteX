"""Stdlib HTTP for the sound providers: JSON requests with retry policy and redirect-safe downloads."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from cortex.config import CortexConfig

USER_AGENT = "CorteX/0.1 (personal video editor)"
MAX_JSON_BYTES = 5 * 1024 * 1024
REDIRECTS = (301, 302, 303, 307, 308)
MAX_HOPS = 3
sleep = time.sleep


class SoundError(RuntimeError):
    """A sound problem the user has to see (missing key, rate limit, bad download)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def api_key(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    env = Path(".env")
    if not env.is_file():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        if key.strip() == name:
            return raw.strip().strip("'\"") or None
    return None


def _message(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return body.decode("utf-8", "replace")[:300]
    if isinstance(data, dict):
        for key in ("message", "error", "detail"):
            if data.get(key):
                return str(data[key])[:300]
    return ""


def _retry_after(err: urllib.error.HTTPError) -> float | None:
    try:
        return float(err.headers.get("Retry-After", ""))
    except ValueError:
        return None


def request_json(cfg: CortexConfig, url: str, *, params: dict | None = None, method: str = "GET",
                 body: dict | None = None, headers: dict | None = None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    host = urlparse(url).netloc
    head = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        head["Content-Type"] = "application/json"
    limit = cfg.sound.max_retry_wait_seconds
    waits, backoffs = 0, 0
    while True:
        req = urllib.request.Request(url, data=data, headers=head, method=method)
        try:
            with _opener.open(req, timeout=cfg.sound.http_timeout_seconds) as resp:
                raw = resp.read(MAX_JSON_BYTES + 1)
            if len(raw) > MAX_JSON_BYTES:
                raise SoundError(f"{host}: response larger than 5 MB")
            return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as err:
            raw = err.read(4096)
            status = err.code
            if status in (429, 503):
                wait = _retry_after(err)
                if wait is None:
                    wait = 2.0 * (waits + 1)
                if wait > limit:
                    raise SoundError(f"{host}: HTTP {status}, retry in {wait:.0f}s "
                                     f"(limit {limit:.0f}s); try again later") from None
                if waits >= 2:
                    raise SoundError(f"{host}: HTTP {status} after retries") from None
                waits += 1
                sleep(wait)
                continue
            if status >= 500:
                if backoffs >= 2:
                    raise SoundError(f"{host}: HTTP {status} after retries") from None
                backoffs += 1
                sleep(2.0 * backoffs)
                continue
            raise SoundError(f"{host}: HTTP {status} {_message(raw)}".strip()) from None
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            if backoffs >= 2:
                raise SoundError(f"{host}: network error ({type(err).__name__})") from None
            backoffs += 1
            sleep(2.0 * backoffs)
        except ValueError as err:
            raise SoundError(f"{host}: invalid JSON response") from err


def download(cfg: CortexConfig, url: str, dest: Path, *, headers: dict | None = None,
             require_https: bool = True) -> Path:
    head = {"User-Agent": USER_AGENT, **(headers or {})}
    limit = int(cfg.sound.max_download_mb * 1024 * 1024)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for hop in range(MAX_HOPS + 1):
        parsed = urlparse(url)
        if require_https and parsed.scheme != "https":
            raise SoundError(f"refusing non-https download from {parsed.netloc or url}")
        req = urllib.request.Request(url, headers=head)
        try:
            resp = _opener.open(req, timeout=cfg.sound.http_timeout_seconds)
        except urllib.error.HTTPError as err:
            if err.code in REDIRECTS and err.headers.get("Location"):
                if hop == MAX_HOPS:
                    raise SoundError(f"{parsed.netloc}: too many redirects") from None
                url = urllib.parse.urljoin(url, err.headers["Location"])
                head = {"User-Agent": USER_AGENT}
                continue
            raise SoundError(f"{parsed.netloc}: HTTP {err.code} {_message(err.read(4096))}".strip()) from None
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise SoundError(f"{parsed.netloc}: network error ({type(err).__name__})") from None
        with resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype in ("text/html", "application/json"):
                raise SoundError(f"{parsed.netloc}: got {ctype} instead of audio")
            size = 0
            try:
                with part.open("wb") as out:
                    while block := resp.read(1 << 16):
                        size += len(block)
                        if size > limit:
                            raise SoundError(f"{parsed.netloc}: file larger than {cfg.sound.max_download_mb:g} MB")
                        out.write(block)
            except BaseException:
                part.unlink(missing_ok=True)
                raise
        os.replace(part, dest)
        return dest
    raise SoundError("too many redirects")
