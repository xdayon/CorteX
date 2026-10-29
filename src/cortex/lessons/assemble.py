"""Deterministic assembly: plates -> segments -> crossfaded cut -> audio mix -> graded export.

Every stage is cached by a content key (input hashes + parameters) in a `<output>.hash` sidecar, so a copied
or touched tree does not rebuild. Each stage key embeds the keys of the stages it consumes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from cortex.config import CortexConfig
from cortex.lessons.common import (ENCODE, LessonError, cache_store, cache_valid, canvas, ffmpeg, hash_parts, probe,
                                   run, sha256_file)
from cortex.lessons.cutout import cutout
from cortex.lessons.sound import PlacedSfx, credits_text, dip_expr, music_source, resolve_cues, sfx_chain
from cortex.lessons.spec import LessonSpec, Shot
from cortex.lessons.titles import build_ass, filter_path
from cortex.lessons.timeline import (ShotWindow, Timings, flow_request, retime_factor, retime_outside_range,
                                     shot_windows)

TP_MARGIN_DB = 1.5  # first limiter setting; the mix loop then tightens it on the measured AAC true peak
AAC = ["-c:a", "aac", "-b:a", "192k"]
DUCK = "sidechaincompress=threshold=0.05:ratio=4:attack=20:release=400"  # ~8 dB under a -14 LUFS voice
GRAIN_FILTER = "noise=alls=4:allf=t,vignette=angle=PI/5,format=yuv420p"
_sha_cache: dict[tuple[str, int, int], str] = {}


def file_key(path: Path) -> str:
    stat = path.stat()
    token = (str(path), stat.st_size, stat.st_mtime_ns)
    if token not in _sha_cache:
        _sha_cache[token] = sha256_file(path)
    return _sha_cache[token]


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _stage(out: Path, key: str, force: bool, build) -> str:
    if not cache_valid(out, key, force):
        build()
        cache_store(out, key)
    return key


def make_plate(cfg: CortexConfig, directory: Path, shot: Shot, window: ShotWindow, build: Path, force: bool,
               size: tuple[int, int]) -> tuple[Path, str, float]:
    lc, flow = cfg.lessons, shot.flow
    w, h = size
    src = directory / "flow" / f"{flow.clip}.mp4"
    if not src.exists():
        raise LessonError(f"shot {shot.id}: missing {src}; run `ingest`")
    t_in = flow.in_
    t_out = flow.out if flow.out is not None else probe(cfg, src).duration
    length = window.needed
    factor = retime_factor(length, t_in, t_out)
    if retime_outside_range(factor):
        log(f"! {shot.id}: clip {flow.clip} retimed {factor:.2f}x to fill {length:.2f}s; plan a second shot instead")
    out = build / f"plate_{shot.id}.mp4"
    key = hash_parts("plate-v2", file_key(src), t_in, t_out, length, lc.fps, w, h)

    def build_it() -> None:
        # fps after minterpolate: its last timestamps leave a gap that made tpad+trim drop two frames per shot.
        motion = (f"setpts=(PTS-STARTPTS)*{factor:.5f},minterpolate=fps={lc.fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:"
                  f"vsbmc=1,fps={lc.fps}" if factor > 1.02 else f"setpts=(PTS-STARTPTS)*{factor:.5f},fps={lc.fps}")
        # Cover the frame: a clip of another aspect (16:9 Flow clip in a 9:16 promo) is centre-cropped.
        vf = (f"{motion},scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},"
              f"tpad=stop_mode=clone:stop_duration=1,trim=duration={length},setsar=1")
        ffmpeg(cfg, "-ss", t_in, "-t", t_out - t_in, "-i", src, "-an", "-vf", vf, *ENCODE, "-preset", "fast", out)

    return out, _stage(out, key, force, build_it), factor


def ensure_gradient(cfg: CortexConfig, build: Path, size: tuple[int, int]) -> Path:
    w, h = size
    grad = build / f"gradient_{w}x{h}.png"
    if not grad.exists():
        a = "255*0.62*(clip((X/W-0.35)/0.3,0,1)^2*(3-2*clip((X/W-0.35)/0.3,0,1)))"
        ffmpeg(cfg, "-f", "lavfi", "-i", f"color=black:s={w}x{h},format=rgba",
               "-vf", f"geq=r=0:g=0:b=0:a='{a}'", "-frames:v", "1", grad)
    return grad


def ensure_glow(cfg: CortexConfig, build: Path, size: tuple[int, int], cy: int) -> Path:
    """Black frame with a warm antique-gold light centred behind an image."""
    w, h = size
    glow = build / f"glow_{w}x{h}_{cy}.png"
    if not glow.exists():
        g = f"exp(-((X-{w / 2})^2+(Y-{cy})^2)/(2*{0.17 * h:.0f}^2))"
        ffmpeg(cfg, "-f", "lavfi", "-i", f"color=black:s={w}x{h},format=rgb24",
               "-vf", f"geq=r='12+70*{g}':g='9+52*{g}':b='6+26*{g}'", "-frames:v", "1", glow)
    return glow


def image_size(cfg: CortexConfig, path: Path) -> tuple[int, int]:
    out = run([cfg.render.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
               "-of", "csv=p=0", path], capture=True, timeout=60).stdout.strip()
    w, _, h = out.partition(",")
    return int(w), int(h)


def make_image_segment(cfg: CortexConfig, directory: Path, shot: Shot, window: ShotWindow, build: Path,
                       force: bool, size: tuple[int, int]) -> tuple[Path, str]:
    lc, im = cfg.lessons, shot.image
    src = directory / im.file
    if not src.exists():
        raise LessonError(f"shot {shot.id}: missing {src}")
    w, h = size
    out, length = build / f"seg_{shot.id}.mp4", window.needed
    key = hash_parts("seg-image-v2", file_key(src), im.model_dump(), length, lc.fps, w, h)

    def build_it() -> None:
        iw, ih = image_size(cfg, src)
        width0 = round(w * im.width / 2) * 2
        cy = round(im.center_y * h)
        bg = (["-loop", 1, "-framerate", lc.fps, "-t", length, "-i", ensure_glow(cfg, build, size, cy)] if im.glow
              else ["-f", "lavfi", "-i", f"color=black:s={w}x{h}:r={lc.fps}:d={length}"])
        layer = cutout(cfg, src, im.key, build / f"cutout_{shot.id}.png") if im.key else src
        grow = f"scale=w='trunc({width0}*(1+{im.zoom - 1:.4f}*t/{length})/2)*2':h=-2:eval=frame:flags=lanczos"
        fc = (f"[1]format=rgba,{grow}[img];[0][img]overlay=x='(W-w)/2':y='{cy}-h/2':format=auto,"
              f"trim=duration={length},setsar=1,format=yuv420p[v]")
        log(f"{shot.id}: still {iw}x{ih} → {width0}px wide, push-in {im.zoom:g}x")
        ffmpeg(cfg, *bg, "-loop", 1, "-framerate", lc.fps, "-t", length, "-i", layer, "-filter_complex", fc,
               "-map", "[v]", *ENCODE, "-preset", "fast", "-r", str(lc.fps), out)

    return out, _stage(out, key, force, build_it)


def make_segment(cfg: CortexConfig, directory: Path, shot: Shot, window: ShotWindow, build: Path, force: bool,
                 size: tuple[int, int]) -> tuple[Path, str, float | None]:
    lc = cfg.lessons
    out = build / f"seg_{shot.id}.mp4"
    before, after, length = window.pad_before, window.pad_after, window.needed
    enc = [*ENCODE, "-preset", "fast", "-r", str(lc.fps), out]
    pad = f"tpad=start_duration={before}:stop_duration={after}:start_mode=clone:stop_mode=clone"
    factor = None
    if shot.kind == "image":
        return (*make_image_segment(cfg, directory, shot, window, build, force, size), None)
    if shot.kind == "flow":
        plate, plate_key, factor = make_plate(cfg, directory, shot, window, build, force, size)
        key = hash_parts("seg-flow", plate_key, lc.fps)
        return out, _stage(out, key, force, lambda: ffmpeg(cfg, "-i", plate, "-an", "-vf", "setsar=1", *enc)), factor
    render = directory / "renders" / shot.manim.render
    if not render.exists():
        raise LessonError(f"shot {shot.id}: missing {render}; run `render`")
    if shot.kind == "manim":
        key = hash_parts("seg-manim", file_key(render), before, after, length, lc.fps)

        def build_it() -> None:
            ffmpeg(cfg, "-i", render, "-an", "-vf", f"{pad},trim=duration={length},setsar=1", *enc)

        return out, _stage(out, key, force, build_it), None
    plate, plate_key, factor = make_plate(cfg, directory, shot, window, build, force, size)
    shade = shot.manim.shade
    key = hash_parts("seg-hybrid", plate_key, file_key(render), before, after, length, lc.fps, *([] if shade else ["plain"]))
    if shade:
        inputs = ["-i", plate, "-loop", "1", "-i", ensure_gradient(cfg, build, size), "-i", render]
        fc = (f"[0]eq=brightness=-0.03:saturation=0.92[p];[p][1]overlay[g];"
              f"[2]{pad},format=yuva420p[o];[g][o]overlay=format=auto,trim=duration={length},setsar=1[v]")
    else:
        inputs = ["-i", plate, "-i", render]
        fc = f"[1]{pad},format=yuva420p[o];[0][o]overlay=format=auto,trim=duration={length},setsar=1[v]"

    def build_it() -> None:
        ffmpeg(cfg, *inputs, "-filter_complex", fc, "-map", "[v]", "-t", length, *enc)

    return out, _stage(out, key, force, build_it), factor


def make_cut(cfg: CortexConfig, segs: list[tuple[Path, str]], windows: list[ShotWindow], build: Path, force: bool) -> tuple[Path, str]:
    lc = cfg.lessons
    out = build / "video_cut.mp4"
    key = hash_parts("cut", [k for _, k in segs], [w.start for w in windows], lc.xfade_seconds)

    def build_it() -> None:
        if len(segs) == 1:
            ffmpeg(cfg, "-i", segs[0][0], "-c", "copy", out)
            return
        fc, prev = [], "0:v"
        for i, w in enumerate(windows[1:], start=1):
            fc.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={lc.xfade_seconds}:offset={w.start - lc.xfade_seconds / 2:.3f}[x{i}]")
            prev = f"x{i}"
        inputs = [a for p, _ in segs for a in ("-i", p)]
        ffmpeg(cfg, *inputs, "-filter_complex", ";".join(fc), "-map", f"[{prev}]", *ENCODE, "-preset", "fast", out)

    return out, _stage(out, key, force, build_it)


def measure_loudness(cfg: CortexConfig, src: Path, target: float = -14.0, tp: float = -1.5) -> dict:
    res = run([cfg.render.ffmpeg, "-hide_banner", "-nostats", "-i", src, "-vn", "-af",
               f"loudnorm=I={target}:TP={tp}:LRA=20:print_format=json", "-f", "null", "-"], capture=True)
    text = res.stderr
    return json.loads(text[text.rindex("{"):text.rindex("}") + 1])


def loudnorm_chain(cfg: CortexConfig, src: Path, target: float, pre: str = "") -> str:
    """Two-pass linear loudnorm: measure, then apply as a constant gain."""
    chain = f"{pre}," if pre else ""
    res = run([cfg.render.ffmpeg, "-hide_banner", "-nostats", "-i", src, "-vn", "-af",
               f"{chain}loudnorm=I={target}:TP=-2:LRA=20:print_format=json", "-f", "null", "-"], capture=True).stderr
    m = json.loads(res[res.rindex("{"):res.rindex("}") + 1])
    return (f"{chain}loudnorm=I={target}:TP=-2:LRA=20:linear=true:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
            f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}")


def native_parts(cfg: CortexConfig, directory: Path, spec: LessonSpec,
                 windows: list[ShotWindow]) -> list[tuple[Shot, ShotWindow, Path, float, float, float]]:
    """(shot, window, clip, in, out, retime) for every Flow shot whose own sound joins the bed."""
    if not spec.sound or spec.sound.native_lufs is None:
        return []
    parts = []
    for shot, w in zip(spec.shots, windows):
        if shot.flow and not shot.flow.mute:
            src = directory / "flow" / f"{shot.flow.clip}.mp4"
            t_out = shot.flow.out if shot.flow.out is not None else probe(cfg, src).duration
            parts.append((shot, w, src, shot.flow.in_, t_out, retime_factor(w.needed, shot.flow.in_, t_out)))
    return parts


def make_mix(cfg: CortexConfig, directory: Path, spec: LessonSpec, timings: Timings, build: Path, force: bool,
             sfx: list[PlacedSfx], windows: list[ShotWindow]) -> tuple[Path, str]:
    lc, m = cfg.lessons, spec.music
    narration = directory / spec.narration
    music, _credit = music_source(cfg, directory, m) if m else (None, "")
    for f in (narration, music):
        if f is not None and not f.exists():
            raise LessonError(f"missing {f}")
    total, lead = timings.total, timings.audio_offset
    native = native_parts(cfg, directory, spec, windows)
    dips = dip_expr(cfg, directory, spec, timings)
    duck = bool((music or native) and spec.sound and spec.sound.duck_music)
    music_lufs = lc.ducked_music_lufs if duck else lc.music_lufs
    out, aac = build / "mix.wav", build / "mix.m4a"
    key = hash_parts("mix-v3", AAC, file_key(narration), file_key(music) if music else None, total, lead, lc.voice_lufs,
                     music_lufs, lc.target_lufs, lc.true_peak_db, TP_MARGIN_DB,
                     m.model_dump(exclude={"file", "sound", "credit"}) if m else None, duck,
                     [(file_key(p.path), p.delay, p.head, p.trim, p.gain_db) for p in sfx],
                     spec.sound.native_lufs if native else None, dips,
                     [(file_key(src), t_in, t_out, w.start, w.needed, s.flow.audio_db) for s, w, src, t_in, t_out, _ in native])
    if cache_valid(out, key, force) and aac.exists():
        return aac, key
    voice = loudnorm_chain(cfg, narration, lc.voice_lufs)
    fc = f"[0]{voice},aresample=48000,adelay={int(lead * 1000)}:all=1,apad" + (",asplit=2[v][key];" if duck else "[v];")
    inputs, labels, bed = ["-i", narration], ["[v]"], []
    if music:
        trim = f"atrim={m.start:g}:{m.start + total:g},asetpts=PTS-STARTPTS"
        if duck:  # promo: the music carries the piece and dips under the voice
            chain = loudnorm_chain(cfg, music, music_lufs, trim)
        else:
            # Bed without ducking: compress the classical dynamics, dip the voice band, keep it low and constant.
            chain = loudnorm_chain(cfg, music, music_lufs, f"{trim},acompressor=threshold=-30dB:ratio=3:attack=30:"
                                   f"release=500,equalizer=f=2500:width_type=o:width=1.6:g=-3")
        fc += f"[1]{chain},aresample=48000"
        fc += f",volume={m.gain_db}dB" if m.gain_db else ""
        fc += f",afade=t=in:d={m.fade_in}" if m.fade_in else ""
        fc += f",afade=t=out:st={max(total - m.fade_out, 0)}:d={m.fade_out}" if m.fade_out else ""
        fc += "[m];"
        inputs += ["-i", music]
        bed.append("[m]")
    xf = lc.xfade_seconds
    for shot, w, src, t_in, t_out, factor in native:
        # The clip's own sound, cut and retimed like its picture, crossfaded on the same 0.4 s as the video.
        n = len(inputs) // 2
        chain = loudnorm_chain(cfg, src, spec.sound.native_lufs,
                               f"atrim={t_in}:{t_out},asetpts=PTS-STARTPTS,atempo={1 / factor:.5f}")
        fc += f"[{n}:a]{chain},aresample=48000,aformat=channel_layouts=stereo,atrim=duration={w.needed}"
        fc += f",volume={shot.flow.audio_db}dB" if shot.flow.audio_db else ""
        fc += f",afade=t=in:d={xf}" if w.pad_before else ""
        fc += f",afade=t=out:st={w.needed - xf:.3f}:d={xf}" if w.pad_after else ""
        fc += f",adelay={int(round((w.start - w.pad_before) * 1000))}:all=1[n{n}];"
        inputs += ["-i", src]
        bed.append(f"[n{n}]")
    if bed and (len(bed) > 1 or dips or duck):
        fc += f"{''.join(bed)}amix=inputs={len(bed)}:normalize=0:duration=longest" if len(bed) > 1 else f"{bed[0]}anull"
        # loudnorm/amix hand over frames seconds long and eval=frame reads `t` once per frame: re-chunk to
        # 1024 samples (~21 ms) and rebuild the clock from the sample count before the dip expression.
        fc += f",asetnsamples=n=1024:p=0,asetpts=N/SR/TB,volume='{dips}':eval=frame" if dips else ""
        fc += f"[bedmix];[bedmix][key]{DUCK}[bed];" if duck else "[bed];"
        labels.append("[bed]")
    else:
        labels += bed
    for p in sfx:
        n = len(inputs) // 2
        fc += f"[{n}]{sfx_chain(p)}[s{n}];"
        inputs += ["-i", p.path]
        labels.append(f"[s{n}]")
    if len(labels) > 1:
        fc += f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest"
    else:
        fc += "[v]anull"
    fc += f",apad=whole_dur={total}[a]"
    raw = build / "mix_raw.wav"
    ffmpeg(cfg, *inputs, "-filter_complex", fc, "-map", "[a]", "-t", total, "-ac", 2, "-ar", 48000,
           "-c:a", "pcm_f32le", raw)  # -t, not atrim: loudnorm leaves timestamps atrim would cut by
    limit_db = lc.true_peak_db - TP_MARGIN_DB
    gain = lc.target_lufs - float(measure_loudness(cfg, raw, lc.target_lufs)["input_i"])
    for _ in range(5):
        # Aim on the AAC that ships: the limiter shaves loudness (re-aim the gain) and the encode overshoots
        # the peak on booms (pull the limit down by the measured excess), both from the raw mix.
        ffmpeg(cfg, "-i", raw, "-af", f"volume={gain:.3f}dB,alimiter=limit={10 ** (limit_db / 20):.4f}:level=false",
               "-ar", 48000, out)
        ffmpeg(cfg, "-i", out, *AAC, aac)
        measured = measure_loudness(cfg, aac, lc.target_lufs)
        error = lc.target_lufs - float(measured["input_i"])
        over = float(measured["input_tp"]) - lc.true_peak_db
        if abs(error) <= 0.05 and over <= 0:
            break
        gain += error
        limit_db -= over + 0.1 if over > 0 else 0
    cache_store(out, key)
    return aac, key


def assemble(cfg: CortexConfig, directory: Path, spec: LessonSpec, timings: Timings, force: bool = False) -> dict:
    lc = cfg.lessons
    build, export = directory / "build", directory / "export"
    build.mkdir(exist_ok=True)
    export.mkdir(exist_ok=True)
    windows = shot_windows(spec, timings, lc.xfade_seconds)
    size = canvas(cfg, spec.aspect)
    sfx = resolve_cues(cfg, directory, spec, timings)  # fail on a bad cue before any encode
    segs, shots_report = [], []
    for shot, window in zip(spec.shots, windows):
        path, key, factor = make_segment(cfg, directory, shot, window, build, force, size)
        segs.append((path, key))
        row = {"id": shot.id, "kind": shot.kind, "start": window.start, "duration": window.duration,
               "retime": round(factor, 4) if factor is not None else None, "flow_duration_requested": None}
        if shot.flow:
            try:
                row["flow_duration_requested"] = flow_request(shot.id, window.needed, lc.flow_durations, lc.flow_max_retime)
            except LessonError:
                pass
        shots_report.append(row)
    video, video_key = make_cut(cfg, segs, windows, build, force)
    mix, mix_key = make_mix(cfg, directory, spec, timings, build, force, sfx, windows)
    out = export / f"{spec.slug}_{min(size)}p.mp4"
    vf, titles_key = GRAIN_FILTER, None
    if spec.titles:  # text under the grain, so it sits in the film instead of on top of it
        ass = build_ass(cfg, directory, spec, timings, size)
        ass_path, fonts = build / "titles.ass", Path(cfg.render.fonts_dir)
        ass_path.write_text(ass, encoding="utf-8")
        titles_key = hash_parts(ass, sorted((p.name, p.stat().st_size) for p in fonts.glob("*.[ot]tf")))
        vf = f"subtitles=filename='{filter_path(ass_path)}':fontsdir='{filter_path(fonts)}',{GRAIN_FILTER}"
    final_key = hash_parts("final", video_key, mix_key, GRAIN_FILTER, titles_key)
    report_path = export / "report.json"
    if not cache_valid(out, final_key, force):
        ffmpeg(cfg, "-i", video, "-i", mix, "-vf", vf, "-c:v", "libx264", "-preset", "slow", "-tune", "grain",
               "-crf", 19, "-c:a", "copy", "-shortest", "-movflags", "+faststart", out)  # AAC peak-checked in make_mix
        cache_store(out, final_key)
    previous = json.loads(report_path.read_text()) if report_path.exists() else {}
    if previous.get("final_key") == final_key:
        measured = {k: previous[k] for k in ("duration", "integrated_lufs", "true_peak_dbtp")}
    else:
        loud = measure_loudness(cfg, out, lc.target_lufs, lc.true_peak_db)
        measured = {"duration": round(probe(cfg, out).duration, 3), "integrated_lufs": float(loud["input_i"]),
                    "true_peak_dbtp": float(loud["input_tp"])}
    report = {"final_key": final_key, "output": str(out), **measured, "shots": shots_report,
              "sfx": [{"at": p.at, "ref": p.ref, "title": p.title, "time": p.time, "starts": p.delay,
                       "gain_db": p.gain_db} for p in sfx]}
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    music_credit = music_source(cfg, directory, spec.music)[1] if spec.music else ""
    (export / "credits.txt").write_text(credits_text(music_credit, sfx), encoding="utf-8")
    return report
