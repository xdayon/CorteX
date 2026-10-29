from __future__ import annotations

import json
import subprocess

from lessons_media import ff, small_config

from cortex.lessons.assemble import assemble, measure_loudness
from cortex.lessons.common import probe
from cortex.lessons.spec import (Beat, FlowRef, LessonSpec, ManimRef, Music, Shot)
from cortex.lessons.timeline import load_timings


def _lesson(tmp_path):
    lesson = tmp_path / "lesson"
    for sub in ("flow", "renders"):
        (lesson / sub).mkdir(parents=True)
    size = "s=320x180:r=15"
    ff("-f", "lavfi", "-i", f"testsrc=d=6:{size}", "-pix_fmt", "yuv420p", lesson / "flow" / "01.mp4")
    ff("-f", "lavfi", "-i", f"testsrc2=d=2:{size}", "-pix_fmt", "yuv420p", lesson / "renders" / "s02.mp4")
    ff("-f", "lavfi", "-i", "color=c=red@0.5:s=320x180:r=15:d=2,format=argb", "-c:v", "qtrle",
       lesson / "renders" / "s03.mov")
    ff("-f", "lavfi", "-i", "sine=f=440:d=5", "-ar", 44100, lesson / "narration.wav")
    ff("-f", "lavfi", "-i", "anoisesrc=d=8:c=pink:a=0.3", "-ar", 44100, lesson / "music.wav")
    (lesson / "timings.json").write_text(json.dumps({
        "audio_offset": 0.8, "total": 6.0,
        "scenes": {"B1": {"start": 0, "duration": 2}, "B2": {"start": 2, "duration": 2}, "B3": {"start": 4, "duration": 2}}}))
    beats = [Beat(id=f"B{i}", text="x") for i in (1, 2, 3)]
    shots = [
        Shot(id="A", beats=["B1"], kind="flow", flow=FlowRef(clip="01", out=2.0)),
        Shot(id="B", beats=["B2"], kind="manim", manim=ManimRef(scene="s.py", cls="S", render="s02.mp4")),
        Shot(id="C", beats=["B3"], kind="hybrid", flow=FlowRef(clip="01", **{"in": 2.0}, out=4.0),
             manim=ManimRef(scene="s.py", cls="S", render="s03.mov", transparent=True)),
    ]
    spec = LessonSpec(slug="demo", title="demo", narration="narration.wav", beats=beats, shots=shots,
                      music=Music(file="music.wav", credit="noise"))
    return lesson, spec


def test_assemble_hits_duration_loudness_and_cache(tmp_path):
    cfg = small_config(tmp_path)
    lesson, spec = _lesson(tmp_path)
    timings = load_timings(lesson / "timings.json")
    report = assemble(cfg, lesson, spec, timings)
    out = lesson / "export" / "demo_180p.mp4"
    assert out.exists()
    assert abs(probe(cfg, out).duration - 6.0) <= 0.05
    assert abs(report["integrated_lufs"] - cfg.lessons.target_lufs) <= 0.5
    assert report["true_peak_dbtp"] <= cfg.lessons.true_peak_db + 0.05  # measured on the AAC that ships
    assert [s["retime"] for s in report["shots"]] == [1.1, None, 1.1]
    assert (lesson / "export" / "report.json").exists()
    assert abs(float(measure_loudness(cfg, out)["input_i"]) - cfg.lessons.target_lufs) <= 0.5

    stamp = {p: p.stat().st_mtime_ns for p in [*(lesson / "build").iterdir(), out]}
    assemble(cfg, lesson, spec, timings)
    assert stamp == {p: p.stat().st_mtime_ns for p in stamp}


def test_hybrid_without_shade_is_a_plain_overlay(tmp_path):
    from cortex.lessons.assemble import make_segment
    from cortex.lessons.timeline import shot_windows

    cfg = small_config(tmp_path)
    lesson, spec = _lesson(tmp_path)
    ff("-f", "lavfi", "-i", "color=c=white@0.0:s=320x180:r=15:d=2,format=argb", "-c:v", "qtrle",
       lesson / "renders" / "s03.mov")  # an empty overlay: whatever changes the picture is the shade
    window = shot_windows(spec, load_timings(lesson / "timings.json"), cfg.lessons.xfade_seconds)[2]
    shot = spec.shots[2]
    build = lesson / "build"
    build.mkdir()

    def right_luma():
        path = make_segment(cfg, lesson, shot, window, build, False, (320, 180))[0]
        out = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(path), "-frames:v", "1", "-vf",
                              "crop=40:180:270:0,scale=1:1,format=gray", "-f", "rawvideo", "-"],
                             capture_output=True, check=True, timeout=60).stdout
        return out[0]

    shaded = right_luma()
    shot.manim.shade = False
    plain = right_luma()
    plate = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(build / "plate_C.mp4"), "-frames:v", "1",
                            "-vf", "crop=40:180:270:0,scale=1:1,format=gray", "-f", "rawvideo", "-"],
                           capture_output=True, check=True, timeout=60).stdout[0]
    assert plain - shaded >= 25 and abs(plain - plate) <= 3
