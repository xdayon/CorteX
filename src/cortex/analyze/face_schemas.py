from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

FACE_INDEX_SCHEMA_VERSION = 1


class FaceLandmarks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    right_eye: tuple[float, float]
    left_eye: tuple[float, float]
    nose_tip: tuple[float, float]
    right_mouth_corner: tuple[float, float]
    left_mouth_corner: tuple[float, float]


class FaceDetection(BaseModel):
    """One detected face in one sampled frame, in normalized (0-1) coordinates."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=0, le=1)
    height: float = Field(ge=0, le=1)
    score: float = Field(ge=0, le=1)
    landmarks: FaceLandmarks
    track_id: str | None = None
    embedding: list[float] | None = None


class FrameFaces(BaseModel):
    """All faces detected in one sampled frame, plus the shot classification."""

    model_config = ConfigDict(extra="forbid")

    time: float = Field(ge=0)
    faces: list[FaceDetection]
    shot_type: str


class SceneFaceSummary(BaseModel):
    """Aggregated face/shot data for one SceneSegment of the scene_index."""

    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=0)
    dominant_shot_type: str
    track_ids_present: list[str]
    sample_count: int = Field(ge=0)


class FaceIndexEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detector: str
    model_path: str
    providers: list[str]
    score_threshold: float
    nms_threshold: float
    sample_fps: float
    ffmpeg_path: str
    ffmpeg_version: str


class FaceIndexDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = FACE_INDEX_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    source_sha256: str
    scene_index_artifact_id: str
    input_hash: str
    duration_seconds: float = Field(ge=0)
    frames: list[FrameFaces]
    scenes: list[SceneFaceSummary]
    frame_count: int = Field(ge=0)
    engine: FaceIndexEngineInfo
