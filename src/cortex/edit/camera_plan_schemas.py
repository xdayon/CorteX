from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CAMERA_EDIT_PLAN_SCHEMA_VERSION = 2
ShotIntent = Literal["speaker", "context", "fallback"]


class CameraEditShot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edit_segment_order: int = Field(ge=0)
    video_source_asset_id: str
    audio_source_asset_id: str
    source_start_us: int = Field(ge=0)
    source_end_us: int = Field(gt=0)
    audio_source_start_us: int = Field(ge=0)
    audio_source_end_us: int = Field(gt=0)
    sync_offset_us: int = 0
    scene_index: int = Field(ge=0)
    layout_id: str
    camera_role: str
    intent: ShotIntent
    confirmed_identity_ids: list[str]
    visual_quality_usable: bool
    evidence: list[str]

    @model_validator(mode="after")
    def validate_synchronized_sources(self) -> "CameraEditShot":
        if self.source_end_us <= self.source_start_us:
            raise ValueError("source_end_us must be greater than source_start_us")
        video_duration = self.source_end_us - self.source_start_us
        audio_duration = self.audio_source_end_us - self.audio_source_start_us
        if video_duration != audio_duration:
            raise ValueError("camera plan requires equal audio/video durations")
        if self.source_start_us != self.audio_source_start_us + self.sync_offset_us:
            raise ValueError("video start does not match synchronized global time")
        if self.source_end_us != self.audio_source_end_us + self.sync_offset_us:
            raise ValueError("video end does not match synchronized global time")
        if self.video_source_asset_id == self.audio_source_asset_id and self.sync_offset_us != 0:
            raise ValueError("same-source shot cannot have sync offset")
        return self


class CameraEditDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_count: int = Field(ge=0)
    speaker_shot_count: int = Field(ge=0)
    context_shot_count: int = Field(ge=0)
    fallback_shot_count: int = Field(ge=0)
    unusable_scene_count: int = Field(ge=0)
    iso_context_shot_count: int = Field(default=0, ge=0)
    indexed_iso_camera_count: int = Field(default=0, ge=0)
    reaction_shots_enabled: Literal[False] = False
    reaction_shots_blocked_by: list[str]


class CameraEditEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str
    algorithm_version: str
    audio_continuity_mode: Literal["primary_source_continuous"] = "primary_source_continuous"
    temporal_reuse_allowed: Literal[False] = False
    identity_scope: Literal["cross_layout_face_embedding"] = "cross_layout_face_embedding"


class CameraEditPlanDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = CAMERA_EDIT_PLAN_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    edit_plan_artifact_id: str
    edit_plan_input_hash: str
    camera_timeline_artifact_id: str
    camera_timeline_input_hash: str
    identity_index_artifact_id: str
    identity_index_input_hash: str
    visual_quality_artifact_id: str
    visual_quality_input_hash: str
    multicam_visual_artifact_id: str | None = None
    multicam_visual_input_hash: str | None = None
    input_hash: str
    shots: list[CameraEditShot]
    diagnostics: CameraEditDiagnostics
    engine: CameraEditEngineInfo
