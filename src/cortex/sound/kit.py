"""Named kits: roles (whoosh, riser, ...) with search queries and the curated sounds, plus audition page."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from cortex.config import CortexConfig
from cortex.sound import library, providers
from cortex.sound.analysis import _run
from cortex.sound.http import SoundError
from cortex.sound.library import LibrarySound
from cortex.sound.providers import Candidate

NAME = re.compile(r"^[A-Za-z0-9_-]+$")
PREVIEW_HOSTS = {"media.soundza.com", "sfxmint.com"}
GAP = 0.6


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["soundza", "sfxmint"]
    q: str
    type: str | None = None
    good_for: list[str] = []
    mood: list[str] = []
    min_duration: float | None = None
    max_duration: float | None = None


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    queries: list[Query] = []
    sounds: list[str] = []


class Kit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    name: str = Field(pattern=NAME.pattern)
    title: str
    roles: dict[str, Role] = {}


def _sz(q, max_duration, good_for=(), min_duration=None) -> Query:
    return Query(provider="soundza", q=q, type="sound_effect", good_for=list(good_for),
                 min_duration=min_duration, max_duration=max_duration)


def _sfx(q, max_duration) -> Query:
    return Query(provider="sfxmint", q=q, max_duration=max_duration)


DEFAULT_ROLES: dict[str, Role] = {
    "whoosh": Role(label="Whoosh de transição", queries=[_sz("whoosh", 2.0, ["transition"]), _sfx("whoosh transition", 2.0)]),
    "swipe": Role(label="Swipe rápido", queries=[_sz("fast swipe swoosh", 0.8, ["transition"])]),
    "riser": Role(label="Riser (subida)", queries=[_sz("riser", 8, ["build_up"], 2), _sfx("riser", 8)]),
    "impact": Role(label="Impacto", queries=[_sz("cinematic impact hit", 4, ["impact_on_beat", "reveal"]), _sfx("impact", 4)]),
    "braam": Role(label="Braam de trailer", queries=[_sz("braam", 6), _sfx("braam", 6)]),
    "sub_drop": Role(label="Sub drop", queries=[_sz("sub drop boom", 4)]),
    "downlifter": Role(label="Downlifter", queries=[_sz("downlifter", 4)]),
    "glitch": Role(label="Glitch digital", queries=[_sz("digital glitch", 1.5)]),
    "shutter": Role(label="Clique de câmera", queries=[_sz("camera shutter", 1.5)]),
    "fabric": Role(label="Tecido/roupa", queries=[_sz("cloth fabric swish", 2)]),
    "static": Role(label="Estática de rádio", queries=[_sz("radio static interference", 3)]),
    "logo": Role(label="Assinatura/logo", queries=[_sz("logo reveal sting", 5, ["title_or_logo"])]),
    "tape_stop": Role(label="Tape stop", queries=[_sz("tape stop", 2)]),
}


def kit_dir(cfg: CortexConfig, name: str) -> Path:
    if not NAME.match(name):
        raise SoundError(f"invalid kit name: {name!r} (use letters, digits, - and _)")
    return cfg.sound.dir / "kits" / name


def load_kit(cfg: CortexConfig, name: str) -> Kit:
    path = kit_dir(cfg, name) / "kit.json"
    if not path.is_file():
        raise SoundError(f"kit {name!r} not found; run `python -m cortex.sound kit init {name}`")
    return Kit.model_validate_json(path.read_text(encoding="utf-8"))


def save_kit(cfg: CortexConfig, kit: Kit) -> None:
    directory = kit_dir(cfg, kit.name)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "kit.json").write_text(kit.model_dump_json(indent=2), encoding="utf-8")


def init_kit(cfg: CortexConfig, name: str, title: str, force: bool = False) -> Kit:
    if (kit_dir(cfg, name) / "kit.json").exists() and not force:
        raise SoundError(f"kit {name!r} exists; pass --force to overwrite")
    kit = Kit(name=name, title=title or name, roles={k: v.model_copy(deep=True) for k, v in DEFAULT_ROLES.items()})
    save_kit(cfg, kit)
    return kit


def _run_query(cfg: CortexConfig, query: Query, refresh: bool) -> list[Candidate]:
    if query.provider == "sfxmint":
        return providers.sfxmint_search(cfg, query.q, max_duration=query.max_duration, refresh=refresh)
    return providers.soundza_search(
        cfg, query.q, type=query.type, good_for=query.good_for or None, mood=query.mood or None,
        min_duration=query.min_duration, max_duration=query.max_duration, refresh=refresh)


def collect_candidates(cfg: CortexConfig, kit: Kit, roles: list[str] | None = None,
                       refresh: bool = False) -> dict[str, list[Candidate]]:
    unknown = [r for r in roles or [] if r not in kit.roles]
    if unknown:
        raise SoundError(f"unknown role(s): {', '.join(unknown)}")
    path = kit_dir(cfg, kit.name) / "candidates.json"
    stored: dict[str, list[Candidate]] = {}
    if path.is_file():
        old = json.loads(path.read_text(encoding="utf-8"))
        stored = {r: [Candidate.model_validate(c) for c in cs] for r, cs in old.items() if r in kit.roles}
    for role_name in roles or list(kit.roles):
        role = kit.roles[role_name]
        seen, found = set(role.sounds), []
        for query in role.queries:
            for cand in _run_query(cfg, query, refresh):
                if cand.ref not in seen:
                    seen.add(cand.ref)
                    found.append(cand)
        stored[role_name] = found
    ordered = {r: stored[r] for r in kit.roles if r in stored}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({r: [c.model_dump() for c in cs] for r, cs in ordered.items()},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    (path.parent / "audition.html").write_text(render_audition(kit, ordered), encoding="utf-8")
    return ordered


def _preview_ok(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in PREVIEW_HOSTS


def render_audition(kit: Kit, candidates: dict[str, list[Candidate]]) -> str:
    e = html.escape
    sections = []
    for role_name, cands in candidates.items():
        rows = []
        for i, c in enumerate(cands, start=1):
            player = (f'<audio controls preload="none" src="{e(c.preview_url, quote=True)}"></audio>'
                      if _preview_ok(c.preview_url) else '<span class="muted">sem prévia</span>')
            badge = '<span class="badge">IA</span>' if c.ai_content else ""
            meta = " · ".join(e(x) for x in (c.provider, c.pack or "", c.license_name,
                                             c.creator or "") if x)
            rows.append(
                f'<li class="row"><label class="pick"><input type="checkbox" data-role="{e(role_name, quote=True)}" '
                f'data-n="{i}"><span class="n">{i}</span></label><div class="body">'
                f'<div class="title">{e(c.title)} {badge}</div>'
                f'<div class="muted">{c.duration:.2f} s · {meta}</div>{player}</div></li>')
        label = kit.roles[role_name].label if role_name in kit.roles else role_name
        sections.append(f'<section id="{e(role_name, quote=True)}"><h2>{e(label)} <code>{e(role_name)}</code></h2>'
                        f'<ul>{"".join(rows) or "<li class=muted>sem candidatos</li>"}</ul></section>')
    nav = " ".join(f'<a href="#{e(r, quote=True)}">{e(r)} ({len(c)})</a>' for r, c in candidates.items())
    return PAGE.replace("{{TITLE}}", e(kit.title)).replace("{{KIT}}", e(kit.name)).replace(
        "{{NAV}}", nav).replace("{{BODY}}", "\n".join(sections))


PAGE = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Audição — {{TITLE}}</title>
<style>
:root { --bg:#f6f6f4; --fg:#1d1d1b; --muted:#5f5f5a; --card:#fff; --line:#d9d9d3; --accent:#0b5fd0; --badge:#8a3ffc; }
@media (prefers-color-scheme: dark) { :root { --bg:#141414; --fg:#ececea; --muted:#a0a09a; --card:#1e1e1e; --line:#363636; --accent:#6aa6ff; --badge:#b794ff; } }
* { box-sizing: border-box; }
body { margin:0; padding:16px; background:var(--bg); color:var(--fg); font:16px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 860px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 12px; }
h2 { font-size: 18px; margin: 24px 0 8px; }
code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px; color: var(--muted); }
textarea { width:100%; min-height:120px; background:var(--card); color:var(--fg); border:1px solid var(--line); border-radius:8px; padding:8px; font:13px/1.4 ui-monospace, Menlo, Consolas, monospace; }
button { margin-top:8px; padding:10px 16px; font-size:16px; border:0; border-radius:8px; background:var(--accent); color:#fff; }
ul { list-style:none; margin:0; padding:0; display:grid; gap:8px; }
.row { display:flex; gap:12px; background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px; }
.pick { display:flex; flex-direction:column; align-items:center; gap:4px; min-width:36px; }
.pick input { width:22px; height:22px; }
.n { font-weight:600; }
.body { flex:1; min-width:0; }
.title { font-weight:600; overflow-wrap:anywhere; }
.muted { color:var(--muted); font-size:13px; overflow-wrap:anywhere; }
.badge { display:inline-block; font-size:12px; padding:0 6px; border-radius:6px; border:1px solid var(--badge); color:var(--badge); }
audio { width:100%; margin-top:6px; }
nav { display:flex; flex-wrap:wrap; gap:6px 12px; margin-top:16px; font-size:14px; }
nav a { color:var(--accent); }
section { scroll-margin-top:12px; }
</style></head><body><main>
<h1>{{TITLE}}</h1>
<p class="muted">Ouça, marque os sons que quer no kit (2 a 4 por grupo bastam) e cole os comandos
abaixo no chat com o Claude, ou rode no terminal do CorteX.</p>
<textarea id="out" readonly placeholder="Nada marcado ainda"></textarea>
<button id="copy" type="button">Copiar comandos</button>
<nav>{{NAV}}</nav>
{{BODY}}
</main>
<script>
const KIT = "{{KIT}}";
const out = document.getElementById("out");
function update() {
  const by = {};
  document.querySelectorAll("input[type=checkbox]:checked").forEach(function (c) {
    (by[c.dataset.role] = by[c.dataset.role] || []).push(c.dataset.n);
  });
  out.value = Object.keys(by).map(function (r) {
    return "python -m cortex.sound kit pick " + KIT + " " + r + " " + by[r].join(" ");
  }).join("\\n");
}
document.addEventListener("change", update);
document.getElementById("copy").addEventListener("click", function () {
  out.select();
  if (navigator.clipboard) navigator.clipboard.writeText(out.value);
});
</script></body></html>
"""


