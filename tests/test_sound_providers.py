from __future__ import annotations

import io
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cortex.config import CortexConfig, SoundConfig
from cortex.sound import http, providers
from cortex.sound.http import SoundError

SZ = {"sounds": [
    {"id": "abc", "url": "https://soundza.com/sound/x", "title": "Fast Whoosh", "description": "d" * 400,
     "type": "sound_effect", "duration": 0.5, "tags": ["whoosh"], "pack": {"name": "Pack"},
     "creator": {"name": "Ann", "url": None}, "license": {"name": "Std", "url": "https://soundza.com/license"},
     "attribution": "credit", "sync_ms": 120, "loudness": {"value": -12.5, "basis": "momentary_max"},
     "ai_content": "some_ai", "download_url": "https://api.soundza.com/v1/sounds/abc/file?search_id=1",
     "preview_files": [{"type": "mp3", "bitrate": 128, "link": "https://media.soundza.com/p.mp3"}]},
    {"id": "k", "title": "Keyless", "duration": 1, "preview_files": ["https://media.soundza.com/q.mp3"],
     "creator": None, "pack": None},
    {"id": "n", "title": "None", "duration": None},
]}
SFX = {"candidates": [
    {"slug": "whoosh-03", "title": "Whoosh 03", "duration_ms": 3000, "tags": ["a"], "category": "transition",
     "description": "x", "mp3_url": "https://sfxmint.com/dl/whoosh-03.mp3",
     "wav_url": "https://sfxmint.com/dl/whoosh-03.wav", "page_url": "https://sfxmint.com/sounds/whoosh-03"}]}


def cfg_for(tmp_path, **kw) -> CortexConfig:
    return CortexConfig(sound=SoundConfig(dir=tmp_path, **kw))


