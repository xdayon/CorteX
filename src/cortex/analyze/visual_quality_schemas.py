from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

VISUAL_QUALITY_SCHEMA_VERSION = 1


class VisualQualityFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=0)
    time_us: int = Field(ge=0)
    mean_luma: float = Field(ge=0, le=255)
    black_pixel_ratio: float = Field(ge=0, le=1)
    blur_score: float = Field(ge=0)
    frame_delta: float | None = Field(default=None, ge=0, le=1)
    is_black: bool
    is_blurred: bool
    is_frozen: bool
    face_edge_occlusion: bool | None = None


class SceneVisualQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=0)
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    sample_count: int = Field(ge=0)
    black_share: float = Field(ge=0, le=1)
    blurred_share: float = Field(ge=0, le=1)
    frozen_share: float = Field(ge=0, le=1)
    face_edge_occlusion_share: float | None = Field(default=None, ge=0, le=1)
    usable: bool
    issues: list[str]

    @model_validator(mode="after")
    def validate_interval(self) -> "SceneVisualQuality":
        if self.end_us <= self.start_us:
            raise ValueError("end_us must be greater than start_us")
        return self


class VisualQualityEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str
    algorithm_version: str
    sample_fps_requested: float = Field(gt=0)
    sample_fps_effective: float = Field(gt=0)
    frame_width: int = Field(gt=0)
    black_luma_threshold: float = Field(ge=0, le=255)
    black_pixel_ratio_threshold: float = Field(ge=0, le=1)
    blur_score_threshold: float = Field(ge=0)
    freeze_delta_threshold: float = Field(ge=0, le=1)
    issue_share_threshold: float = Field(gt=0, le=1)
    face_edge_margin: float = Field(ge=0, le=0.25)
    ffmpeg_path: str
    ffmpeg_version: str
    decoder_requested: str
    decoder_effective: str
    device_requested: str
    device_effective: str


class VisualQualityDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = VISUAL_QUALITY_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    source_sha256: str
    scene_index_artifact_id: str
    scene_index_input_hash: str
    face_index_artifact_id: str
    face_index_input_hash: str
    input_hash: str
    duration_us: int = Field(ge=0)
    frames: list[VisualQualityFrame]
    scenes: list[SceneVisualQuality]
    engine: VisualQualityEngineInfo
