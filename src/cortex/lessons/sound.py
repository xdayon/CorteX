"""Sound design: effect cues (kit role or library sound + anchor) placed on the video timeline."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cortex.config import CortexConfig
from cortex.lessons.align import _norm
from cortex.lessons.common import LessonError
from cortex.lessons.spec import LessonSpec, Music
from cortex.lessons.timeline import Timings, shot_windows
from cortex.sound.http import SoundError
from cortex.sound.kit import load_kit
from cortex.sound.library import LibrarySound, load_sound

PROVIDER_CREDIT = {"soundza": "soundza.com", "sfxmint": "SFXMint (CC0)"}


@dataclass(frozen=True)
class PlacedSfx:
    at: str
    ref: str
    title: str
    time: float  # video second the sound's hit (or its start, align "start") lands on
    delay: float  # video second the audible part starts
    head: float  # seconds cut from the file's start (the sound would begin before 0)
    trim: float | None
    gain_db: float
    path: Path
    license_name: str
    page_url: str


class Anchors:
    """Video seconds for `shot:<id>` (the cut into it), `word:<beat>:<word>`, `t:<s>` and `end`."""

    def __init__(self, cfg: CortexConfig, directory: Path, spec: LessonSpec, timings: Timings):
        self.starts = {w.id: w.start for w in shot_windows(spec, timings, cfg.lessons.xfade_seconds)}
        self.directory, self.timings, self._words = directory, timings, None

    def time(self, at: str, offset: float = 0.0) -> float:
        kind, _, rest = at.partition(":")
        if kind == "t":
            base = float(rest)
        elif kind == "end":
            base = self.timings.total
        elif kind == "shot":
            base = self.starts[rest]
        else:
            beat, _, word = rest.partition(":")
            base = self._word(beat, word)
        return round(base + offset, 3)

    def _word(self, beat: str, word: str) -> float:
        if self._words is None:
            path = self.directory / "alignment.json"
            if not path.exists():
                raise LessonError("word anchors need alignment.json; run `align`")
            self._words = json.loads(path.read_text(encoding="utf-8"))["words"]
        scene, target = self.timings.scenes[beat], _norm(word)
        for start, _end, heard in self._words:
            t = float(start) + self.timings.audio_offset
            if scene.start <= t < scene.start + scene.duration and _norm(heard) == target:
                return t
        raise LessonError(f"word:{beat}:{word}: '{word}' is not heard in beat {beat}")


def resolve_cues(cfg: CortexConfig, directory: Path, spec: LessonSpec, timings: Timings) -> list[PlacedSfx]:
    """Each cue of a kit role takes the role's next sound, so neighbouring cuts never repeat a file."""
    if not spec.sound or not spec.sound.sfx:
        return []
    lc = cfg.lessons
    anchors = Anchors(cfg, directory, spec, timings)
    kit, used, placed = None, {}, []
    for cue in spec.sound.sfx:
        try:
            if cue.role:
                kit = kit or load_kit(cfg, spec.sound.kit or cfg.sound.default_kit)
                role = kit.roles.get(cue.role)
                if role is None or not role.sounds:
                    raise LessonError(f"cue {cue.at}: kit '{kit.name}' has no sounds for role '{cue.role}'")
                ref = role.sounds[used.get(cue.role, 0) % len(role.sounds)]
                used[cue.role] = used.get(cue.role, 0) + 1
            else:
                ref = cue.sound
            sound: LibrarySound = load_sound(cfg, ref)
        except SoundError as exc:
            raise LessonError(f"cue {cue.at}: {exc}") from exc
        t = anchors.time(cue.at, cue.offset)
        start = t - (sound.anchor if cue.align == "anchor" else 0.0)
        if start >= timings.total:
            raise LessonError(f"cue {cue.at}: lands at {t:.2f}s, after the video ends ({timings.total}s)")
        level = lc.sfx_lufs - sound.max_momentary_lufs if sound.max_momentary_lufs is not None else 0.0
        placed.append(PlacedSfx(cue.at, ref, sound.title, t, round(max(start, 0.0), 3), round(max(-start, 0.0), 3),
                                cue.trim, round(level + cue.gain_db, 2), sound.path(cfg), sound.license_name,
                                sound.page_url))
    return placed


