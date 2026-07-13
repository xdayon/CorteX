from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SPEAKER_TIMELINE_SCHEMA_VERSION = 2
SpeakerState = Literal["no_speech", "speaker", "unknown", "overlap"]


class TrackScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str
    visible: bool
    mouth_motion_score: float = Field(ge=0, le=1)
    speaking_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence: str = "mouth_motion"


class SpeakerObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_us: int = Field(ge=0)
    scene_index: int = Field(ge=0)
    speech_active: bool
    state: SpeakerState
    speaker_track_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    track_scores: list[TrackScore]

    @model_validator(mode="after")
    def state_matches_speaker(self) -> "SpeakerObservation":
        if (self.state == "speaker") != (self.speaker_track_id is not None):
            raise ValueError("speaker_track_id is required only for speaker observations")
        return self


class SpeakerSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    state: SpeakerState
    speaker_track_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: str

    @model_validator(mode="after")
    def validate_interval_and_state(self) -> "SpeakerSegment":
        if self.end_us <= self.start_us:
            raise ValueError("end_us must be greater than start_us")
        if (self.state == "speaker") != (self.speaker_track_id is not None):
            raise ValueError("speaker_track_id is required only for speaker segments")
        return self


class SpeakerEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str
    algorithm_version: str
    sample_fps_requested: float = Field(gt=0)
    sample_fps_effective: float = Field(gt=0)
    frame_width_requested: int = Field(gt=0)
    frame_width_effective: int = Field(gt=0)
    vad_padding_seconds: float = Field(ge=0)
    motion_threshold: float = Field(ge=0, le=1)
    winner_margin: float = Field(ge=0, le=1)
    ffmpeg_path: str
    ffmpeg_version: str
    decoder_requested: str
    decoder_effective: str
    device_requested: str
    device_effective: str


class SpeakerTimelineDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SPEAKER_TIMELINE_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    source_sha256: str
    scene_index_artifact_id: str
    scene_index_input_hash: str
    face_index_artifact_id: str
    face_index_input_hash: str
    analysis_artifact_id: str
    analysis_input_hash: str
    input_hash: str
    duration_us: int = Field(ge=0)
    observations: list[SpeakerObservation]
    segments: list[SpeakerSegment]
    engine: SpeakerEngineInfo
