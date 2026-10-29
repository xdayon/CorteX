from __future__ import annotations

import json
import re
import subprocess

import pytest
from lessons_media import ff, small_config

from cortex.config import SoundConfig
from cortex.lessons.assemble import assemble
from cortex.lessons.common import LessonError, probe
from cortex.lessons.sound import auto_cues, resolve_cues
from cortex.lessons.spec import Beat, FlowRef, LessonSpec, Music, SfxCue, Shot, SoundDesign
from cortex.lessons.timeline import Timings
from cortex.sound.analysis import analyse
from cortex.sound.kit import Kit, Role, save_kit
from cortex.sound.library import LibrarySound

TIMINGS = Timings(audio_offset=0.8, total=6.0, scenes={"B1": {"start": 0, "duration": 3}, "B2": {"start": 3, "duration": 3}})


def _cfg(tmp_path):
    return small_config(tmp_path).model_copy(update={"sound": SoundConfig(dir=tmp_path / "sound")})


def _sound(cfg, ident, *, anchor=0.3, lufs=-20.0, source=None):
    ref = f"soundza:{ident}"
    folder = cfg.sound.dir / "library" / "soundza"
    folder.mkdir(parents=True, exist_ok=True)
    wav = folder / f"{ident}.wav"
    if source:
        ff("-f", "lavfi", "-i", source, "-ar", 48000, wav)
        info = analyse(cfg, wav)
        anchor, lufs = info["peak_time"], info["max_momentary_lufs"]
    meta = LibrarySound(ref=ref, provider="soundza", id=ident, title=f"T {ident}", file=f"library/soundza/{ident}.wav",
                        duration=1.0, anchor=anchor, anchor_source="peak", max_momentary_lufs=lufs, sha256="x",
                        license_name="Soundza Standard License", page_url=f"https://soundza.com/sound/{ident}",
                        downloaded_at="2026-10-28T00:00:00+00:00")
    (folder / f"{ident}.json").write_text(meta.model_dump_json())
    return ref


def _spec(cues, **kw):
    beats = [Beat(id="B1", text="hello world"), Beat(id="B2", text="buy the jacket")]
    shots = [Shot(id="A", beats=["B1"], kind="flow", flow=FlowRef(clip="01", out=3.0)),
             Shot(id="B", beats=["B2"], kind="flow", flow=FlowRef(clip="01", **{"in": 3.0}, out=6.0))]
    return LessonSpec(slug="promo", title="promo", narration="narration.wav", beats=beats, shots=shots,
                      sound=SoundDesign(kit="k", sfx=[SfxCue(**c) for c in cues]), **kw)


def test_cues_anchor_rotate_trim_and_words(tmp_path):
    cfg = _cfg(tmp_path)
    a, b = _sound(cfg, "a", anchor=0.3, lufs=-20), _sound(cfg, "b", anchor=0.5, lufs=-10)
    save_kit(cfg, Kit(name="k", title="k", roles={"whoosh": Role(label="w", sounds=[a, b])}))
    lesson = tmp_path / "lesson"
    lesson.mkdir()
    (lesson / "alignment.json").write_text(json.dumps({"words": [[2.0, 2.3, "Buy"], [2.4, 2.9, "jacket,"]]}))
    spec = _spec([{"role": "whoosh", "at": "shot:B"}, {"role": "whoosh", "at": "t:0.1"},
                  {"role": "whoosh", "at": "word:B2:JACKET", "align": "start", "gain_db": -3},
                  {"sound": b, "at": "shot:A", "offset": 0.25, "trim": 0.4}])
    placed = resolve_cues(cfg, lesson, spec, TIMINGS)
    assert [p.ref for p in placed] == [a, b, a, b]
    assert (placed[0].time, placed[0].delay, placed[0].head) == (3.0, 2.7, 0.0)  # hit on the cut
    assert (placed[1].delay, placed[1].head) == (0.0, 0.4)  # would start before 0: head cut
    assert placed[2].time == placed[2].delay == 3.2  # word heard at 2.4 + 0.8 lead-in
    assert placed[0].gain_db == cfg.lessons.sfx_lufs + 20 and placed[2].gain_db == cfg.lessons.sfx_lufs + 17
    assert placed[3].time == 0.25 and placed[3].trim == 0.4