def music_source(cfg: CortexConfig, directory: Path, music: Music) -> tuple[Path, str]:
    """(file, credit line) of the music bed: a file in the lesson folder or a library sound."""
    if music.file:
        return directory / music.file, music.credit
    try:
        sound = load_sound(cfg, music.sound)
    except SoundError as exc:
        raise LessonError(f"music: {exc}") from exc
    provider = PROVIDER_CREDIT.get(sound.provider, sound.provider)
    return sound.path(cfg), music.credit or f"{sound.title} — {provider}"


def dip_expr(cfg: CortexConfig, directory: Path, spec: LessonSpec, timings: Timings) -> str:
    """volume expression (eval=frame) for the bed: 1 outside the dips, ramps down to each dip's gain."""
    if not spec.sound or not spec.sound.dips:
        return ""
    anchors, factors = Anchors(cfg, directory, spec, timings), []
    for d in spec.sound.dips:
        t0, t1 = anchors.time(d.at, d.offset), anchors.time(d.until, d.until_offset)
        if t1 <= t0:
            raise LessonError(f"dip {d.at} → {d.until}: ends before it starts ({t0:.2f}s → {t1:.2f}s)")
        g, ramp = round(10 ** (d.db / 20), 5), min(d.fade, t1 - t0)
        factors.append(f"if(lt(t,{t0}),1,if(lt(t,{t0 + ramp:.3f}),1-{1 - g}*(t-{t0})/{ramp},if(lt(t,{t1}),{g},"
                       f"if(lt(t,{t1 + 0.05:.3f}),{g}+{1 - g}*(t-{t1})/0.05,1))))")
    return "*".join(factors)


def sfx_chain(p: PlacedSfx) -> str:
    chain = f"atrim=start={p.head},asetpts=PTS-STARTPTS"
    if p.trim:
        chain += f",atrim=duration={p.trim},afade=t=out:st={max(p.trim - 0.05, 0):g}:d=0.05"
    return (f"{chain},volume={p.gain_db}dB,aresample=48000,aformat=channel_layouts=stereo,"
            f"adelay={int(round(p.delay * 1000))}:all=1")


def auto_cues(spec: LessonSpec, roles: set[str]) -> list[dict]:
    """Default promo design from the kit roles that have sounds: a whoosh on every cut, and a riser that
    peaks on the cut into the last shot, where an impact lands."""
    shots = [s.id for s in spec.shots]
    if len(shots) < 2:
        return []
    cues = [{"role": "whoosh", "at": f"shot:{s}"} for s in shots[1:-1]] if "whoosh" in roles else []
    last = shots[-1]
    if "riser" in roles:
        cues.append({"role": "riser", "at": f"shot:{last}"})
    if "impact" in roles:
        cues.append({"role": "impact", "at": f"shot:{last}"})
    elif "whoosh" in roles:
        cues.append({"role": "whoosh", "at": f"shot:{last}"})
    return cues


def credits_text(music_credit: str, sfx: list[PlacedSfx]) -> str:
    """Lines for the post caption/description, then every effect with its license and page."""
    lines = [f"Música: {music_credit}"] if music_credit else []
    providers = sorted({p.ref.partition(":")[0] for p in sfx})
    lines += [f"Efeitos sonoros: {PROVIDER_CREDIT.get(name, name)}" for name in providers]
    seen = {}
    for p in sfx:
        seen.setdefault(p.ref, f"- {p.title} — {p.license_name} — {p.page_url}")
    if seen:
        lines += ["", "Efeitos usados:", *seen.values()]
    return "\n".join(lines) + "\n" if lines else ""
