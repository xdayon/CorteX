"""flow_pack.md: what to generate in Google Flow, and how long each clip should be."""
from __future__ import annotations

from pathlib import Path

from cortex.config import CortexConfig
from cortex.lessons.common import LessonError
from cortex.lessons.spec import LessonSpec
from cortex.lessons.timeline import Timings, flow_request, seconds_used, shot_windows


def flow_requests(cfg: CortexConfig, spec: LessonSpec, timings: Timings) -> list[dict]:
    lc = cfg.lessons
    rows = []
    for shot, window in zip(spec.shots, shot_windows(spec, timings, lc.xfade_seconds)):
        if not shot.flow:
            continue
        row = {"shot": shot.id, "clip": shot.flow.clip, "needed": window.needed, "variations": shot.flow.variations,
               "prompt": shot.flow.prompt or "", "request": None, "used": None, "error": None}
        try:
            row["request"] = flow_request(shot.id, window.needed, lc.flow_durations, lc.flow_max_retime)
            row["used"] = seconds_used(row["request"], window.needed)
        except LessonError as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows


def write_flow_pack(cfg: CortexConfig, directory: Path, spec: LessonSpec, timings: Timings) -> list[dict]:
    rows = flow_requests(cfg, spec, timings)
    lines = [f"# Flow pack: {spec.title}", ""]
    for r in rows:
        lines.append(f"## Clip {r['clip']} (shot {r['shot']})")
        if r["error"]:
            lines.append(f"- NOT GENERATABLE AS ONE CLIP: {r['error']}")
        else:
            lines.append(f"- Request duration: {r['request']:g} s")
            lines.append(f"- Seconds actually used: {r['used']:g} s (window incl. crossfade pads: {r['needed']:.2f} s)")
        lines += [f"- Variations: {r['variations']}", "", "```", r["prompt"] or "(no prompt yet)", "```", ""]
    (directory / "flow_pack.md").write_text("\n".join(lines), encoding="utf-8")
    return rows
