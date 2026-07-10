"""Video cutting helpers — extract time ranges and stitch them together.

Extracted from video_processor.py. These are the two entry points used
by the clip generator to slice the source video before any cropping or
caption rendering.
"""

from __future__ import annotations

import os

from services.encoder import get_video_decode_flags
from services.media_probe import FFMPEG_TIMEOUT, run_ffmpeg_with_fallback
from utils.proc import run as proc_run


def cut_segment(
    input_path: str,
    output_path: str,
    start_second: float,
    end_second: float,
) -> str:
    """Extract a single time segment from a video file.

    Uses `-ss` before `-i` with re-encoding for frame-accurate
    timestamps. Encodes with the best available HW encoder (NVENC on
    NVIDIA) and decodes via NVDEC when present; falls back to libx264.
    """
    duration = end_second - start_second

    return run_ffmpeg_with_fallback(
        [
            "ffmpeg", "-y",
            *get_video_decode_flags(),
            "-ss", str(start_second),
            "-i", input_path,
            "-t", str(duration),
        ],
        [
            "-c:a", "aac", "-b:a", "192k",
            "-avoid_negative_ts", "make_zero",
        ],
        output_path,
        label="cut",
    )


def cut_multi_segment(
    input_path: str,
    output_path: str,
    segments: list[dict],
    default_crossfade: float = 0.06,
) -> str:
    """Cut multiple ranges and join them with sample-safe micro transitions.

    segments: [{"start": 10.5, "end": 25.0}, {"start": 30.2, "end": 45.0}]

    Each segment is cut individually with frame-accurate encoding. Joins use a
    very short equal-power audio crossfade plus a matching micro dissolve. This
    prevents waveform discontinuities/clicks and hides same-camera jump cuts.
    A stream-copy hard concat remains as a defensive fallback.
    """
    if len(segments) == 1:
        return cut_segment(
            input_path, output_path, segments[0]["start"], segments[0]["end"]
        )

    work_dir = os.path.dirname(output_path) or "."
    part_paths: list[str] = []
    concat_file = os.path.join(work_dir, "_concat_parts.txt")

    try:
        for i, seg in enumerate(segments):
            part_path = os.path.join(work_dir, f"_part_{i}.mp4")
            cut_segment(input_path, part_path, seg["start"], seg["end"])
            part_paths.append(part_path)

        durations = [float(s["end"]) - float(s["start"]) for s in segments]
        transition_durations: list[float] = []
        for i, segment in enumerate(segments[:-1]):
            transition = segment.get("transition") or {}
            requested = float(transition.get("duration", default_crossfade) or 0.0)
            safe = min(max(0.025, requested), 0.18, durations[i] * 0.20, durations[i + 1] * 0.20)
            transition_durations.append(safe)

        input_args = ["ffmpeg", "-y"]
        for path in part_paths:
            input_args.extend(["-i", path])

        filters: list[str] = []
        for i in range(len(part_paths)):
            filters.append(f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[v{i}]")
            filters.append(
                f"[{i}:a]aresample=async=1:first_pts=0,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
                f"[a{i}]"
            )

        video_label = "v0"
        audio_label = "a0"
        cumulative = durations[0]
        for i in range(1, len(part_paths)):
            fade = transition_durations[i - 1]
            offset = max(0.01, cumulative - fade)
            next_video = f"vx{i}"
            next_audio = f"ax{i}"
            filters.append(
                f"[{video_label}][v{i}]xfade=transition=fade:duration={fade:.3f}:"
                f"offset={offset:.3f}[{next_video}]"
            )
            filters.append(
                f"[{audio_label}][a{i}]acrossfade=d={fade:.3f}:c1=tri:c2=tri[{next_audio}]"
            )
            video_label = next_video
            audio_label = next_audio
            cumulative += durations[i] - fade

        try:
            run_ffmpeg_with_fallback(
                [
                    *input_args,
                    "-filter_complex", ";".join(filters),
                    "-map", f"[{video_label}]",
                    "-map", f"[{audio_label}]",
                ],
                [
                    "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart",
                ],
                output_path,
                label="professional_join",
            )
        except Exception:
            # Keep export reliable on unusual codecs/old ffmpeg builds.
            with open(concat_file, "w", encoding="utf-8") as f:
                for p in part_paths:
                    f.write(f"file '{os.path.abspath(p)}'\n")
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-c", "copy",
                "-movflags", "+faststart",
                output_path,
            ]
            result = proc_run(cmd, timeout=FFMPEG_TIMEOUT, check=False)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg concat failed: {result.stderr[-500:]}")

        return output_path

    finally:
        for p in part_paths:
            if os.path.exists(p):
                os.remove(p)
        if os.path.exists(concat_file):
            os.remove(concat_file)
