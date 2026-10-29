from __future__ import annotations

import json
import subprocess

import pytest

from cortex.config import CortexConfig, SoundConfig
from cortex.sound import kit as kits
from cortex.sound import library
from cortex.sound.http import SoundError
from cortex.sound.library import LibrarySound
from cortex.sound.providers import Candidate
from lessons_media import ff


def cfg_for(tmp_path) -> CortexConfig:
    return CortexConfig(sound=SoundConfig(dir=tmp_path / "sound"))


def cand(provider, ident, title="T", **kw) -> Candidate:
    return Candidate(provider=provider, id=ident, title=title, duration=1.0, download_url="https://x/y",
                     preview_url="https://media.soundza.com/p.mp3" if provider == "soundza" else None, **kw)


@pytest.fixture
def fakes(monkeypatch):
    def sz(cfg, q, **kw):
        return [cand("soundza", "a", "<script>x</script>"), cand("soundza", "b", ai_content="ai")]

    def sfx(cfg, q, **kw):
        return [cand("sfxmint", "c"), cand("soundza", "a")]

    monkeypatch.setattr(kits.providers, "soundza_search", sz)
    monkeypatch.setattr(kits.providers, "sfxmint_search", sfx)

    def ensure(cfg, c):
        s = LibrarySound(ref=c.ref, provider=c.provider, id=c.id, title=c.title,
                         file=f"library/{c.provider}/{c.id}.wav", duration=1.0, anchor=0.1,
                         anchor_source="peak", sha256="0" * 64, downloaded_at="2026-01-01T00:00:00+00:00")
        path = library.meta_path(cfg, c.ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(s.model_dump_json())
        s.path(cfg).write_bytes(b"x")
        return s

    monkeypatch.setattr(kits.library, "ensure", ensure)


def test_kit_flow(tmp_path, fakes):
    cfg = cfg_for(tmp_path)
    kit = kits.init_kit(cfg, "dayon", "Dayon News")
    assert list(kit.roles)[:3] == ["whoosh", "swipe", "riser"] and len(kit.roles) == 13
    with pytest.raises(SoundError):
        kits.init_kit(cfg, "dayon", "x")
    with pytest.raises(SoundError):
        kits.load_kit(cfg, "../evil")

    found = kits.collect_candidates(cfg, kit, ["whoosh"])
    assert [c.ref for c in found["whoosh"]] == ["soundza:a", "soundza:b", "sfxmint:c"]
    directory = kits.kit_dir(cfg, "dayon")
    page = (directory / "audition.html").read_text()
    assert "<script>x</script>" not in page.split("<script>")[0] and "&lt;script&gt;x&lt;/script&gt;" in page
    assert "sem prévia" in page and "IA" in page and "https://media.soundza.com/p.mp3" in page
    assert list(json.loads((directory / "candidates.json").read_text())) == ["whoosh"]

    picked = kits.pick(cfg, "dayon", "whoosh", [1, 3])
    assert [s.ref for s in picked] == ["soundza:a", "sfxmint:c"]
    kits.pick(cfg, "dayon", "whoosh", [1])
    assert kits.load_kit(cfg, "dayon").roles["whoosh"].sounds == ["soundza:a", "sfxmint:c"]
    with pytest.raises(SoundError):
        kits.pick(cfg, "dayon", "whoosh", [9])

    again = kits.collect_candidates(cfg, kits.load_kit(cfg, "dayon"), ["whoosh"])
    assert [c.ref for c in again["whoosh"]] == ["soundza:b"]

    assert kits.drop(cfg, "dayon", "whoosh", "2") == "sfxmint:c"
    removed = kits.apply_removals(cfg, ["a", "zzz"])
    assert removed == ["soundza:a"]
    assert kits.load_kit(cfg, "dayon").roles["whoosh"].sounds == []
    assert not library.meta_path(cfg, "soundza:a").exists()


def test_render_reel(tmp_path):
    cfg = cfg_for(tmp_path)
    kit = kits.init_kit(cfg, "r", "R")
    for i, role in enumerate(["whoosh", "riser"], start=1):
        ident = f"s{i}"
        path = cfg.sound.dir / "library" / "sfxmint" / f"{ident}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        ff("-f", "lavfi", "-i", f"sine=f={400 * i}:r=44100:d={i}", path)
        s = LibrarySound(ref=f"sfxmint:{ident}", provider="sfxmint", id=ident, title=f"Sound {i}",
                         file=f"library/sfxmint/{ident}.wav", duration=float(i), anchor=0.0,
                         anchor_source="peak", max_momentary_lufs=-20.0, sha256="0" * 64,
                         downloaded_at="2026-01-01T00:00:00+00:00")
        library.meta_path(cfg, s.ref).write_text(s.model_dump_json())
        kit.roles[role].sounds.append(s.ref)
    kits.save_kit(cfg, kit)
    wav, txt = kits.render_reel(cfg, "r")
    out = subprocess.run([str(cfg.render.ffprobe), "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(wav)], capture_output=True, text=True).stdout
    assert abs(float(out) - (1 + 2 + 0.6)) < 0.1
    lines = txt.read_text().splitlines()
    assert lines[0].startswith("00:00.0  whoosh  Sound 1") and lines[1].startswith("00:01.6  riser")
