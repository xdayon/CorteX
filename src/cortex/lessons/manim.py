"""Manim renders through podman (port of the pilot's render_final.sh), cached by content hash."""
from __future__ import annotations

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cortex.config import CortexConfig
from cortex.lessons.common import LessonError, cache_store, cache_valid, canvas, hash_parts, probe, sha256_file
from cortex.lessons.sound import Anchors
from cortex.lessons.spec import LessonSpec, Shot
from cortex.lessons.timeline import Timings, shot_windows

RETRIES = 3
RENDER_TIMEOUT = 1800
DURATION_TOLERANCE = 0.2
# fc-cache so the mounted .fonts are visible; exec "$@" keeps every manim argument a separate argv item.
CONTAINER_PREFIX = ["sh", "-c", 'fc-cache -f >/dev/null 2>&1; exec "$@"', "sh"]


def podman_command(cfg: CortexConfig, scenes: Path, shot: Shot, size: tuple[int, int] | None = None) -> list[str]:
    lc, m = cfg.lessons, shot.manim
    w, h = size or (lc.width, lc.height)
    stem = Path(m.scene).stem
    return [
        "podman", "run", "--rm", "--userns=keep-id", "-v", f"{scenes}:/manim:z", lc.manim_image,
        *CONTAINER_PREFIX, "manim", "-r", f"{w},{h}", "--fps", str(lc.fps), "--disable_caching",
        "--progress_bar", "none", *(["-t"] if m.transparent else []),
        "--media_dir", f"media_final_{stem}", m.scene, m.cls,
    ]


def run_container(cmd: list[str], log: Path, timeout: float = RENDER_TIMEOUT) -> bool:
    with log.open("w") as handle:
        try:
            return subprocess.run(cmd, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout).returncode == 0
        except subprocess.TimeoutExpired:
            return False


def render_key(cfg: CortexConfig, scenes: Path, shot: Shot, size: tuple[int, int] | None = None,
               cues: dict[str, float] | None = None) -> str:
    lc, m = cfg.lessons, shot.manim
    w, h = size or (lc.width, lc.height)
    return hash_parts(sha256_file(scenes / m.scene), sha256_file(scenes / "brand.py"), sha256_file(scenes / "timings.json"),
                      lc.manim_image, m.cls, w, h, lc.fps, m.transparent, *([cues] if cues else []))


def shot_info(cfg: CortexConfig, directory: Path, spec: LessonSpec, timings: Timings) -> dict[str, dict]:
    """Per Manim shot: its window and each cue in seconds from the shot start (scenes read it as shots.json)."""
    anchors = Anchors(cfg, directory, spec, timings)
    windows = {w.id: w for w in shot_windows(spec, timings, cfg.lessons.xfade_seconds)}
    info = {}
    for s in spec.shots:
        if s.manim:
            w = windows[s.id]
            cues = {name: round(anchors.time(c.at, c.offset) - w.start, 3) for name, c in s.manim.cues.items()}
            bad = {name: t for name, t in cues.items() if not 0 <= t <= w.duration}
            if bad:
                raise LessonError(f"shot {s.id}: cues outside the shot (0–{w.duration:.2f}s): {bad}")
            info[s.id] = {"start": w.start, "duration": w.duration, "cues": cues}
    return info


def render_shot(cfg: CortexConfig, directory: Path, shot: Shot, force: bool = False,
                size: tuple[int, int] | None = None, cues: dict[str, float] | None = None) -> str:
    """Returns 'cached' or 'rendered'; raises LessonError when every retry fails."""
    lc, m = cfg.lessons, shot.manim
    size = size or (lc.width, lc.height)
    scenes, renders, check = (directory / "scenes").resolve(), directory / "renders", directory / "check"
    renders.mkdir(exist_ok=True)
    check.mkdir(exist_ok=True)
    if not (scenes / m.scene).exists():
        raise LessonError(f"shot {shot.id}: missing scene file {scenes / m.scene}")
    shutil.copyfile(directory / "timings.json", scenes / "timings.json")  # brand.py reads it next to itself
    out = renders / m.render
    key = render_key(cfg, scenes, shot, size, cues)
    if cache_valid(out, key, force):
        return "cached"
    stem = Path(m.scene).stem
    log = check / f"render_{stem}.log"
    cmd = podman_command(cfg, scenes, shot, size)
    if not any(run_container(cmd, log) for _ in range(RETRIES)):
        raise LessonError(f"shot {shot.id}: manim failed after {RETRIES} tries; see {log}")
    produced = list((scenes / f"media_final_{stem}" / "videos" / stem / f"{size[1]}p{lc.fps}").glob(f"{m.cls}.*"))
    if not produced:
        raise LessonError(f"shot {shot.id}: manim produced no {m.cls} output; see {log}")
    shutil.copyfile(produced[0], out)
    cache_store(out, key)
    return "rendered"


def render_lesson(cfg: CortexConfig, directory: Path, spec: LessonSpec, timings: Timings,
                  only: list[str] | None = None, force: bool = False) -> tuple[dict[str, str], list[str]]:
    """Render manim/hybrid shots in parallel; returns ({shot: status}, duration mismatch messages)."""
    shots = [s for s in spec.shots if s.manim and (not only or s.id in only)]
    unknown = set(only or []) - {s.id for s in spec.shots if s.manim}
    if unknown:
        raise LessonError(f"not manim shots: {sorted(unknown)}")
    size = canvas(cfg, spec.aspect)
    info = shot_info(cfg, directory, spec, timings)
    if shots:
        (directory / "scenes").mkdir(exist_ok=True)
        (directory / "scenes" / "shots.json").write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    with ThreadPoolExecutor(max_workers=cfg.lessons.manim_workers) as pool:
        statuses = dict(zip((s.id for s in shots), pool.map(
            lambda s: render_shot(cfg, directory, s, force, size, info[s.id]["cues"]), shots)))
    windows = {w.id: w for w in shot_windows(spec, timings, cfg.lessons.xfade_seconds)}
    problems = []
    for s in shots:
        actual = probe(cfg, directory / "renders" / s.manim.render).duration
        if abs(actual - windows[s.id].duration) > DURATION_TOLERANCE:
            problems.append(f"{s.id}: render lasts {actual:.2f}s, shot window is {windows[s.id].duration:.2f}s")
    return statuses, problems
