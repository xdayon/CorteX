from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cortex.lessons.common import LessonError

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
# Timeline anchors shared by effects, titles, dips and Manim cues: the cut into a shot, a heard word, a second, the end.
CUE_AT = re.compile(r"^(shot:[A-Za-z0-9_-]+|word:[A-Za-z0-9_-]+:[^:\s]+|t:\d+(\.\d+)?|end)$")


class Beat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str = ""
    hold: float | None = Field(default=None, gt=0)


class FlowRef(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    clip: str = Field(pattern=r"^\d{2,3}$")  # Flow prompt number; long lessons pass 99
    in_: float = Field(default=0.0, ge=0, alias="in")
    out: float | None = None
    prompt: str | None = None
    variations: int = Field(default=1, ge=1)
    audio_db: float = 0.0  # native clip sound, relative to sound.native_lufs
    mute: bool = False

    @model_validator(mode="after")
    def _window(self) -> "FlowRef":
        if self.out is not None and self.out <= self.in_:
            raise ValueError("flow.out must be greater than flow.in")
        return self


class Cue(BaseModel):
    """A narration moment a Manim scene syncs to; `render` writes it to scenes/shots.json in seconds from the shot start."""

    model_config = ConfigDict(extra="forbid")

    at: str = Field(pattern=CUE_AT.pattern)
    offset: float = 0.0


class ManimRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene: str = Field(pattern=r"^[A-Za-z0-9_]+\.py$")
    cls: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    render: str = Field(pattern=r"^[A-Za-z0-9_-]+\.(mp4|mov)$")
    transparent: bool = False
    shade: bool = True  # hybrid: dim the plate and add the 16:9 lesson's right-hand scrim; false = plain overlay
    cues: dict[str, Cue] = {}


class ImageRef(BaseModel):
    """A still (product mockup, end card) on black, slowly pushed in; `key` cuts out a flat background."""

    model_config = ConfigDict(extra="forbid")

    file: str
    key: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{6}$")
    width: float = Field(default=0.86, gt=0, le=1.5)  # fraction of the frame width
    center_y: float = Field(default=0.36, ge=0, le=1)  # image centre, fraction of the frame height
    zoom: float = Field(default=1.04, ge=1, le=1.5)  # scale reached at the end of the shot
    glow: bool = True  # warm light behind the image


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    beats: list[str] = Field(min_length=1)
    kind: Literal["flow", "manim", "hybrid", "image"]
    flow: FlowRef | None = None
    manim: ManimRef | None = None
    image: ImageRef | None = None

    @model_validator(mode="after")
    def _refs(self) -> "Shot":
        if self.kind in ("flow", "hybrid") and self.flow is None:
            raise ValueError(f"shot {self.id}: kind {self.kind} needs 'flow'")
        if self.kind in ("manim", "hybrid") and self.manim is None:
            raise ValueError(f"shot {self.id}: kind {self.kind} needs 'manim'")
        if self.kind == "image" and self.image is None:
            raise ValueError(f"shot {self.id}: kind image needs 'image'")
        if self.kind == "hybrid" and not self.manim.transparent:
            raise ValueError(f"shot {self.id}: hybrid manim must be transparent")
        return self


class Music(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str | None = None  # in the lesson folder
    sound: str | None = None  # or a sound library ref ("soundza:<id>")
    credit: str = ""
    start: float = Field(default=0.0, ge=0)  # second of the track the video opens on
    fade_in: float = Field(default=2.5, ge=0)
    fade_out: float = Field(default=5.0, ge=0)
    gain_db: float = 0.0

    @model_validator(mode="after")
    def _one_source(self) -> "Music":
        if (self.file is None) == (self.sound is None):
            raise ValueError("music: give exactly one of 'file' or 'sound'")
        return self


class SfxCue(BaseModel):
    """One effect: a kit role (rotates through the role's sounds) or a library ref, landed on `at`.

    at: "shot:<id>" (the cut into that shot), "word:<beat>:<word>" (first time the word is heard in the
    beat) or "t:<seconds>". align "anchor" puts the sound's hit there (a riser ends on it, a whoosh
    peaks on it); "start" starts the sound there.
    """

    model_config = ConfigDict(extra="forbid")

    at: str = Field(pattern=CUE_AT.pattern)
    role: str | None = None
    sound: str | None = None
    offset: float = 0.0
    gain_db: float = 0.0
    align: Literal["anchor", "start"] = "anchor"
    trim: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _one_source(self) -> "SfxCue":
        if (self.role is None) == (self.sound is None):
            raise ValueError(f"cue {self.at}: give exactly one of 'role' or 'sound'")
        return self


class Dip(BaseModel):
    """Pull the bed (music + native clip sound) down between two anchors: the silence before a line."""

    model_config = ConfigDict(extra="forbid")

    at: str = Field(pattern=CUE_AT.pattern)
    until: str = Field(pattern=CUE_AT.pattern)
    offset: float = 0.0
    until_offset: float = 0.0
    db: float = Field(default=-30.0, le=0)
    fade: float = Field(default=0.25, gt=0)  # ramp down; the way back up is a 50 ms snap


class SoundDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kit: str | None = None  # None = sound.default_kit
    duck_music: bool = True
    native_lufs: float | None = None  # keep the Flow clips' own sound, each levelled to this
    dips: list[Dip] = []
    sfx: list[SfxCue] = []


class Title(BaseModel):
    """On-screen text burned with libass. Styles and their default places live in lessons/titles.py."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    at: str = Field(pattern=CUE_AT.pattern)
    offset: float = 0.0
    until: str | None = Field(default=None, pattern=CUE_AT.pattern)
    until_offset: float = 0.0
    duration: float = Field(default=2.5, gt=0)  # used when there is no `until`
    style: str = "line"
    effect: Literal["fade", "type", "track"] = "fade"
    y: float | None = Field(default=None, ge=0, le=1)  # override the style's height (fraction)


class LessonSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    slug: str
    title: str
    language: str = "en"
    aspect: Literal["16:9", "9:16"] = "16:9"
    narration: str
    music: Music | None = None
    sound: SoundDesign | None = None
    titles: list[Title] = []
    beats: list[Beat] = Field(min_length=1)
    shots: list[Shot] = Field(min_length=1)

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SAFE_ID.match(value):
            raise ValueError("slug must match [A-Za-z0-9_-]+")
        return value

    @model_validator(mode="after")
    def _coverage(self) -> "LessonSpec":
        beat_ids = [b.id for b in self.beats]
        if len(set(beat_ids)) != len(beat_ids):
            raise ValueError("duplicate beat id")
        shot_ids = [s.id for s in self.shots]
        if len(set(shot_ids)) != len(shot_ids):
            raise ValueError("duplicate shot id")
        covered = [b for s in self.shots for b in s.beats]
        if covered != beat_ids:
            missing = [b for b in beat_ids if b not in covered]
            extra = [b for b in covered if b not in beat_ids or covered.count(b) > 1]
            raise ValueError(f"shots must cover every beat exactly once, in order (missing {missing}, bad {sorted(set(extra))})")
        anchors = [t.at for t in self.titles] + [t.until for t in self.titles if t.until]
        if self.sound:
            anchors += [c.at for c in self.sound.sfx] + [a for d in self.sound.dips for a in (d.at, d.until)]
        anchors += [c.at for s in self.shots if s.manim for c in s.manim.cues.values()]
        for at in anchors:
            kind, _, rest = at.partition(":")
            ref = rest.partition(":")[0]
            if (kind == "shot" and ref not in shot_ids) or (kind == "word" and ref not in beat_ids):
                raise ValueError(f"cue {at}: no such {'shot' if kind == 'shot' else 'beat'} {ref}")
        return self

    def flow_numbers(self) -> list[str]:
        return sorted({s.flow.clip for s in self.shots if s.flow})


def lesson_dir(cfg, slug: str) -> Path:
    return Path(cfg.lessons.dir) / slug


def load_spec(directory: Path) -> LessonSpec:
    path = directory / "lesson.json"
    if not path.exists():
        raise LessonError(f"missing {path}")
    try:
        return LessonSpec.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise LessonError(f"invalid {path}: {exc}") from exc


def save_spec(directory: Path, spec: LessonSpec) -> None:
    payload = spec.model_dump(by_alias=True, exclude_none=True)
    (directory / "lesson.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
