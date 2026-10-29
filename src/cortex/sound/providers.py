"""Soundza and SFXMint searches normalised to `Candidate`, with an on-disk response cache."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from cortex.config import CortexConfig
from cortex.sound.http import api_key, request_json

SOUNDZA_API = "https://api.soundza.com/v1"
SFXMINT_API = "https://sfxmint.com/api/v1"
CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["soundza", "sfxmint"]
    id: str
    title: str
    description: str = ""
    duration: float = 0.0
    type: str | None = None
    tags: list[str] = []
    pack: str | None = None
    creator: str | None = None
    license_name: str = ""
    license_url: str = ""
    attribution: str = ""
    page_url: str = ""
    preview_url: str | None = None
    download_url: str = ""
    sync_ms: int | None = None
    loudness: float | None = None
    ai_content: str | None = None
    bpm: float | None = None
    key: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.provider}:{self.id}"


def _auth(name: str) -> dict[str, str]:
    key = api_key(name)
    return {"Authorization": f"Bearer {key}"} if key else {}


def _cached(cfg: CortexConfig, provider: str, url: str, params: dict, authed: bool, refresh: bool):
    ident = json.dumps({"url": url, "params": params, "authed": authed}, sort_keys=True)
    path = cfg.sound.dir / "cache" / provider / f"{hashlib.sha256(ident.encode()).hexdigest()}.json"
    if path.is_file() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    headers = _auth("SOUNDZA_API_KEY" if provider == "soundza" else "SFXMINT_API_KEY")
    data = request_json(cfg, url, params=params, headers=headers)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _preview(files) -> str | None:
    for item in files or []:
        link = item if isinstance(item, str) else (item or {}).get("link") or (item or {}).get("url")
        if link:
            return link
    return None


def _soundza(item: dict) -> Candidate:
    loud = item.get("loudness")
    lic = item.get("license") or {}
    return Candidate(
        provider="soundza", id=item["id"], title=item.get("title") or item["id"],
        description=(item.get("description") or "")[:300], duration=float(item.get("duration") or 0),
        type=item.get("type"), tags=list(item.get("tags") or []),
        pack=(item.get("pack") or {}).get("name"), creator=(item.get("creator") or {}).get("name"),
        license_name=lic.get("name", ""), license_url=lic.get("url", ""),
        attribution=item.get("attribution") or "", page_url=item.get("url") or "",
        preview_url=_preview(item.get("preview_files")), download_url=item.get("download_url") or "",
        sync_ms=item.get("sync_ms"),
        loudness=loud.get("value") if isinstance(loud, dict) else loud,
        ai_content=item.get("ai_content"), bpm=item.get("bpm"), key=item.get("key"),
    )


def soundza_search(cfg: CortexConfig, q: str, *, type: str | None = None,
                   good_for: list[str] | None = None, mood: list[str] | None = None,
                   min_duration: float | None = None, max_duration: float | None = None,
                   pack: str | None = None, fit_seconds: float | None = None,
                   min_bpm: float | None = None, max_bpm: float | None = None,
                   per_page: int | None = None, refresh: bool = False) -> list[Candidate]:
    params: dict = {"q": q, "per_page": min(per_page or cfg.sound.candidates_per_query, 50)}
    if type:
        params["type"] = type.lower()
    if good_for:
        params["good_for"] = ",".join(good_for)
    if mood:
        params["mood"] = ",".join(mood)
    for name, value in (("min_duration", min_duration), ("max_duration", max_duration), ("pack", pack),
                        ("fit_seconds", fit_seconds), ("min_bpm", min_bpm), ("max_bpm", max_bpm)):
        if value is not None:
            params[name] = value
    data = _cached(cfg, "soundza", f"{SOUNDZA_API}/sounds/search", params,
                   bool(api_key("SOUNDZA_API_KEY")), refresh)
    return [_soundza(s) for s in data.get("sounds", [])]


def sfxmint_search(cfg: CortexConfig, q: str, *, max_duration: float | None = None,
                   limit: int | None = None, refresh: bool = False) -> list[Candidate]:
    params: dict = {"q": q, "limit": min(limit or cfg.sound.candidates_per_query, 50),
                    "format": "wav", "response": "structured"}
    if max_duration is not None:
        params["max_duration_ms"] = int(max_duration * 1000)
    data = _cached(cfg, "sfxmint", f"{SFXMINT_API}/search", params,
                   bool(api_key("SFXMINT_API_KEY")), refresh)
    out = []
    for c in data.get("candidates", []):
        title = c.get("title") or c["slug"]
        out.append(Candidate(
            provider="sfxmint", id=c["slug"], title=title, description=(c.get("description") or "")[:300],
            duration=(c.get("duration_ms") or 0) / 1000, type=c.get("category"),
            tags=list(c.get("tags") or []), license_name="CC0", license_url=CC0_URL,
            attribution=f'"{title}" — SFXMint (CC0)', page_url=c.get("page_url") or "",
            preview_url=c.get("mp3_url"), download_url=c.get("wav_url") or "",
        ))
    return out


def soundza_removals(cfg: CortexConfig, since: str) -> list[str]:
    ids: list[str] = []
    url, params = f"{SOUNDZA_API}/removals", {"since": since}
    headers = _auth("SOUNDZA_API_KEY")
    for _ in range(1000):
        data = request_json(cfg, url, params=params, headers=headers)
        ids += [r["id"] for r in data.get("removals", [])]
        nxt = data.get("next_page")
        if not nxt:
            break
        url, params = nxt, None
    return ids
