from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MULTICAM_SYNC_SCHEMA_VERSION = 1


class CameraSyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str
    source_sha256: str
    status: Literal["synced", "rejected"]
    offset_us: int
    correlation: float = Field(ge=-1, le=1)
    peak_margin: float = Field(ge=0, le=2)
    overlap_seconds: float = Field(ge=0)
    reason: str | None = None


class MulticamSyncEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str
    algorithm_version: str
    sample_rate: int = Field(gt=0)
    envelope_hz: int = Field(gt=0)
    max_offset_seconds_requested: float = Field(gt=0)
    max_offset_seconds_effective: float = Field(gt=0)
    analysis_seconds_requested: float = Field(gt=0)
    correlation_threshold: float = Field(ge=-1, le=1)
    peak_margin_threshold: float = Field(ge=0, le=2)
    minimum_overlap_seconds: float = Field(gt=0)
    ffmpeg_path: str
    ffprobe_path: str


class MulticamSyncDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = MULTICAM_SYNC_SCHEMA_VERSION
    project_id: str
    primary_source_asset_id: str
    primary_source_sha256: str
    input_hash: str
    cameras: list[CameraSyncResult]
    synced_camera_count: int = Field(ge=0)
    rejected_camera_count: int = Field(ge=0)
    engine: MulticamSyncEngineInfo
