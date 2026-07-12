from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CAMERA_TIMELINE_SCHEMA_VERSION = 1
CameraRole = Literal["speaker_close", "two_shot", "wide", "no_face", "unknown"]
SpeakerAlignment = Literal["confirmed", "ambiguous", "no_speech", "unavailable"]


class CameraScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=0)
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    layout_id: str
    shot_type: str
    role: CameraRole
    visible_track_ids: list[str]
    dominant_speaker_track_id: str | None = None
    speaker_alignment: SpeakerAlignment
    speech_coverage: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]

    @model_validator(mode="after")
    def validate_interval(self) -> "CameraScene":
        if self.end_us <= self.start_us:
            raise ValueError("end_us must be greater than start_us")
        return self


class CameraTimelineEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str
    algorithm_version: str
    dominant_speaker_min_share: float = Field(ge=0, le=1)
    identity_scope: Literal["layout_track_only"] = "layout_track_only"


class CameraTimelineDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = CAMERA_TIMELINE_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    source_sha256: str
    scene_index_artifact_id: str
    scene_index_input_hash: str
    face_index_artifact_id: str
    face_index_input_hash: str
    speaker_timeline_artifact_id: str
    speaker_timeline_input_hash: str
    input_hash: str
    duration_us: int = Field(ge=0)
    scenes: list[CameraScene]
    engine: CameraTimelineEngineInfo
