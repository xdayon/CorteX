"""Short, face-safe hook headline overlay for vertical clips."""

from __future__ import annotations

import os
import re
import tempfile
import textwrap

def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _clean_hook(text: str, max_chars: int = 72) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = text.replace("{", "").replace("}", "")
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _wrap_hook(text: str, max_line_chars: int = 25) -> str:
    lines = textwrap.wrap(
        text,
        width=max_line_chars,
        break_long_words=False,
        break_on_hyphens=False,
    )
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    return r"\N".join(lines[:2])


def write_hook_ass(
    hook_text: str,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    duration: float = 3.4,
) -> str:
    """Create a compact two-line headline in the upper safe zone."""
    hook_text = _clean_hook(hook_text)
    if not hook_text:
        raise ValueError("hook_text is empty")

    font_size = max(44, min(72, round(width * 0.058)))
    margin_v = max(100, round(height * 0.075))
    wrapped = _wrap_hook(hook_text, max_line_chars=26 if width >= 900 else 22)
    # \fad is subtle enough to feel designed without delaying comprehension.
    dialogue = r"{\fad(140,220)\blur0.25}" + wrapped
    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,Montserrat,{font_size},&H00FFFFFF,&H00FFFFFF,&HCC090B10,&H99090B10,-1,0,0,0,100,100,0,0,3,3,1,8,90,90,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 10,{_ass_time(0.04)},{_ass_time(duration)},Hook,,0,0,0,,{dialogue}
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass)
    return output_path


def _escape_filter_path(path: str) -> str:
    return os.path.abspath(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def overlay_hook(
    input_path: str,
    output_path: str,
    hook_text: str,
    target_dims: tuple[int, int] = (1080, 1920),
    duration: float = 3.4,
) -> str:
    """Burn the hook headline without touching the existing audio track."""
    hook_text = _clean_hook(hook_text)
    if not hook_text:
        return input_path

    from services.encoder import get_video_decode_flags
    from services.media_probe import run_ffmpeg_with_fallback

    fd, ass_path = tempfile.mkstemp(prefix="podcli_hook_", suffix=".ass")
    os.close(fd)
    try:
        write_hook_ass(
            hook_text,
            ass_path,
            width=int(target_dims[0]),
            height=int(target_dims[1]),
            duration=max(1.5, min(float(duration), 6.0)),
        )
        vf = f"ass='{_escape_filter_path(ass_path)}'"
        run_ffmpeg_with_fallback(
            [
                "ffmpeg", "-y",
                *get_video_decode_flags(),
                "-i", input_path,
                "-vf", vf,
            ],
            ["-c:a", "copy", "-movflags", "+faststart"],
            output_path,
            label="hook_overlay",
        )
        return output_path
    finally:
        try:
            os.remove(ass_path)
        except OSError:
            pass
