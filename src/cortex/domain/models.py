from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from cortex.schemas import utc_now


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SourceKind(StrEnum):
    UPLOAD = "upload"
    YOUTUBE = "youtube"


class SourceAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    kind: SourceKind
    original_filename: str | None = None
    source_url: str | None = None
    stored_path: str
    sha256: str
    size_bytes: int
    probe: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class TranscriptArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    source_asset_id: str
    schema_version: int = 1
    path: str
    audio_sha256: str
    engine: str
    model: str
    device: str
    compute_type: str
    language: str | None = None
    vad: bool
    batch_size: int
    duration_seconds: float
    created_at: datetime = Field(default_factory=utc_now)


class StageArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    stage: str
    schema_version: int = 1
    path: str
    input_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
