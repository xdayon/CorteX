from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MULTICAM_VISUAL_SCHEMA_VERSION = 1


class IsoVisualIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str
    source_sha256: str
    offset_us: int
    status: Literal["indexed", "rejected"]
    scene_index_artifact_id: str | None = None
    face_index_artifact_id: str | None = None
    visual_quality_artifact_id: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_artifacts(self) -> "IsoVisualIndex":
        artifact_ids = (
            self.scene_index_artifact_id,
            self.face_index_artifact_id,
            self.visual_quality_artifact_id,
        )
        if self.status == "indexed" and not all(artifact_ids):
            raise ValueError("indexed ISO requires all visual artifacts")
        if self.status == "rejected" and any(artifact_ids):
            raise ValueError("rejected ISO cannot reference visual artifacts")
        return self


class MulticamVisualEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str
    algorithm_version: str
    stages: list[str]
    scene_threshold: float = Field(ge=0, le=100)
    face_sample_fps: float = Field(gt=0)
    visual_quality_sample_fps: float = Field(gt=0)


class MulticamVisualIndexDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = MULTICAM_VISUAL_SCHEMA_VERSION
    project_id: str
    primary_source_asset_id: str
    multicam_sync_artifact_id: str
    multicam_sync_input_hash: str
    input_hash: str
    cameras: list[IsoVisualIndex]
    indexed_camera_count: int = Field(ge=0)
    rejected_camera_count: int = Field(ge=0)
    engine: MulticamVisualEngineInfo
