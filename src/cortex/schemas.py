from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobType(StrEnum):
    TRANSCRIPTION = "transcription"
    ANALYSIS = "analysis"
    SCENE_ANALYSIS = "scene_analysis"
    FACE_ANALYSIS = "face_analysis"
    SPEAKER_ANALYSIS = "speaker_analysis"
    CAMERA_ANALYSIS = "camera_analysis"
    VISUAL_QUALITY_ANALYSIS = "visual_quality_analysis"
    IDENTITY_ANALYSIS = "identity_analysis"
    REACTION_CANDIDATE_ANALYSIS = "reaction_candidate_analysis"
    CAMERA_PLANNING = "camera_planning"
    SUGGESTION = "suggestion"
    EDIT_PLAN = "edit_plan"
    RENDER = "render"
    LEGACY_PIPELINE = "pipeline"
    INGEST_YOUTUBE = "ingest_youtube"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelineStage(StrEnum):
    QUEUED = "queued"
    INGEST = "ingest"
    TRANSCRIBE = "transcribe"
    ANALYZE = "analyze"
    SUGGEST = "suggest"
    EDIT = "edit"
    RENDER = "render"
    QUALITY = "quality"
    COMPLETE = "complete"


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: JobType
    project_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_legacy_pipeline(self) -> "JobCreate":
        if self.type == JobType.LEGACY_PIPELINE:
            raise ValueError("pipeline genérico foi removido; use WorkflowRun")
        return self


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    type: JobType
    status: JobStatus = JobStatus.QUEUED
    stage: PipelineStage = PipelineStage.QUEUED
    progress: float = Field(default=0.0, ge=0.0, le=100.0)
    message: str = "Aguardando execução"
    project_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    worker_pid: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class JobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: JobStatus | None = None
    stage: PipelineStage | None = None
    progress: float | None = Field(default=None, ge=0.0, le=100.0)
    message: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class CpuSnapshot(BaseModel):
    usage_percent: float = Field(ge=0, le=100)
    logical_cores: int = Field(ge=1)
    physical_cores: int | None = Field(default=None, ge=1)
    memory_used_percent: float = Field(ge=0, le=100)


class GpuSnapshot(BaseModel):
    state: str
    name: str | None = None
    usage_percent: float | None = Field(default=None, ge=0, le=100)
    memory_used_mb: int | None = Field(default=None, ge=0)
    memory_total_mb: int | None = Field(default=None, ge=0)
    temperature_c: int | None = None
    encoder_percent: float | None = Field(default=None, ge=0, le=100)
    decoder_percent: float | None = Field(default=None, ge=0, le=100)
    error: str | None = None


class HardwareSnapshot(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    cpu: CpuSnapshot
    gpus: list[GpuSnapshot]
