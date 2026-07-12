from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IDENTITY_INDEX_SCHEMA_VERSION = 1
IdentityStatus = Literal["confirmed", "single_layout", "ambiguous"]


class IdentityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    time_us: int = Field(ge=0)
    scene_index: int = Field(ge=0)
    layout_id: str
    local_track_id: str
    identity_id: str
    status: IdentityStatus
    assignment_similarity: float | None = Field(default=None, ge=-1, le=1)
    ambiguous_match: bool = False


class IdentityEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_id: str
    status: IdentityStatus
    observation_ids: list[str]
    layout_ids: list[str]
    scene_indices: list[int]
    local_track_ids: list[str]
    sample_count: int = Field(gt=0)
    minimum_pair_similarity: float | None = Field(default=None, ge=-1, le=1)
    evidence: list[str]


class IdentityEngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str
    algorithm_version: str
    recognizer: Literal["sface_2021dec"]
    model_path: str
    model_sha256: str
    embedding_dimension: int = Field(gt=0)
    cosine_match_threshold: float = Field(ge=-1, le=1)
    ambiguity_margin: float = Field(ge=0, le=2)
    provider: Literal["CPUExecutionProvider"] = "CPUExecutionProvider"
    identity_scope: Literal["cross_layout_face_embedding"] = "cross_layout_face_embedding"


class IdentityIndexDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = IDENTITY_INDEX_SCHEMA_VERSION
    project_id: str
    source_asset_id: str
    source_sha256: str
    face_index_artifact_id: str
    face_index_input_hash: str
    camera_timeline_artifact_id: str
    camera_timeline_input_hash: str
    input_hash: str
    observations: list[IdentityObservation]
    identities: list[IdentityEntity]
    unresolved_face_count: int = Field(ge=0)
    engine: IdentityEngineInfo
