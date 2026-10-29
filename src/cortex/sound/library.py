"""Downloaded sounds: one audio file + one JSON per sound, with license metadata and analysis."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from cortex.config import CortexConfig
from cortex.sound import analysis, http
from cortex.sound.http import SoundError
from cortex.sound.providers import Candidate


class LibrarySound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    provider: str
    id: str
    title: str
    file: str
    duration: float
    anchor: float
    anchor_source: Literal["provider", "peak"]
    max_momentary_lufs: float | None = None
    sha256: str
    license_name: str = ""
    license_url: str = ""
    attribution: str = ""
    page_url: str = ""
    creator: str | None = None
    pack: str | None = None
    ai_content: str | None = None
    tags: list[str] = []
    downloaded_at: str
    source_url: str = ""

    def path(self, cfg: CortexConfig) -> Path:
        return cfg.sound.dir / self.file


def _split(ref: str) -> tuple[str, str]:
    provider, _, ident = ref.partition(":")
    if not provider or not ident or "/" in ident or ident.startswith("."):
        raise SoundError(f"invalid sound ref: {ref!r}")
    return provider, ident


def meta_path(cfg: CortexConfig, ref: str) -> Path:
    provider, ident = _split(ref)
    return cfg.sound.dir / "library" / provider / f"{ident}.json"


def load_sound(cfg: CortexConfig, ref: str) -> LibrarySound:
    path = meta_path(cfg, ref)
    if not path.is_file():
        raise SoundError(f"{ref} is not in the library")
    return LibrarySound.model_validate_json(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _extension(path: Path) -> str:
    head = path.read_bytes()[:4]
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    return "wav"


def ensure(cfg: CortexConfig, candidate: Candidate) -> LibrarySound:
    ref = candidate.ref
    if meta_path(cfg, ref).is_file():
        return load_sound(cfg, ref)
    headers = {}
    if candidate.provider == "soundza":
        key = http.api_key("SOUNDZA_API_KEY")
        if not key:
            raise SoundError("no SOUNDZA_API_KEY; run `python -m cortex.sound login`")
        headers["Authorization"] = f"Bearer {key}"
    elif key := http.api_key("SFXMINT_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    if not candidate.download_url:
        raise SoundError(f"{ref} has no download_url (keyless result); run `python -m cortex.sound login`")
    directory = cfg.sound.dir / "library" / candidate.provider
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f"{candidate.id}.wav"
    http.download(cfg, candidate.download_url, tmp, headers=headers)
    try:
        info = analysis.analyse(cfg, tmp)
        if info["duration"] <= 0:
            raise SoundError(f"{ref}: downloaded file has no audio")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    final = tmp
    if _extension(tmp) == "mp3":
        final = tmp.with_suffix(".mp3")
        os.replace(tmp, final)
    from_provider = candidate.sync_ms is not None
    sound = LibrarySound(
        ref=ref, provider=candidate.provider, id=candidate.id, title=candidate.title,
        file=str(final.relative_to(cfg.sound.dir)), duration=info["duration"],
        anchor=candidate.sync_ms / 1000 if from_provider else info["peak_time"],
        anchor_source="provider" if from_provider else "peak",
        max_momentary_lufs=info["max_momentary_lufs"], sha256=_sha256(final),
        license_name=candidate.license_name, license_url=candidate.license_url,
        attribution=candidate.attribution, page_url=candidate.page_url, creator=candidate.creator,
        pack=candidate.pack, ai_content=candidate.ai_content, tags=candidate.tags,
        downloaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_url=candidate.download_url,
    )
    meta_path(cfg, ref).write_text(sound.model_dump_json(indent=2), encoding="utf-8")
    return sound


def remove(cfg: CortexConfig, ref: str) -> bool:
    path = meta_path(cfg, ref)
    if not path.is_file():
        return False
    sound = load_sound(cfg, ref)
    sound.path(cfg).unlink(missing_ok=True)
    path.unlink()
    return True
