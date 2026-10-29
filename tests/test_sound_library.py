from __future__ import annotations

import shutil

import pytest

from cortex.config import CortexConfig, SoundConfig
from cortex.sound import analysis, http, library
from cortex.sound.http import SoundError
from cortex.sound.providers import Candidate
from lessons_media import ff


def cfg_for(tmp_path) -> CortexConfig:
    return CortexConfig(sound=SoundConfig(dir=tmp_path / "sound"))


def burst(path):
    ff("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-f", "lavfi",
       "-i", "sine=f=1000:r=48000:d=0.2", "-filter_complex",
       "[0:a]atrim=0:2[s];[1:a]adelay=1200|1200,apad=whole_dur=2[b];[s][b]amix=inputs=2:normalize=0,atrim=0:2",
       path)


def candidate(sync_ms=None, **kw) -> Candidate:
    return Candidate(provider="sfxmint", id="s1", title="S1", duration=2.0, license_name="CC0",
                     download_url="https://sfxmint.com/dl/s1.wav", sync_ms=sync_ms, **kw)


def test_analyse_burst(tmp_path):
    wav = tmp_path / "b.wav"
    burst(wav)
    info = analysis.analyse(cfg_for(tmp_path), wav)
    assert 1.9 < info["duration"] < 2.1
    assert 1.2 <= info["peak_time"] <= 1.4
    assert info["max_momentary_lufs"] is not None and info["max_momentary_lufs"] > -120


def fake_download(src, calls):
    def download(cfg, url, dest, *, headers=None, require_https=True):
        calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest
    return download


def test_ensure_peak_anchor_and_cache(tmp_path, monkeypatch):
    src = tmp_path / "src.wav"
    burst(src)
    calls = []
    monkeypatch.setattr(http, "download", fake_download(src, calls))
    cfg = cfg_for(tmp_path)
    sound = library.ensure(cfg, candidate())
    assert sound.anchor_source == "peak" and 1.2 <= sound.anchor <= 1.4
    assert sound.file == "library/sfxmint/s1.wav" and sound.path(cfg).is_file()
    assert (cfg.sound.dir / "library/sfxmint/s1.json").is_file()
    assert sound.source_url == "https://sfxmint.com/dl/s1.wav" and len(sound.sha256) == 64
    again = library.ensure(cfg, candidate())
    assert again == sound and len(calls) == 1
    assert library.load_sound(cfg, "sfxmint:s1") == sound


def test_ensure_provider_anchor(tmp_path, monkeypatch):
    src = tmp_path / "src.wav"
    burst(src)
    monkeypatch.setattr(http, "download", fake_download(src, []))
    sound = library.ensure(cfg_for(tmp_path), candidate(sync_ms=500))
    assert sound.anchor == 0.5 and sound.anchor_source == "provider"


def test_ensure_invalid_file_removed(tmp_path, monkeypatch):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio")
    monkeypatch.setattr(http, "download", fake_download(bad, []))
    cfg = cfg_for(tmp_path)
    with pytest.raises(SoundError):
        library.ensure(cfg, candidate())
    assert not list((cfg.sound.dir / "library").rglob("s1.*"))


def test_soundza_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("SOUNDZA_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    cand = Candidate(provider="soundza", id="z", title="Z", download_url="https://api.soundza.com/x")
    with pytest.raises(SoundError, match="login"):
        library.ensure(cfg_for(tmp_path), cand)


def test_load_missing_and_remove(tmp_path):
    with pytest.raises(SoundError):
        library.load_sound(cfg_for(tmp_path), "soundza:nope")
    assert library.remove(cfg_for(tmp_path), "soundza:nope") is False
