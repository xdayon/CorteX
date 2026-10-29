"""python -m cortex.lessons {init,align,check,ingest,flow-pack,render,sfx-auto,assemble} <slug>"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from cortex.config import CortexConfig, load_config
from cortex.lessons.common import LessonError, probe
from cortex.lessons.spec import (Beat, FlowRef, LessonSpec, Music, SfxCue, Shot, SoundDesign, lesson_dir, load_spec,
                                 save_spec)
from cortex.sound.http import SoundError
from cortex.lessons.timeline import (load_timings, retime_factor, retime_outside_range, shot_windows)


def paragraphs(text: str) -> list[str]:
    return [" ".join(p.split()) for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]


def cmd_init(cfg: CortexConfig, args) -> int:
    directory = lesson_dir(cfg, args.slug)
    if (directory / "lesson.json").exists() and not args.force:
        raise LessonError(f"{directory}/lesson.json exists; pass --force to overwrite")
    directory.mkdir(parents=True, exist_ok=True)
    script = Path(args.script).expanduser()
    paras = paragraphs(script.read_text(encoding="utf-8"))
    if not paras:
        raise LessonError("script has no paragraphs")
    shutil.copyfile(script, directory / "script.txt")
    narration = Path(args.narration).expanduser()
    shutil.copyfile(narration, directory / narration.name)
    music = None
    if args.music:
        src = Path(args.music).expanduser()
        shutil.copyfile(src, directory / src.name)
        music = Music(file=src.name, credit=args.music_credit or "")
    beats = [Beat(id=f"S{i:02d}", text=t) for i, t in enumerate(paras, start=1)]
    shots = [Shot(id=b.id, beats=[b.id], kind="flow", flow=FlowRef(clip=f"{i:02d}")) for i, b in enumerate(beats, start=1)]
    sound = SoundDesign() if args.promo else None
    spec = LessonSpec(slug=args.slug, title=args.title or args.slug, language=args.language or cfg.lessons.language,
                      aspect=args.aspect, narration=narration.name, music=music, sound=sound, beats=beats, shots=shots)
    save_spec(directory, spec)
    print(f"created {directory} with {len(beats)} beats (one flow shot per beat; edit lesson.json)")
    return 0


def cmd_sfx_auto(cfg: CortexConfig, args) -> int:
    from cortex.lessons.sound import auto_cues
    from cortex.sound.kit import load_kit
    directory = lesson_dir(cfg, args.slug)
    spec = load_spec(directory)
    sound = spec.sound or SoundDesign()
    if sound.sfx and not args.force:
        raise LessonError(f"lesson.json already has {len(sound.sfx)} sound cues; pass --force to replace them")
    kit = load_kit(cfg, sound.kit or cfg.sound.default_kit)
    cues = auto_cues(spec, {name for name, role in kit.roles.items() if role.sounds})
    if not cues:
        raise LessonError(f"kit '{kit.name}' has no whoosh/riser/impact sounds yet; pick some first")
    spec.sound = sound.model_copy(update={"sfx": [SfxCue(**c) for c in cues]})
    save_spec(directory, spec)
    print(f"wrote {len(cues)} cues from kit '{kit.name}' (edit lesson.json → sound.sfx)")
    return 0


def cmd_align(cfg: CortexConfig, args) -> int:
    directory = lesson_dir(cfg, args.slug)
    device = args.device or cfg.transcription.device
    if device == "cuda":
        from cortex.transcribe.cuda_env import apply_cuda_env, find_cuda_library_dirs
        dirs = [str(d) for d in find_cuda_library_dirs(Path(sys.prefix))]
        loaded = os.environ.get("LD_LIBRARY_PATH", "").split(":")
        if dirs and not all(d in loaded for d in dirs) and os.environ.get("CORTEX_CUDA_ENV_BOOTSTRAPPED") != "1":
            env = dict(os.environ)
            apply_cuda_env(Path(sys.prefix), env)
            env["CORTEX_CUDA_ENV_BOOTSTRAPPED"] = "1"
            os.execve(sys.executable, [sys.executable, "-m", "cortex.lessons", *sys.argv[1:]], env)
    if args.out == "timings.json" and (directory / "timings.json").exists() and not args.force:
        raise LessonError("timings.json exists; pass --force or --out FILE")
    from cortex.lessons.align import align_lesson
    timings = align_lesson(cfg, directory, device=device, out_name=args.out)
    print(f"wrote {directory / args.out} (device {device}); total {timings['total']} s")
    return 0


def cmd_check(cfg: CortexConfig, args) -> int:
    from cortex.lessons.flow_pack import flow_requests
    directory = lesson_dir(cfg, args.slug)
    spec = load_spec(directory)
    timings = load_timings(directory / "timings.json")
    windows = shot_windows(spec, timings, cfg.lessons.xfade_seconds)
    requests = {r["shot"]: r for r in flow_requests(cfg, spec, timings)}
    print(f"{spec.title}: {len(spec.beats)} beats, {len(spec.shots)} shots, total {timings.total}s")
    print(f"{'shot':6}{'kind':8}{'start':>8}{'dur':>8}  detail")
    for shot, w in zip(spec.shots, windows):
        detail = ""
        if shot.flow:
            r = requests[shot.id]
            clip = directory / "flow" / f"{shot.flow.clip}.mp4"
            detail = f"clip {shot.flow.clip} " + (f"request {r['request']:g}s" if r["request"] else "SPLIT THE BEAT")
            t_out = shot.flow.out if shot.flow.out is not None else (probe(cfg, clip).duration if clip.exists() else None)
            if t_out is not None:  # < 1 speeds the clip up (set flow.out), > 1 slows it down
                f = retime_factor(w.needed, shot.flow.in_, t_out)
                detail += f" retime {f:.2f}x" + (" !" if retime_outside_range(f) else "")
            detail += "" if clip.exists() else " (clip not ingested)"
        if shot.manim and not (directory / "renders" / shot.manim.render).exists():
            detail += f" {shot.manim.render} not rendered"
        print(f"{shot.id:6}{shot.kind:8}{w.start:8.2f}{w.duration:8.2f}  {detail}")
    if spec.sound and spec.sound.sfx:
        from cortex.lessons.sound import resolve_cues
        print(f"\n{'cue':22}{'hit':>8}{'starts':>8}{'gain':>7}  sound")
        for p in resolve_cues(cfg, directory, spec, timings):
            print(f"{p.at:22}{p.time:8.2f}{p.delay:8.2f}{p.gain_db:7.1f}  {p.title} ({p.ref})")
    return 0


def cmd_ingest(cfg, args) -> int:
    from cortex.lessons.ingest import ingest_lesson
    directory = lesson_dir(cfg, args.slug)
    for num, info in ingest_lesson(cfg, directory, load_spec(directory), args.source).items():
        print(f"{num}: {info['source']} {info['duration']}s {info['width']}x{info['height']}")
    return 0


def cmd_flow_pack(cfg, args) -> int:
    from cortex.lessons.flow_pack import write_flow_pack
    directory = lesson_dir(cfg, args.slug)
    rows = write_flow_pack(cfg, directory, load_spec(directory), load_timings(directory / "timings.json"))
    for r in rows:
        print(f"clip {r['clip']} ({r['shot']}): " + (f"request {r['request']:g}s, use {r['used']:g}s" if r["request"] else r["error"]))
    print(f"wrote {directory / 'flow_pack.md'}")
    return 0


def cmd_render(cfg, args) -> int:
    from cortex.lessons.manim import render_lesson
    directory = lesson_dir(cfg, args.slug)
    statuses, problems = render_lesson(cfg, directory, load_spec(directory), load_timings(directory / "timings.json"),
                                       args.shots or None, args.force)
    for shot, status in statuses.items():
        print(f"{shot}: {status}")
    for p in problems:
        print(f"! duration mismatch {p}")
    return 1 if problems else 0


def cmd_assemble(cfg, args) -> int:
    from cortex.lessons.assemble import assemble
    directory = lesson_dir(cfg, args.slug)
    report = assemble(cfg, directory, load_spec(directory), load_timings(directory / "timings.json"), args.force)
    print(f"{report['output']}: {report['duration']} s, {report['integrated_lufs']} LUFS, {report['true_peak_dbtp']} dBTP")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cortex.lessons")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, fn, **kw):
        p = sub.add_parser(name, **kw)
        p.add_argument("slug")
        p.add_argument("--force", action="store_true")
        p.set_defaults(fn=fn)
        return p

    p = add("init", cmd_init)
    p.add_argument("--script", required=True)
    p.add_argument("--narration", required=True)
    p.add_argument("--music")
    p.add_argument("--music-credit")
    p.add_argument("--title")
    p.add_argument("--language", help="narration language (default lessons.language)")
    p.add_argument("--aspect", choices=["16:9", "9:16"], default="16:9")
    p.add_argument("--promo", action="store_true", help="sound design on: kit effects and music ducked under the voice")
    p = add("align", cmd_align)
    p.add_argument("--device", choices=["cuda", "cpu"])
    p.add_argument("--out", default="timings.json")
    add("check", cmd_check)
    p = add("ingest", cmd_ingest)
    p.add_argument("--source", type=Path, help="folder with the Flow downloads (default lessons.ingest_dir)")
    add("flow-pack", cmd_flow_pack)
    p = add("render", cmd_render)
    p.add_argument("shots", nargs="*")
    add("assemble", cmd_assemble)
    add("sfx-auto", cmd_sfx_auto)
    args = parser.parse_args(argv)
    try:
        return args.fn(load_config(args.config), args)
    except (LessonError, SoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
