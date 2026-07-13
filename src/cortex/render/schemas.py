from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RENDER_SCHEMA_VERSION = 7
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


class RenderFramingSettings(BaseModel):
    """Defines how a landscape source occupies a portrait render canvas."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    mode: Literal["vertical_crop", "blurred_background", "face_static_crop"] = "vertical_crop"


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


class RenderQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    expected_duration_seconds: float = Field(ge=0)
    actual_duration_seconds: float = Field(ge=0)
    duration_delta_seconds: float = Field(ge=0)
    has_video: bool
    has_audio: bool
    issues: list[str]
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


class RenderQualityPublicationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publish_ready: bool
    reasons: list[str]
    loudness: dict[str, float | None]
    visual: dict[str, float | int | bool | None]
    captions: dict[str, int | bool | None]
    safe_zones: dict[str, object]
    encoder: dict[str, str]
    dimensions: dict[str, int | float]
    hashes: dict[str, str | None]
    provenance: dict[str, str | None]


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
    engine: RenderEngineInfo
    requested_settings: RenderSettings | None = None
    effective_settings: RenderSettings | None = None
    overlays: RenderOverlayInfo | None = None
    quality: RenderQualityReport
    publication: RenderQualityPublicationReport | None = None
