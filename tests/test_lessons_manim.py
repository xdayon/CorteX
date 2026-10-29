from __future__ import annotations

import json

from lessons_media import ff, small_config

from cortex.lessons import manim
from cortex.lessons.spec import Beat, LessonSpec, ManimRef, Shot
from cortex.lessons.timeline import Timings


def _setup(tmp_path):
    lesson = tmp_path / "lesson"
    (lesson / "scenes").mkdir(parents=True)
    (lesson / "scenes" / "s01_x.py").write_text("# scene")
    (lesson / "scenes" / "brand.py").write_text("# brand")
    (lesson / "timings.json").write_text(json.dumps({"audio_offset": 0.8, "total": 2, "scenes": {}}))
    shot = Shot(id="A", beats=["B1"], kind="manim", manim=ManimRef(scene="s01_x.py", cls="SceneA", render="s01.mp4"))
    spec = LessonSpec(slug="t", title="t", narration="n.mp3", beats=[Beat(id="B1", text="x")], shots=[shot])
    return lesson, shot, spec


def test_command_is_an_argument_array(tmp_path):
    cfg = small_config(tmp_path)
    lesson, shot, _ = _setup(tmp_path)
    cmd = manim.podman_command(cfg, (lesson / "scenes").resolve(), shot)
    assert cmd[:4] == ["podman", "run", "--rm", "--userns=keep-id"]
    assert cmd[cmd.index("-v") + 1].endswith("/scenes:/manim:z")
    assert cmd[cmd.index("-r") + 1] == "320,180" and cmd[cmd.index("--fps") + 1] == "15"
    assert "--disable_caching" in cmd and "-t" not in cmd
    assert cmd[-2:] == ["s01_x.py", "SceneA"]
    shot.manim.transparent = True
    assert "-t" in manim.podman_command(cfg, lesson / "scenes", shot)


def test_render_then_cache_skip(tmp_path, monkeypatch):
    cfg = small_config(tmp_path)
    lesson, shot, spec = _setup(tmp_path)
    calls = []

    def fake(cmd, log, timeout=0):
        calls.append(cmd)
        out = lesson / "scenes" / "media_final_s01_x" / "videos" / "s01_x" / "180p15"
        out.mkdir(parents=True, exist_ok=True)
        ff("-f", "lavfi", "-i", "color=black:s=320x180:r=15:d=2", "-pix_fmt", "yuv420p", out / "SceneA.mp4")
        return True

    monkeypatch.setattr(manim, "run_container", fake)
    timings = Timings(audio_offset=0.8, total=2, scenes={"B1": {"start": 0, "duration": 2}})
    statuses, problems = manim.render_lesson(cfg, lesson, spec, timings)
    assert statuses == {"A": "rendered"} and problems == [] and len(calls) == 1
    assert manim.render_lesson(cfg, lesson, spec, timings)[0] == {"A": "cached"} and len(calls) == 1
    (lesson / "scenes" / "s01_x.py").write_text("# changed")
    assert manim.render_lesson(cfg, lesson, spec, timings)[0] == {"A": "rendered"} and len(calls) == 2
    short = Timings(audio_offset=0.8, total=5, scenes={"B1": {"start": 0, "duration": 5}})
    assert manim.render_lesson(cfg, lesson, spec, short)[1]


def test_cues_resolve_from_the_shot_start_and_reach_the_key(tmp_path, monkeypatch):
    import pytest

    from cortex.lessons.common import LessonError
    from cortex.lessons.spec import Cue

    cfg = small_config(tmp_path)
    lesson, _, _ = _setup(tmp_path)
    (lesson / "alignment.json").write_text(json.dumps({"words": [[0.2, 0.5, "olá"], [2.4, 2.8, "Estilo,"]]}))
    timings = Timings(audio_offset=0.8, total=6, scenes={"B1": {"start": 0, "duration": 3},
                                                          "B2": {"start": 3, "duration": 3}})
    plain = Shot(id="A", beats=["B1"], kind="manim", manim=ManimRef(scene="s01_x.py", cls="SceneA", render="a.mp4"))
    cued = Shot(id="END", beats=["B2"], kind="manim", manim=ManimRef(
        scene="s01_x.py", cls="SceneB", render="b.mp4",
        cues={"word": Cue(at="word:B2:estilo", offset=-0.1), "cut": Cue(at="shot:END"), "late": Cue(at="t:5.5")}))
    spec = LessonSpec(slug="t", title="t", narration="n.mp3", beats=[Beat(id="B1", text="x"), Beat(id="B2", text="y")],
                      shots=[plain, cued])
    info = manim.shot_info(cfg, lesson, spec, timings)
    assert info["A"] == {"start": 0, "duration": 3, "cues": {}}
    assert info["END"] == {"start": 3, "duration": 3, "cues": {"word": 0.1, "cut": 0.0, "late": 2.5}}

    # A lesson without cues keeps its old cache keys; a moved cue re-renders the scene.
    scenes = lesson / "scenes"
    (scenes / "timings.json").write_text("{}")
    old = manim.hash_parts(manim.sha256_file(scenes / "s01_x.py"), manim.sha256_file(scenes / "brand.py"),
                           manim.sha256_file(scenes / "timings.json"), cfg.lessons.manim_image, "SceneA", 320, 180, 15, False)
    assert manim.render_key(cfg, scenes, plain, (320, 180), {}) == old
    keyed = manim.render_key(cfg, scenes, cued, (320, 180), info["END"]["cues"])
    assert keyed != manim.render_key(cfg, scenes, cued, (320, 180), {**info["END"]["cues"], "word": 0.2})

    def fake(cmd, log, timeout=0):
        cls = cmd[-1]
        out = scenes / "media_final_s01_x" / "videos" / "s01_x" / "180p15"
        out.mkdir(parents=True, exist_ok=True)
        ff("-f", "lavfi", "-i", "color=black:s=320x180:r=15:d=3", "-pix_fmt", "yuv420p", out / f"{cls}.mp4")
        return True

    monkeypatch.setattr(manim, "run_container", fake)
    manim.render_lesson(cfg, lesson, spec, timings)
    assert json.loads((scenes / "shots.json").read_text())["END"]["cues"]["word"] == 0.1

    cued.manim.cues["late"] = Cue(at="t:6.5")
    with pytest.raises(LessonError, match="outside the shot"):
        manim.shot_info(cfg, lesson, spec, timings)
    with pytest.raises(ValueError, match="no such beat"):
        LessonSpec(slug="t", title="t", narration="n.mp3", beats=[Beat(id="B1", text="x")], shots=[
            Shot(id="A", beats=["B1"], kind="manim",
                 manim=ManimRef(scene="s.py", cls="S", render="s.mp4", cues={"x": Cue(at="word:B9:oi")}))])
