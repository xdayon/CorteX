from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REACTION_CANDIDATE_INDEX_SCHEMA_VERSION = 2


class ReactionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    source_start_us: int = Field(ge=0)
    source_end_us: int = Field(gt=0)
    duration_us: int = Field(gt=0)
    scene_index: int = Field(ge=0)
    layout_id: str
    interviewer_identity_id: str
    interviewer_track_id: str
    speech_context: Literal["concurrent_speech", "adjacent_silence"]
    concurrent_speaker_identity_id: str | None = None
    concurrent_speaker_track_id: str | None = None
    reference_speaker_identity_id: str
    reference_speech_time_us: int = Field(ge=0)
    reference_speech_distance_us: int = Field(ge=0)
    observation_count: int = Field(gt=0)
    mean_mouth_motion: float = Field(ge=0, le=1)
    max_mouth_motion: float = Field(ge=0, le=1)
    minimum_speaker_confidence: float = Field(ge=0, le=1)
    visual_quality_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]

    @model_validator(mode="after")
    def validate_interval(self) -> "ReactionCandidate":
        if self.source_end_us <= self.source_start_us:
            raise ValueError("source_end_us must be greater than source_start_us")
        if self.duration_us != self.source_end_us - self.source_start_us:
            raise ValueError("duration_us must match the source interval")
        has_speaker = (
            self.concurrent_speaker_identity_id is not None
            and self.concurrent_speaker_track_id is not None
        )
        if self.speech_context == "concurrent_speech" and not has_speaker:
            raise ValueError("concurrent speech requires a confirmed speaker")
        if self.speech_context == "adjacent_silence" and (
            self.concurrent_speaker_identity_id is not None
            or self.concurrent_speaker_track_id is not None
        ):
            raise ValueError("adjacent silence cannot claim a concurrent speaker")
        if self.interviewer_identity_id in {
            self.concurrent_speaker_identity_id,
            self.reference_speaker_identity_id,
        }:
            raise ValueError("reaction identity must differ from the concurrent speaker")
        return self


class ReactionCandidateDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_count: int = Field(ge=0)
    inspected_observation_count: int = Field(ge=0)
    qualifying_observation_count: int = Field(ge=0)
    rejected_observation_counts: dict[str, int]


class ReactionCandidateEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str
    algorithm_version: str
    minimum_duration_us: int = Field(gt=0)
    maximum_mouth_motion: float = Field(ge=0, le=1)
    maximum_sample_gap_us: int = Field(gt=0)
    maximum_silence_distance_us: int = Field(gt=0)
    identity_scope: str
    role_assignment: str
    audio_policy: str


class ReactionCandidateIndexDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = REACTION_CANDIDATE_INDEX_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    source_sha256: str
    speaker_timeline_artifact_id: str
    speaker_timeline_input_hash: str
    camera_timeline_artifact_id: str
    camera_timeline_input_hash: str
    identity_index_artifact_id: str
    identity_index_input_hash: str
    visual_quality_artifact_id: str
    visual_quality_input_hash: str
    interviewer_identity_id: str
    input_hash: str
    candidates: list[ReactionCandidate]
    diagnostics: ReactionCandidateDiagnostics
    engine: ReactionCandidateEngineInfo
