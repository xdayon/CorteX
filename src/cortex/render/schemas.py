from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cortex.render.technical_quality import RenderTechnicalQualityReport

RENDER_SCHEMA_VERSION = 12
RENDER_SETTINGS_SCHEMA_VERSION = 1
OVERLAY_SCHEMA_VERSION = 1

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _hex_color(value: str) -> str:
    if not _HEX_COLOR_RE.fullmatch(value):
        raise ValueError("cor must be hex #RRGGBB ou #RRGGBBAA")
    return value.upper()


class RenderAnimationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: Literal["none", "fade", "pop"]
    duration_seconds: float = Field(gt=0.0, le=2.0)


class RenderHeadlineAnimationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entrance: Literal["none", "fade", "slide"]
    exit: Literal["none", "fade", "slide"]
    duration_seconds: float = Field(gt=0.0, le=2.0)


class RenderColorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caption_text: str = Field(default="#FFFFFF")
    karaoke_highlight: str = Field(default="#2CE4D7")
    outline: str = Field(default="#071012")
    shadow: str = Field(default="#000000B8")
    editorial_quote_burst: str = Field(default="#11B9AD")
    editorial_quote_strip: str = Field(default="#FFFFFFF5")
    editorial_quote_headline: str = Field(default="#071012")

    @field_validator("caption_text", "karaoke_highlight", "outline", "shadow", "editorial_quote_burst", "editorial_quote_strip", "editorial_quote_headline")
    @classmethod
    def normalize_hex(cls, value: str) -> str:
        return _hex_color(value)


class RenderTemplateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_burst_opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    quote_strip_opacity: float = Field(default=0.96, ge=0.0, le=1.0)
    quote_padding: float = Field(default=14.0, ge=0.0, le=64.0)


class RenderCanvasSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(ge=320, le=3840)
    height: int = Field(ge=320, le=3840)
    fps: int = Field(ge=15, le=120)


# Hard ceiling on punch_in.scale (Gate 5). The renderer never keyframes or
# tracks the crop window — it is one static scale/crop per segment — so the
# only lever that controls how far the digital zoom pushes past native
# source detail is this scalar. vertical_crop and blurred_background's
# foreground both already normalize the source to exactly canvas resolution
# before punch-in ever runs (force_original_aspect_ratio=increase/decrease +
# a crop/pad to width×height), so punch-in there is a zoom *within* a
# canvas-resolution frame: a 1.5x ceiling is a fixed product decision (not
# computed per-source) capping how much of that canvas-resolution frame gets
# thrown away, chosen so the upscaled result still reads as sharp on typical
# 1080p+ source footage. face_static_crop already resolves its base crop in
# native source pixels sized to the canvas aspect ratio; punch-in shrinks
# that rectangle by up to the same 1.5x, which RenderService validates stays
# within the source's bounds before encode (see PUNCH_IN_MAX_TOTAL_UPSCALE).
PUNCH_IN_MAX_SCALE = 1.5
PUNCH_IN_MIN_SCALE = 1.0


class RenderPunchInSettings(BaseModel):
    """Static, per-segment digital zoom (Gate 5) used to mask jump cuts
    without introducing camera motion. No keyframes, no pans, no tracking:
    the effective scale/anchor is resolved once per segment and baked into
    a constant crop+scale filter pair."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    scale: float = Field(default=1.15, ge=PUNCH_IN_MIN_SCALE, le=PUNCH_IN_MAX_SCALE)
    anchor: Literal["center", "face"] = "center"
    # When true, punch-in alternates between consecutive segments of the same
    # scene (by scene_index when a camera plan is present, else by parity of
    # the segment's global timeline_order) so consecutive jump cuts read as a
    # deliberate size change instead of a jarring repeat. When false, every
    # eligible segment gets the punch-in.
    alternate_on_jump_cuts: bool = True


class RenderFramingSettings(BaseModel):
    """Defines how a landscape source occupies a portrait render canvas."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    mode: Literal["vertical_crop", "blurred_background", "face_static_crop"] = "vertical_crop"
    punch_in: RenderPunchInSettings = Field(default_factory=RenderPunchInSettings)

    @model_validator(mode="after")
    def _validate_punch_in_anchor(self) -> "RenderFramingSettings":
        if self.punch_in.anchor == "face" and self.mode != "face_static_crop":
            raise ValueError(
                "punch_in.anchor 'face' exige framing.mode 'face_static_crop'"
            )
        return self


class RenderCaptionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    font_family: str = Field(min_length=1, max_length=80, pattern=r"^[\w .-]+$")
    font_size: int = Field(ge=12, le=120)
    words_per_cue: int = Field(ge=1, le=12)
    outline: bool
    shadow: bool = True
    karaoke: bool = True
    text_color: str = Field(default="#FFFFFF")
    karaoke_color: str = Field(default="#2CE4D7")
    outline_color: str = Field(default="#071012")
    shadow_color: str = Field(default="#000000B8")
    animation: RenderAnimationSettings = Field(
        default_factory=lambda: RenderAnimationSettings(style="fade", duration_seconds=0.18)
    )

    @field_validator("text_color", "karaoke_color", "outline_color", "shadow_color")
    @classmethod
    def normalize_hex(cls, value: str) -> str:
        return _hex_color(value)


class RenderHeadlineSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    text: str = Field(default="", max_length=120)
    font_family: str = Field(min_length=1, max_length=80, pattern=r"^[\w .-]+$")
    font_size: int = Field(ge=12, le=160)
    duration_seconds: float = Field(gt=0.0, le=15.0)
    burst_color: str = Field(default="#11B9AD")
    strip_color: str = Field(default="#FFFFFFF5")
    text_color: str = Field(default="#071012")
    animation: RenderHeadlineAnimationSettings = Field(
        default_factory=lambda: RenderHeadlineAnimationSettings(
            entrance="fade", exit="fade", duration_seconds=0.24
        )
    )

    @field_validator("burst_color", "strip_color", "text_color")
    @classmethod
    def normalize_hex(cls, value: str) -> str:
        return _hex_color(value)


class RenderSubtitleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sidecar_srt: bool


class RenderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = RENDER_SETTINGS_SCHEMA_VERSION
    encoder: Literal["h264_nvenc", "libx264"]
    canvas: RenderCanvasSettings
    framing: RenderFramingSettings = Field(default_factory=RenderFramingSettings)
    captions: RenderCaptionSettings
    headline: RenderHeadlineSettings
    subtitles: RenderSubtitleSettings
    template: RenderTemplateSettings = Field(default_factory=RenderTemplateSettings)


class RenderCaptionSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    font_family: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[\w .-]+$")
    font_size: int | None = Field(default=None, ge=12, le=120)
    words_per_cue: int | None = Field(default=None, ge=1, le=12)
    outline: bool | None = None
    shadow: bool | None = None
    karaoke: bool | None = None
    text_color: str | None = None
    karaoke_color: str | None = None
    outline_color: str | None = None
    shadow_color: str | None = None
    animation: RenderAnimationSettings | None = None

    @field_validator("text_color", "karaoke_color", "outline_color", "shadow_color")
    @classmethod
    def normalize_hex(cls, value: str | None) -> str | None:
        return _hex_color(value) if value is not None else None


class RenderHeadlineSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    text: str | None = Field(default=None, max_length=120)
    font_family: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[\w .-]+$")
    font_size: int | None = Field(default=None, ge=12, le=160)
    duration_seconds: float | None = Field(default=None, gt=0.0, le=15.0)
    burst_color: str | None = None
    strip_color: str | None = None
    text_color: str | None = None
    animation: RenderHeadlineAnimationSettings | None = None

    @field_validator("burst_color", "strip_color", "text_color")
    @classmethod
    def normalize_hex(cls, value: str | None) -> str | None:
        return _hex_color(value) if value is not None else None


class RenderTemplateSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_burst_opacity: float | None = Field(default=None, ge=0.0, le=1.0)
    quote_strip_opacity: float | None = Field(default=None, ge=0.0, le=1.0)
    quote_padding: float | None = Field(default=None, ge=0.0, le=64.0)


class RenderFramingSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["vertical_crop", "blurred_background", "face_static_crop"] | None = None
    # Whole-object override, matching how captions.animation / headline.animation
    # already behave in this patch model: no partial-field patch for nested
    # settings, the incoming object fully replaces punch_in when present.
    punch_in: RenderPunchInSettings | None = None


class RenderSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    encoder: Literal["h264_nvenc", "libx264"] | None = None
    canvas: RenderCanvasSettings | None = None
    framing: RenderFramingSettingsPatch | None = None
    captions: RenderCaptionSettingsPatch | None = None
    headline: RenderHeadlineSettingsPatch | None = None
    subtitles: RenderSubtitleSettings | None = None
    template: RenderTemplateSettingsPatch | None = None


class RenderEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ffmpeg: str
    ffprobe: str
    requested_encoder: str
    effective_encoder: str
    width: int
    height: int
    fps: int


class RenderSourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str
    sha256: str
    video_used: bool
    audio_used: bool


class RenderTransitionInfo(BaseModel):
    """Requested-vs-effective transition actually materialized in the
    filtergraph at one interior EDL boundary (Gate 4, Session H).

    ``requested_*`` mirrors ``EditTransition`` on the source EditPlanDocument
    boundary as-authored by the planner. ``effective_*`` is what the renderer
    built into the filtergraph — today that is always identical to
    requested, because the planner already materializes the audio offset
    into the segment's audio_start/audio_end clocks before the render stage
    ever sees the plan; the renderer has no further degrade path for a
    boundary it accepts. The field pair exists so a future renderer-side
    fallback (e.g. an audio duration too short to honor the offset) has
    somewhere to report a discrepancy without a schema migration.
    """

    model_config = ConfigDict(extra="forbid")

    boundary_index: int = Field(ge=0)
    requested_kind: Literal["hard", "crossfade", "j_cut", "l_cut"]
    effective_kind: Literal["hard", "crossfade", "j_cut", "l_cut"]
    requested_audio_offset_seconds: float
    effective_audio_offset_seconds: float


class RenderPunchInInfo(BaseModel):
    """Requested-vs-effective punch-in actually materialized for one EDL
    segment (Gate 5), following the same requested_/effective_ pattern used
    elsewhere in this manifest. ``anchor_x``/``anchor_y`` are the constant
    top-left offset of the punch-in crop in the coordinate space the segment's
    framing mode operates in (canvas pixels for vertical_crop and the
    foreground of blurred_background; native source pixels for
    face_static_crop). ``applied=False`` means punch-in was configured but
    this specific segment was skipped (e.g. alternation parity), with
    ``reason`` explaining why."""

    model_config = ConfigDict(extra="forbid")

    segment_order: int = Field(ge=0)
    requested_scale: float
    effective_scale: float
    anchor_x: int = Field(ge=0)
    anchor_y: int = Field(ge=0)
    applied: bool
    reason: str | None = None


class RenderQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    expected_duration_seconds: float = Field(ge=0)
    actual_duration_seconds: float = Field(ge=0)
    duration_delta_seconds: float = Field(ge=0)
    has_video: bool
    has_audio: bool
    issues: list[str]
    warnings: list[str] = Field(default_factory=list)
    loudness_target_lufs: float | None = None
    integrated_loudness_lufs: float | None = None
    loudness_delta_lu: float | None = Field(default=None, ge=0)
    true_peak_dbfs: float | None = None
    true_peak_limit_dbfs: float | None = None
    caption_cue_count: int = Field(default=0, ge=0)
    subtitles_present: bool = False
    headline_present: bool = False
    visual_analysis_performed: bool = False
    black_threshold_seconds: float | None = Field(default=None, ge=0)
    black_interval_count: int = Field(default=0, ge=0)
    black_total_duration_seconds: float = Field(default=0, ge=0)
    black_max_duration_seconds: float = Field(default=0, ge=0)
    freeze_threshold_seconds: float | None = Field(default=None, ge=0)
    freeze_interval_count: int = Field(default=0, ge=0)
    freeze_total_duration_seconds: float = Field(default=0, ge=0)
    freeze_max_duration_seconds: float = Field(default=0, ge=0)
    technical: RenderTechnicalQualityReport | None = None


class RenderOverlayInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    renderer: str
    captions_enabled: bool
    caption_font: str | None = None
    caption_text_color: str | None = None
    caption_karaoke_color: str | None = None
    caption_outline_color: str | None = None
    caption_shadow_color: str | None = None
    caption_font_size: int | None = None
    caption_words_per_cue: int | None = None
    caption_outline: bool | None = None
    caption_shadow: bool | None = None
    caption_text_color: str | None = None
    caption_karaoke_color: str | None = None
    caption_outline_color: str | None = None
    caption_shadow_color: str | None = None
    caption_animation: str | None = None
    caption_animation_duration_seconds: float | None = None
    karaoke_enabled: bool = False
    headline_enabled: bool
    headline_text: str | None = None
    headline_font: str | None = None
    headline_font_size: int | None = None
    headline_duration_seconds: float | None = None
    headline_burst_color: str | None = None
    headline_strip_color: str | None = None
    headline_text_color: str | None = None
    headline_animation_entrance: str | None = None
    headline_animation_exit: str | None = None
    headline_animation_duration_seconds: float | None = None
    sidecar_srt: bool = False
    artifact_path: str | None = None
    artifact_sha256: str | None = None
    artifact_size_bytes: int | None = Field(default=None, ge=1)
    input_hash: str | None = None
    codec: str | None = None
    pixel_format: str | None = None
    node_executable: str | None = None
    node_version: str | None = None
    remotion_version: str | None = None
    safe_zones_version: int | None = None
    canvas_safe_zone: str | None = None


class RenderQualityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    status: Literal["pass", "warning", "fail"]
    severity: Literal["info", "warning", "blocking"]
    evidence: dict[str, bool | float | int | str | None] = Field(default_factory=dict)
    thresholds: dict[str, float | int | str] = Field(default_factory=dict)


class RenderQualityPublicationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publish_ready: bool
    reasons: list[str]
    warnings: list[str] = Field(default_factory=list)
    checks: list[RenderQualityCheck] = Field(default_factory=list)
    loudness: dict[str, float | None]
    visual: dict[str, float | int | bool | None]
    captions: dict[str, int | bool | None]
    safe_zones: dict[str, object]
    encoder: dict[str, str]
    dimensions: dict[str, int | float]
    hashes: dict[str, str | None]
    provenance: dict[str, str | None]
    punch_in: dict[str, object] = Field(default_factory=dict)
    technical: RenderTechnicalQualityReport | None = None


class RenderDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = RENDER_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    edit_plan_artifact_id: str
    camera_edit_plan_artifact_id: str | None = None
    face_index_artifact_id: str | None = None
    identity_index_artifact_id: str | None = None
    target_identity_id: str | None = None
    static_face_crops: list[dict[str, object]] = Field(default_factory=list)
    sources: list[RenderSourceInfo] = Field(default_factory=list)
    input_hash: str
    output_path: str
    output_sha256: str
    output_size_bytes: int = Field(ge=1)
    subtitles_path: str | None = None
    subtitles_sha256: str | None = None
    timeline_duration_seconds: float = Field(ge=0)
    segment_count: int = Field(ge=1)
    transitions: list[RenderTransitionInfo] = Field(default_factory=list)
    punch_ins: list[RenderPunchInInfo] = Field(default_factory=list)
    engine: RenderEngineInfo
    requested_settings: RenderSettings | None = None
    effective_settings: RenderSettings | None = None
    overlays: RenderOverlayInfo | None = None
    quality: RenderQualityReport
    publication: RenderQualityPublicationReport | None = None
