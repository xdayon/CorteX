from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

ANALYSIS_SCHEMA_VERSION = 1


class TimeInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    duration: float = Field(ge=0)


class VadInterval(TimeInterval):
    start_sample: int = Field(ge=0)
    end_sample: int = Field(ge=0)


class WaveformResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: int = Field(ge=1)
    frames_per_point: int = Field(ge=1)
    peaks: list[float]
    rms: list[float]


class SpeechDensityWindow(TimeInterval):
    speech_ratio: float = Field(ge=0, le=1)


class LoudnessMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integrated_lufs: float | None
    loudness_range_lu: float | None
    true_peak_dbfs: float | None
    threshold_lufs: float | None
    engine: str
    measurement_scope: str = "normalized_mono_16khz"


class RoomToneSample(TimeInterval):
    rms_dbfs: float


class AnalysisEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vad: str
    vad_version: str
    vad_parameters: dict[str, float | int]
    waveform: str
    loudness: str


class AnalysisDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = ANALYSIS_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    transcript_artifact_id: str
    input_hash: str
    normalized_audio_sha256: str
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    duration_seconds: float = Field(ge=0)
    engines: AnalysisEngineInfo
    waveform: list[WaveformResolution]
    vad_intervals: list[VadInterval]
    pauses: list[TimeInterval]
    speech_density: list[SpeechDensityWindow]
    overall_speech_ratio: float = Field(ge=0, le=1)
    loudness: LoudnessMetrics
    room_tone: list[RoomToneSample]
