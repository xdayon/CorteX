from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

TRANSCRIPT_SCHEMA_VERSION = 1


class TranscriptWord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float
    end: float
    word: str
    probability: float


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    start: float
    end: float
    text: str
    avg_logprob: float
    no_speech_prob: float
    words: list[TranscriptWord] = Field(default_factory=list)


class EngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "faster-whisper"
    model: str
    requested_device: str
    effective_device: str
    requested_compute_type: str
    effective_compute_type: str
    batch_size: int
    vad: bool
    fallback: bool = False


class TranscriptDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = TRANSCRIPT_SCHEMA_VERSION
    language: str | None = None
    language_probability: float | None = None
    duration_seconds: float
    engine: EngineInfo
    segments: list[TranscriptSegment] = Field(default_factory=list)