@pytest.fixture(autouse=True)
def no_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("SOUNDZA_API_KEY", raising=False)
    monkeypatch.delenv("SFXMINT_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(http, "sleep", lambda s: None)


def test_soundza_normalisation(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "request_json", lambda *a, **k: SZ)
    a, b, c = providers.soundza_search(cfg_for(tmp_path), "whoosh", type="SOUND_EFFECT")
    assert a.ref == "soundza:abc" and len(a.description) == 300
    assert a.preview_url == "https://media.soundza.com/p.mp3"
    assert (a.creator, a.pack, a.sync_ms, a.loudness, a.ai_content) == ("Ann", "Pack", 120, -12.5, "some_ai")
    assert a.download_url.endswith("search_id=1") and a.license_name == "Std"
    assert b.preview_url == "https://media.soundza.com/q.mp3" and b.creator is None
    assert c.duration == 0 and c.preview_url is None


def test_soundza_params(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(providers, "request_json", lambda cfg, url, params=None, headers=None: seen.update(p=params) or SZ)
    providers.soundza_search(cfg_for(tmp_path), "x", type="SOUND_EFFECT", good_for=["a", "b"], per_page=99)
    assert seen["p"]["type"] == "sound_effect" and seen["p"]["good_for"] == "a,b" and seen["p"]["per_page"] == 50


def test_sfxmint_normalisation(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(providers, "request_json", lambda cfg, url, params=None, headers=None: seen.update(p=params) or SFX)
    (c,) = providers.sfxmint_search(cfg_for(tmp_path), "whoosh", max_duration=2.0)
    assert seen["p"]["max_duration_ms"] == 2000 and seen["p"]["format"] == "wav"
    assert c.ref == "sfxmint:whoosh-03" and c.duration == 3.0 and c.download_url.endswith(".wav")
    assert c.license_name == "CC0" and c.attribution == '"Whoosh 03" — SFXMint (CC0)' and c.sync_ms is None


def test_search_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(providers, "request_json", lambda *a, **k: calls.append(1) or SZ)
    cfg = cfg_for(tmp_path)
    providers.soundza_search(cfg, "whoosh")
    providers.soundza_search(cfg, "whoosh")
    assert len(calls) == 1
    providers.soundza_search(cfg, "whoosh", refresh=True)
    assert len(calls) == 2


def test_removals_pagination(tmp_path, monkeypatch):
    pages = iter([{"removals": [{"id": "a"}], "next_page": "https://x/next"},
                  {"removals": [{"id": "b"}], "next_page": None}])
    monkeypatch.setattr(providers, "request_json", lambda *a, **k: next(pages))
    assert providers.soundza_removals(cfg_for(tmp_path), "2026-01-01") == ["a", "b"]


def test_api_key(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("# c\nFOO=1\nSOUNDZA_API_KEY=\"sz_live_file\"\n")
    assert http.api_key("SOUNDZA_API_KEY") == "sz_live_file"
    monkeypatch.setenv("SOUNDZA_API_KEY", "from_env")
    assert http.api_key("SOUNDZA_API_KEY") == "from_env"
    assert http.api_key("MISSING") is None


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, headers=None, body=b"{}"):
    return urllib.error.HTTPError("https://api.soundza.com/x", code, "m", headers or {}, io.BytesIO(body))


def script_opener(monkeypatch, outcomes):
    calls = []

    class Opener:
        def open(self, req, timeout=None):
            calls.append(req)
            out = outcomes.pop(0)
            if isinstance(out, Exception):
                raise out
            return FakeResp(out)

    monkeypatch.setattr(http, "_opener", Opener())
    return calls


def test_retry_after_then_success(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(http, "sleep", sleeps.append)
    calls = script_opener(monkeypatch, [http_error(429, {"Retry-After": "3"}), b'{"ok": 1}'])
    assert http.request_json(cfg_for(tmp_path), "https://api.soundza.com/x") == {"ok": 1}
    assert sleeps == [3.0] and len(calls) == 2


def test_retry_after_too_long(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(http, "sleep", sleeps.append)
    script_opener(monkeypatch, [http_error(429, {"Retry-After": "300"})])
    with pytest.raises(SoundError, match=r"api\.soundza\.com.*429.*300"):
        http.request_json(cfg_for(tmp_path), "https://api.soundza.com/x")
    assert sleeps == []


def test_5xx_backoff_and_404_message(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(http, "sleep", sleeps.append)
    script_opener(monkeypatch, [http_error(500), http_error(502), b"{}"])
    assert http.request_json(cfg_for(tmp_path), "https://h/x") == {}
    assert sleeps == [2.0, 4.0]
    script_opener(monkeypatch, [http_error(404, body=b'{"message": "sound not found"}')])
    with pytest.raises(SoundError, match="404 sound not found"):
        http.request_json(cfg_for(tmp_path), "https://h/x")


@pytest.fixture
def server():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen[self.path] = self.headers.get("Authorization")
            if self.path == "/file":
                self.send_response(302)
                self.send_header("Location", "/signed")
                self.end_headers()
            elif self.path == "/signed":
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.end_headers()
                self.wfile.write(b"RIFFdata")
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html></html>")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()


def test_download_redirect_drops_auth(tmp_path, server):
    base, seen = server
    dest = http.download(cfg_for(tmp_path), f"{base}/file", tmp_path / "out" / "a.wav",
                         headers={"Authorization": "Bearer secret"}, require_https=False)
    assert dest.read_bytes() == b"RIFFdata"
    assert seen["/file"] == "Bearer secret" and seen["/signed"] is None
    assert not dest.with_suffix(".wav.part").exists()


def test_download_rejects_html_and_http(tmp_path, server):
    base, _ = server
    with pytest.raises(SoundError, match="text/html"):
        http.download(cfg_for(tmp_path), f"{base}/page", tmp_path / "b.wav", require_https=False)
    with pytest.raises(SoundError, match="https"):
        http.download(cfg_for(tmp_path), f"{base}/file", tmp_path / "c.wav")
