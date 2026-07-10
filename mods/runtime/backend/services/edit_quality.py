"""Fail-safe validation for edit plans before expensive rendering."""

from __future__ import annotations

from typing import Optional
import json
import os
import subprocess

from services.audio_editing import EditingProfile


def validate_edit_plan(
    segments: list[dict],
    words: list[dict],
    profile: EditingProfile,
    hook_text: Optional[str] = None,
) -> dict:
    issues: list[dict] = []
    if not segments:
        issues.append({"severity": "error", "code": "empty_timeline"})
        return {"passed": False, "issues": issues, "profile": profile.name}

    for index, segment in enumerate(segments):
        start = float(segment.get("start", 0))
        end = float(segment.get("end", 0))
        if end <= start:
            issues.append({
                "severity": "error", "code": "invalid_segment", "segment": index
            })
            continue
        if end - start < 0.50:
            issues.append({
                "severity": "error", "code": "segment_too_short", "segment": index
            })

        for boundary_name, boundary in (("in", start), ("out", end)):
            for word in words:
                word_start = float(word.get("start", 0))
                word_end = float(word.get("end", 0))
                if word_start + 0.015 < boundary < word_end - 0.015:
                    issues.append({
                        "severity": "error",
                        "code": "boundary_inside_word",
                        "segment": index,
                        "boundary": boundary_name,
                        "word": str(word.get("word", "")),
                        "time": round(boundary, 3),
                    })
                    break

    total_duration = sum(
        max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
        for s in segments
    )
    max_cuts = max(1, int(total_duration / 30.0 * profile.max_cuts_per_30s) + 1)
    if len(segments) - 1 > max_cuts:
        issues.append({
            "severity": "warning",
            "code": "excessive_cut_density",
            "cuts": len(segments) - 1,
            "recommended_max": max_cuts,
        })

    clean_hook = " ".join(str(hook_text or "").split())
    if len(clean_hook) > 72:
        issues.append({"severity": "warning", "code": "hook_too_long"})
    if clean_hook and len(clean_hook.split()) > 12:
        issues.append({"severity": "warning", "code": "hook_too_wordy"})

    return {
        "passed": not any(i["severity"] == "error" for i in issues),
        "issues": issues,
        "profile": profile.name,
    }


def validate_render_file(path: str, expected_duration: Optional[float] = None) -> dict:
    """Verify that the final artifact has playable audio/video and sane timing."""
    issues: list[dict] = []
    if not os.path.exists(path) or os.path.getsize(path) < 10_000:
        return {
            "passed": False,
            "issues": [{"severity": "error", "code": "missing_or_empty_render"}],
        }
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,width,height",
                "-of", "json", path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-300:])
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        kinds = {s.get("codec_type") for s in streams}
        if "video" not in kinds:
            issues.append({"severity": "error", "code": "video_stream_missing"})
        if "audio" not in kinds:
            issues.append({"severity": "error", "code": "audio_stream_missing"})
        duration = float((data.get("format") or {}).get("duration") or 0.0)
        if duration <= 0:
            issues.append({"severity": "error", "code": "invalid_duration"})
        if expected_duration and duration > 0:
            tolerance = max(0.60, expected_duration * 0.04)
            if abs(duration - expected_duration) > tolerance:
                issues.append({
                    "severity": "error",
                    "code": "timeline_duration_mismatch",
                    "expected": round(expected_duration, 3),
                    "actual": round(duration, 3),
                })
        return {
            "passed": not any(i["severity"] == "error" for i in issues),
            "issues": issues,
            "duration": round(duration, 3),
        }
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        return {
            "passed": False,
            "issues": [{
                "severity": "error",
                "code": "render_probe_failed",
                "detail": str(exc),
            }],
        }