def pick(cfg: CortexConfig, name: str, role: str, positions: list[int]) -> list[LibrarySound]:
    kit = load_kit(cfg, name)
    if role not in kit.roles:
        raise SoundError(f"unknown role: {role}")
    path = kit_dir(cfg, name) / "candidates.json"
    if not path.is_file():
        raise SoundError(f"no candidates for kit {name!r}; run `kit candidates {name}` first")
    cands = json.loads(path.read_text(encoding="utf-8")).get(role, [])
    chosen = []
    for n in positions:
        if not 1 <= n <= len(cands):
            raise SoundError(f"position {n} out of range for {role} (1-{len(cands)})")
        chosen.append(Candidate.model_validate(cands[n - 1]))
    sounds = [library.ensure(cfg, c) for c in chosen]
    current = kit.roles[role].sounds
    for s in sounds:
        if s.ref not in current:
            current.append(s.ref)
    save_kit(cfg, kit)
    return sounds


def drop(cfg: CortexConfig, name: str, role: str, ref_or_position: str) -> str:
    kit = load_kit(cfg, name)
    if role not in kit.roles:
        raise SoundError(f"unknown role: {role}")
    sounds = kit.roles[role].sounds
    if ref_or_position.isdigit():
        n = int(ref_or_position)
        if not 1 <= n <= len(sounds):
            raise SoundError(f"position {n} out of range for {role} (1-{len(sounds)})")
        ref = sounds[n - 1]
    else:
        ref = ref_or_position
        if ref not in sounds:
            raise SoundError(f"{ref} is not in role {role}")
    sounds.remove(ref)
    save_kit(cfg, kit)
    return ref


