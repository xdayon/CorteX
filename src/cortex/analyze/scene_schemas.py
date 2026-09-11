from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from cortex.analyze.scope import SourceRange

SCENE_INDEX_SCHEMA_VERSION = 1


class SceneCut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: float = Field(ge=0)
    score: float = Field(ge=0)


class SceneSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class SceneIndexEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ffmpeg_path: str
    ffmpeg_version: str
    filter: str
    threshold_requested: float
    threshold_effective: float


class SceneIndexDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCENE_INDEX_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    source_sha256: str
    input_hash: str
    duration_seconds: float = Field(ge=0)
    source_ranges: list[SourceRange] = Field(default_factory=list)
    cuts: list[SceneCut]
    scenes: list[SceneSegment]
    cut_count: int = Field(ge=0)
    engine: SceneIndexEngineInfo
