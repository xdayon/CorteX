from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

RENDER_SCHEMA_VERSION = 2


class RenderEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ffmpeg: str
    ffprobe: str
    requested_encoder: str
    effective_encoder: str
    width: int
    height: int
    fps: int


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


class RenderDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = RENDER_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    edit_plan_artifact_id: str
    input_hash: str
    output_path: str
    output_sha256: str
    output_size_bytes: int = Field(ge=1)
    timeline_duration_seconds: float = Field(ge=0)
    segment_count: int = Field(ge=1)
    engine: RenderEngineInfo
    quality: RenderQualityReport