def render_reel(cfg: CortexConfig, name: str) -> tuple[Path, Path]:
    kit = load_kit(cfg, name)
    items = [(r, library.load_sound(cfg, ref)) for r, role in kit.roles.items() for ref in role.sounds]
    if not items:
        raise SoundError(f"kit {name!r} has no sounds; pick some first")
    inputs: list = []
    chains, labels, lines, start = [], [], [], 0.0
    for i, (role, s) in enumerate(items):
        gain = cfg.sound.reel_lufs - s.max_momentary_lufs if s.max_momentary_lufs is not None else 0.0
        delay = int(round(start * 1000))
        inputs += ["-i", s.path(cfg)]
        chains.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,volume={gain:.2f}dB,"
                      f"adelay={delay}|{delay}[a{i}]")
        labels.append(f"[a{i}]")
        lines.append(f"{int(start // 60):02d}:{start % 60:04.1f}  {role}  {s.title}  ({s.ref})")
        start += s.duration + GAP
    graph = ";".join(chains) + f";{''.join(labels)}amix=inputs={len(items)}:normalize=0:duration=longest[out]"
    directory = kit_dir(cfg, name)
    wav, txt = directory / "reel.wav", directory / "reel.txt"
    res = _run([cfg.render.ffmpeg, "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
                "-ar", 48000, "-ac", 2, wav], 600)
    if res.returncode != 0:
        raise SoundError(f"ffmpeg reel failed: {res.stderr.decode('utf-8', 'replace')[-300:]}")
    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return wav, txt


def apply_removals(cfg: CortexConfig, ids: list[str]) -> list[str]:
    refs = {f"soundza:{i}" for i in ids}
    removed: set[str] = set()
    for ref in refs:
        if library.remove(cfg, ref):
            removed.add(ref)
    kits_dir = cfg.sound.dir / "kits"
    for kit_json in sorted(kits_dir.glob("*/kit.json")) if kits_dir.is_dir() else []:
        kit = Kit.model_validate_json(kit_json.read_text(encoding="utf-8"))
        changed = False
        for role in kit.roles.values():
            hit = [r for r in role.sounds if r in refs]
            if hit:
                role.sounds = [r for r in role.sounds if r not in refs]
                removed.update(hit)
                changed = True
        if changed:
            save_kit(cfg, kit)
    return sorted(removed)
