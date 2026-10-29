"""python -m cortex.sound {login,search,kit,removals}"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from cortex.config import CortexConfig, load_config
from cortex.sound import kit as kits
from cortex.sound import providers
from cortex.sound.http import SoundError, request_json
from cortex.sound.providers import SOUNDZA_API, Candidate


def table(cands: list[Candidate]) -> None:
    for i, c in enumerate(cands, start=1):
        extra = " | ".join(x for x in (c.pack, c.license_name) if x)
        print(f"{i:>3}  {c.provider:<7} {c.duration:>5.2f}s  {c.title[:50]:<50}  {extra[:60]}")


def write_env_key(value: str, env: Path = Path(".env")) -> None:
    lines = env.read_text(encoding="utf-8").splitlines() if env.is_file() else []
    new = f"SOUNDZA_API_KEY={value}"
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == "SOUNDZA_API_KEY":
            lines[i] = new
            break
    else:
        lines.append(new)
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env.chmod(0o600)


def cmd_login(cfg: CortexConfig, args) -> int:
    info = request_json(cfg, f"{SOUNDZA_API}/auth/device", method="POST", body={
        "client_name": f"CorteX on {socket.gethostname()}", "project_kind": "own_content",
        "project": "Personal video editor for my own promo and lesson videos (Dayon News)"})
    print(f"Open {info['verification_uri_complete']} and approve code {info['user_code']}")
    interval = float(info.get("interval", 5))
    deadline = time.monotonic() + float(info.get("expires_in", 600))
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            res = request_json(cfg, f"{SOUNDZA_API}/auth/token", method="POST",
                               body={"device_code": info["device_code"]})
        except SoundError as err:
            text = str(err)
            if "authorization_pending" in text:
                continue
            if "slow_down" in text:
                interval += 5
                continue
            raise
        if res.get("access_token"):
            write_env_key(res["access_token"])
            print(f"saved SOUNDZA_API_KEY={res['access_token'][:12]}... to .env")
            return 0
        err = res.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval = float(res.get("interval", interval + 5))
            continue
        raise SoundError(f"login failed: {err or 'unexpected response'}")
    raise SoundError("login code expired")


def cmd_search(cfg: CortexConfig, args) -> int:
    if args.provider == "soundza":
        cands = providers.soundza_search(
            cfg, args.query, type=args.type, good_for=args.good_for.split(",") if args.good_for else None,
            min_duration=args.min_duration, max_duration=args.max_duration, per_page=args.limit,
            refresh=args.refresh)
    else:
        cands = providers.sfxmint_search(cfg, args.query, max_duration=args.max_duration,
                                         limit=args.limit, refresh=args.refresh)
    table(cands)
    return 0


def cmd_kit(cfg: CortexConfig, args) -> int:
    action = args.action
    if action == "init":
        kit = kits.init_kit(cfg, args.name, args.title or args.name, args.force)
        print(f"created kit {kit.name} with {len(kit.roles)} roles")
    elif action == "candidates":
        kit = kits.load_kit(cfg, args.name)
        found = kits.collect_candidates(cfg, kit, args.roles or None, args.refresh)
        for role, cands in found.items():
            print(f"{role}: {len(cands)} candidates")
        print(f"open {kits.kit_dir(cfg, args.name) / 'audition.html'}")
    elif action == "pick":
        for s in kits.pick(cfg, args.name, args.role, [int(n) for n in args.positions]):
            print(f"{s.ref}  {s.duration:.2f}s  anchor {s.anchor:.3f}s ({s.anchor_source})  {s.title}")
    elif action == "drop":
        print(f"dropped {kits.drop(cfg, args.name, args.role, args.target)}")
    elif action == "show":
        from cortex.sound import library
        kit = kits.load_kit(cfg, args.name)
        print(f"{kit.name}: {kit.title}")
        for role, r in kit.roles.items():
            print(f"[{role}] {r.label} ({len(r.sounds)})")
            for i, ref in enumerate(r.sounds, start=1):
                try:
                    s = library.load_sound(cfg, ref)
                    print(f"{i:>3}  {s.provider:<7} {s.duration:>5.2f}s  {s.title[:50]:<50}  {s.license_name}")
                except SoundError:
                    print(f"{i:>3}  {ref}  (missing from library)")
    elif action == "reel":
        wav, txt = kits.render_reel(cfg, args.name)
        print(f"{wav}\n{txt}")
    return 0


def cmd_removals(cfg: CortexConfig, args) -> int:
    state = cfg.sound.dir / "removals_checked.json"
    since = args.since
    if not since:
        try:
            since = json.loads(state.read_text(encoding="utf-8"))["date"]
        except (OSError, ValueError, KeyError):
            since = (date.today() - timedelta(days=30)).isoformat()
    ids = providers.soundza_removals(cfg, since)
    removed = kits.apply_removals(cfg, ids)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"date": date.today().isoformat()}), encoding="utf-8")
    print(f"{len(ids)} removals since {since}; removed locally: {', '.join(removed) or 'none'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cortex.sound")
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login")
    s = sub.add_parser("search")
    s.add_argument("provider", choices=["soundza", "sfxmint"])
    s.add_argument("query")
    s.add_argument("--type")
    s.add_argument("--good-for")
    s.add_argument("--min-duration", type=float)
    s.add_argument("--max-duration", type=float)
    s.add_argument("--limit", type=int)
    s.add_argument("--refresh", action="store_true")
    k = sub.add_parser("kit")
    ks = k.add_subparsers(dest="action", required=True)
    p = ks.add_parser("init")
    p.add_argument("name")
    p.add_argument("--title")
    p.add_argument("--force", action="store_true")
    p = ks.add_parser("candidates")
    p.add_argument("name")
    p.add_argument("roles", nargs="*")
    p.add_argument("--refresh", action="store_true")
    p = ks.add_parser("pick")
    p.add_argument("name")
    p.add_argument("role")
    p.add_argument("positions", nargs="+", type=int)
    p = ks.add_parser("drop")
    p.add_argument("name")
    p.add_argument("role")
    p.add_argument("target")
    for action in ("show", "reel"):
        ks.add_parser(action).add_argument("name")
    r = sub.add_parser("removals")
    r.add_argument("--since")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config) if args.config else load_config()
    handlers = {"login": cmd_login, "search": cmd_search, "kit": cmd_kit, "removals": cmd_removals}
    try:
        return handlers[args.command](cfg, args)
    except SoundError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
