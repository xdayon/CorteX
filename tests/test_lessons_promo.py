from __future__ import annotations

import re
import subprocess

import pytest
from lessons_media import ff
from test_lessons_sound import TIMINGS, _cfg, _sound

from cortex.lessons.assemble import assemble
from cortex.lessons.common import LessonError, probe
from cortex.lessons.spec import Beat, FlowRef, ImageRef, LessonSpec, Music, Shot, SoundDesign, Title
from cortex.lessons.titles import STYLES, build_ass

BEATS = [Beat(id="B1", text="hello world"), Beat(id="B2", text="buy the jacket")]


def _spec(**kw):
    shots = [Shot(id="A", beats=["B1"], kind="flow", flow=FlowRef(clip="01", out=3.2)),
             Shot(id="B", beats=["B2"], kind="image",
                  image=ImageRef(file="card.png", key="E5E5E5", width=0.5, center_y=0.5, zoom=1.05))]
    return LessonSpec(slug="promo", title="promo", narration="narration.wav", aspect="9:16", beats=BEATS,
                      shots=shots, **kw)


def test_titles_ass_styles_typewriter_and_box(tmp_path):
    cfg = _cfg(tmp_path)
    spec = _spec(titles=[Title(text="33.7° N", at="t:0.5", until="shot:B", style="coords", effect="type"),
                         Title(text="ESTILO", at="t:3.2", until="end", style="cta_big", effect="track"),
                         Title(text="CUPOM X · 10% OFF", at="t:4", until="end", style="coupon")])
    ass = build_ass(cfg, tmp_path, spec, TIMINGS, (1080, 1920))
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    coupon = next(line for line in ass.splitlines() if line.startswith("Style: coupon"))
    assert "IBM Plex Mono SemiBold" in coupon and coupon.split(",")[15] == "3"  # opaque gold box
    typed = [line for line in ass.splitlines() if "{\\pos(540,221)" in line]
    assert len(typed) == len("33.7° N")  # one event per typed character
    assert all("\\alpha&HFF&" in line for line in typed)  # untyped rest kept in the layout, invisible
    assert typed[-1].split(",")[2] == "0:00:03.00"  # the full line holds until the cut into B
    track = next(line for line in ass.splitlines() if "ESTILO" in line)
    assert "\\fsp" in track and "\\t(0," in track  # letters open up over the whole title
    text = "THE HIDDEN KNOWLEDGE ABOUT IT"
    long = build_ass(cfg, tmp_path, _spec(titles=[Title(text=text, at="t:1", style="line")]), TIMINGS, (1080, 1920))
    fs, fsp = (float(v) for v in re.search(r"\\fs(\d+)\\fsp([\d.]+)", long).groups())
    assert len(text) * (0.6 * fs + fsp) <= 0.8 * 1080 + 1  # shrunk to fit the frame
    for style in STYLES.values():  # every default height inside the Reels/TikTok safe band
        assert 0.10 <= style.y <= 0.78
    with pytest.raises(LessonError, match="unknown title style"):
        build_ass(cfg, tmp_path, _spec(titles=[Title(text="x", at="t:1", style="nope")]), TIMINGS, (1080, 1920))
    with pytest.raises(LessonError, match="ends before it starts"):
        build_ass(cfg, tmp_path, _spec(titles=[Title(text="x", at="t:2", until="t:1")]), TIMINGS, (1080, 1920))


def _luma(path, t, x, y):
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-vf",
                          f"crop=3:3:{x - 1}:{y - 1},scale=1:1,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True, timeout=60).stdout
    return out[0]


def _band(path, start, end, af):
    res = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", f"atrim={start}:{end},{af},volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True, timeout=60)
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", res.stderr).group(1))


def test_image_card_native_sound_dip_and_library_music(tmp_path):
    cfg = _cfg(tmp_path)
    music = _sound(cfg, "m", source="anoisesrc=d=8:c=pink:a=0.3")
    lesson = tmp_path / "lesson"
    (lesson / "flow").mkdir(parents=True)
    ff("-f", "lavfi", "-i", "testsrc=d=6:s=180x320:r=15", "-f", "lavfi", "-i", "sine=f=1000:d=6",
       "-pix_fmt", "yuv420p", "-shortest", lesson / "flow" / "01.mp4")
    ff("-f", "lavfi", "-i", "color=0xE5E5E5:s=200x200,drawbox=x=50:y=50:w=100:h=100:color=black:t=fill,"
       "drawbox=x=88:y=88:w=24:h=24:color=white:t=fill", "-frames:v", 1, lesson / "card.png")
    ff("-f", "lavfi", "-i", "sine=f=440:d=0.5,volume=8", "-ar", 44100, lesson / "narration.wav")  # voice 0.8-1.3 s
    (lesson / "timings.json").write_text(TIMINGS.model_dump_json())
    spec = _spec(music=Music(sound=music, fade_in=0, fade_out=0),
                 sound=SoundDesign(native_lufs=-26, dips=[{"at": "t:4.0", "until": "t:5.0", "db": -30}]))
    assemble(cfg, lesson, spec, TIMINGS)
    out = lesson / "export" / "promo_180p.mp4"
    assert (probe(cfg, out).width, probe(cfg, out).height, round(probe(cfg, out).duration, 2)) == (180, 320, 6.0)
    card = lesson / "build" / "seg_B.mp4"
    assert _luma(card, 1.0, 3, 3) < 40  # the flat grey background is cut out
    assert _luma(card, 1.0, 90, 160) > 180  # the picture itself is there (white square at its centre)
    mix = lesson / "build" / "mix.wav"
    tone = "bandpass=f=1000:width_type=h:w=60,bandpass=f=1000:width_type=h:w=60"
    assert _band(mix, 2.0, 2.7, tone) - _band(mix, 4.0, 5.5, tone) >= 10  # clip sound only under its own shot
    high = "highpass=f=3000:poles=2,highpass=f=3000:poles=2"
    assert _band(mix, 3.3, 3.9, high) - _band(mix, 4.3, 4.9, high) >= 20  # the dip pulls the bed down
    assert "Música: T m — soundza.com" in (lesson / "export" / "credits.txt").read_text()
