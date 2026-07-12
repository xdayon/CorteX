from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

EDIT_PLAN_SCHEMA_VERSION = 1


class EditTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video: str
    audio: str
    duration: float = Field(ge=0)


class EditSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    timeline_order: int = Field(ge=0)
    transition: EditTransition | None = None


class EditPlanDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str
    waveform_used: bool
    candidate_pauses: int = Field(ge=0)
    cuts: int = Field(ge=0)
    saved_seconds: float = Field(ge=0)
    crossfade: float = Field(ge=0)
    vad_used: bool
    scene_snap_count: int = Field(default=0, ge=0)


class EditQualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    code: str
    segment: int | None = None
    boundary: str | None = None
    word: str | None = None
    time: float | None = None
    snapped_from: float | None = None
    snapped_to: float | None = None
    delta_ms: float | None = None
    cuts: int | None = None
    recommended_max: int | None = None


class EditQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: list[EditQualityIssue]
    profile: str
    degraded: bool


class EditPlanDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = EDIT_PLAN_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    transcript_artifact_id: str
    analysis_artifact_id: str
    scene_index_artifact_id: str | None = None
    input_hash: str
    clip_start: float = Field(ge=0)
    clip_end: float = Field(ge=0)
    profile: str
    segments: list[EditSegment]
    timeline_duration_seconds: float = Field(ge=0)
    diagnostics: EditPlanDiagnostics
    quality: EditQualityReport
