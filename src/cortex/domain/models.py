from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
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


class RenderPreset(BaseModel):
    """A named, project-scoped render settings snapshot."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    name: str = Field(min_length=1, max_length=120)
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WorkflowStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY_FOR_REVIEW = "ready_for_review"
    RENDERING = "rendering"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowRun(BaseModel):
    """Persisted episode workflow that survives UI and worker restarts."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    source_asset_id: str
    status: WorkflowStatus = WorkflowStatus.QUEUED
    stage: str = "transcribe"
    progress: float = Field(default=0.0, ge=0.0, le=100.0)
    message: str = "Aguardando processamento"
    brief: dict[str, Any] = Field(default_factory=dict)
    active_job_id: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    subject_identity_id: str | None = None  # Read compatibility; never infer an interviewer.
    interviewer_identity_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ClipRevision(BaseModel):
    """Server-side snapshot of the human review state for one workflow run."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    workflow_run_id: str
    version: int = Field(default=1, ge=1)
    selection_artifact_id: str
    selected_clips: list[dict[str, Any]] = Field(min_length=1, max_length=25)
    render_settings: dict[str, Any]
    caption_edits: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    headlines: dict[str, str] = Field(default_factory=dict)
    # Caption emphasis per clip key (transcript word indices); absent keys use
    # the AI suggestion stored in the clip.
    emphasis: dict[str, list[int]] = Field(default_factory=dict)
    # Manual caption line breaks per clip key (word indices that start a line).
    line_breaks: dict[str, list[int]] = Field(default_factory=dict)
    # Per-clip render overrides (partial RenderSettings) deep-merged over
    # render_settings for that clip only; absent keys use the shared settings.
    clip_settings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    visual_artifacts: dict[str, str] = Field(default_factory=dict)
    interviewer_identity_id: str | None = None
    reuse_reactions: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExportStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExportItemStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    CAMERA_PLANNING = "camera_planning"  # legacy: kept so old rows validate
    RENDERING = "rendering"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    batch_id: str
    project_id: str
    position: int = Field(ge=0)
    clip_key: str
    title: str
    clip: dict[str, Any]
    render_settings: dict[str, Any]
    status: ExportItemStatus = ExportItemStatus.QUEUED
    active_job_id: str | None = None
    edit_plan_artifact_id: str | None = None
    camera_edit_plan_artifact_id: str | None = None
    render_artifact_id: str | None = None
    attempt: int = Field(default=1, ge=1)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExportBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    workflow_run_id: str
    revision_id: str
    idempotency_key: str = Field(min_length=1, max_length=240)
    status: ExportStatus = ExportStatus.QUEUED
    active_job_id: str | None = None
    reaction_candidate_artifact_id: str | None = None
    error: str | None = None
    items: list[ExportItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ClipPublication(BaseModel):
    """What happened to one exported clip after it was posted (learning-loop input)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    render_artifact_id: str
    title: str = Field(min_length=1, max_length=300)
    headline: str = Field(default="", max_length=300)
    hook_text: str = Field(default="", max_length=200)
    duration_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    published: bool = False
    platforms: list[Literal["instagram", "tiktok"]] = Field(default_factory=list)
    published_on: date | None = None
    url: str | None = Field(default=None, max_length=500, pattern=r"^https?://\S+$")
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    retention_3s_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    avg_watch_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    rating: Literal[-1, 0, 1] | None = None
    notes: str = Field(default="", max_length=300)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ClipDecision(StrEnum):
    APPROVED = "approved"
    DISCARDED = "discarded"


class ClipState(BaseModel):
    """Manual state the user sets on one suggested clip of a workflow run."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    workflow_run_id: str
    clip_key: str = Field(min_length=1, max_length=200)
    decision: ClipDecision | None = None
    discard_reason: Literal[
        "weak_hook", "off_topic", "repeated", "bad_edit", "technical", "other"
    ] | None = None
    saved: bool = False
    saved_at: datetime | None = None
    notes: str = Field(default="", max_length=300)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