def test_cue_errors(tmp_path):
    cfg = _cfg(tmp_path)
    save_kit(cfg, Kit(name="k", title="k", roles={"whoosh": Role(label="w", sounds=[])}))
    with pytest.raises(LessonError, match="no sounds for role"):
        resolve_cues(cfg, tmp_path, _spec([{"role": "whoosh", "at": "shot:B"}]), TIMINGS)
    with pytest.raises(LessonError, match="not in the library"):
        resolve_cues(cfg, tmp_path, _spec([{"sound": "soundza:nope", "at": "shot:B"}]), TIMINGS)
    _sound(cfg, "a")
    with pytest.raises(LessonError, match="after the video ends"):
        resolve_cues(cfg, tmp_path, _spec([{"sound": "soundza:a", "at": "t:7", "align": "start"}]), TIMINGS)
    with pytest.raises(ValueError, match="exactly one"):
        SfxCue(at="shot:B")
    with pytest.raises(ValueError):
        SfxCue(at="cut B", role="whoosh")
    with pytest.raises(ValueError, match="no such shot"):
        _spec([{"role": "whoosh", "at": "shot:Z"}])


def test_auto_cues():
    spec = _spec([])
    assert auto_cues(spec, {"whoosh", "riser", "impact"}) == [
        {"role": "riser", "at": "shot:B"}, {"role": "impact", "at": "shot:B"}]
    three = spec.model_copy(update={"shots": [*spec.shots[:1], spec.shots[0].model_copy(update={"id": "M"}),
                                              spec.shots[1]]})
    assert auto_cues(three, {"whoosh"}) == [{"role": "whoosh", "at": "shot:M"}, {"role": "whoosh", "at": "shot:B"}]


def _band_db(path, start, end, highpass):
    res = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af",
                          f"atrim={start}:{end},highpass=f={highpass}:poles=2,highpass=f={highpass}:poles=2,volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True, timeout=60)
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", res.stderr).group(1))


def test_vertical_promo_ducks_music_and_lands_effects(tmp_path):
    cfg = _cfg(tmp_path)
    whoosh = _sound(cfg, "w", source="anoisesrc=d=0.6:c=pink:a=0.5,afade=t=in:d=0.3,afade=t=out:st=0.3:d=0.3")
    click = _sound(cfg, "c", source="sine=f=8000:d=0.15,volume=8")
    save_kit(cfg, Kit(name="k", title="k", roles={"whoosh": Role(label="w", sounds=[whoosh])}))
    lesson = tmp_path / "lesson"
    (lesson / "flow").mkdir(parents=True)
    ff("-f", "lavfi", "-i", "testsrc=d=6:s=320x180:r=15", "-pix_fmt", "yuv420p", lesson / "flow" / "01.mp4")
    ff("-f", "lavfi", "-i", "sine=f=440:d=2,volume=8", "-ar", 44100, lesson / "narration.wav")  # voice 0.8-2.8 s
    ff("-f", "lavfi", "-i", "anoisesrc=d=8:c=pink:a=0.3", "-ar", 44100, "-ac", 2, lesson / "music.wav")
    (lesson / "timings.json").write_text(TIMINGS.model_dump_json())
    spec = _spec([{"role": "whoosh", "at": "shot:B"}, {"sound": click, "at": "t:4.5", "align": "start"}],
                 aspect="9:16", music=Music(file="music.wav", credit="Pink — test", fade_in=0, fade_out=0))
    report = assemble(cfg, lesson, spec, TIMINGS)
    out = lesson / "export" / "promo_180p.mp4"
    info = probe(cfg, out)
    assert (info.width, info.height) == (180, 320)
    assert abs(info.duration - 6.0) <= 0.05
    assert abs(report["integrated_lufs"] - cfg.lessons.target_lufs) <= 0.5
    assert [(s["ref"], s["time"]) for s in report["sfx"]] == [(whoosh, 3.0), (click, 4.5)]
    mix = lesson / "build" / "mix.wav"
    under_voice, free = _band_db(mix, 1.2, 2.4, 3000), _band_db(mix, 3.6, 4.3, 3000)
    assert free - under_voice >= 4, (under_voice, free)  # music (only source above 3 kHz) dips under the voice
    assert _band_db(mix, 4.5, 4.65, 6000) - _band_db(mix, 3.6, 4.3, 6000) >= 6  # the click lands at 4.5 s
    credits = (lesson / "export" / "credits.txt").read_text()
    assert "Música: Pink — test" in credits and "Efeitos sonoros: soundza.com" in credits and "T c" in credits
