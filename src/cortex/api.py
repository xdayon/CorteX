from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cortex.analyze.camera_schemas import CameraTimelineDocument
from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument
from cortex.analyze.reaction_candidate_schemas import ReactionCandidateIndexDocument
from cortex.analyze.multicam_sync_schemas import MulticamSyncDocument
from cortex.analyze.multicam_visual_schemas import MulticamVisualIndexDocument
from cortex.analyze.speaker_schemas import SpeakerTimelineDocument
from cortex.analyze.visual_quality_schemas import VisualQualityDocument
from cortex.config import CortexConfig, load_config
from cortex.domain.models import RenderPreset, SourceAsset, SourceKind
from cortex.domain.store import (
    DomainStore,
    ProjectNotFoundError,
    RenderPresetNameConflictError,
    RenderPresetNotFoundError,
    SourceAssetNotFoundError,
    StageArtifactNotFoundError,
    TranscriptArtifactNotFoundError,
)
from cortex.edit.camera_plan_schemas import CameraEditPlanDocument
from cortex.edit.schemas import EditPlanDocument
from cortex.hardware import collect_hardware_snapshot
from cortex.ingest.ffprobe import FFprobeError, probe_media
from cortex.ingest.upload import UploadValidationError, store_upload_stream
from cortex.jobs import InvalidJobTransitionError, JobNotFoundError, JobStore
from cortex.paths import renders_dir, source_dir
from cortex.project_status import read_project_status
from cortex.render.schemas import RenderDocument, RenderSettings, RenderSettingsPatch
from cortex.schemas import JobCreate, JobStatus, JobType


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)


class RenderPresetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    settings: RenderSettings


class RenderPresetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    settings: RenderSettings | None = None

    @model_validator(mode="after")
    def require_change(self) -> "RenderPresetUpdate":
        if self.name is None and self.settings is None:
            raise ValueError("name ou settings é obrigatório")
        return self


class YoutubeSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)


class TranscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str
    model: str | None = None
    language: str | None = None
    batch_size: int | None = Field(default=None, ge=0, le=64)
    vad: bool | None = None


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript_artifact_id: str


class EditPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript_artifact_id: str
    analysis_artifact_id: str
    scene_index_artifact_id: str | None = None
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    profile: str = "auto"

    @model_validator(mode="after")
    def clip_order(self) -> "EditPlanRequest":
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class SceneIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str
    scene_threshold: float | None = Field(default=None, ge=0.0, le=100.0)


class FaceIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str
    scene_index_artifact_id: str
    face_sample_fps: float | None = Field(default=None, gt=0.0, le=10.0)


class SpeakerTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str
    scene_index_artifact_id: str
    face_index_artifact_id: str
    analysis_artifact_id: str
    sample_fps: float | None = Field(default=None, ge=1.0, le=10.0)


class CameraTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_index_artifact_id: str
    face_index_artifact_id: str
    speaker_timeline_artifact_id: str


class VisualQualityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str
    scene_index_artifact_id: str
    face_index_artifact_id: str
    sample_fps: float | None = Field(default=None, gt=0.0, le=10.0)


class IdentityIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    face_index_artifact_id: str
    camera_timeline_artifact_id: str


class ReactionCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_timeline_artifact_id: str
    camera_timeline_artifact_id: str
    identity_index_artifact_id: str
    visual_quality_artifact_id: str
    interviewer_identity_id: str = Field(min_length=1)
    min_duration_seconds: float | None = Field(default=None, gt=0.0, le=10.0)


class CameraEditPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edit_plan_artifact_id: str
    camera_timeline_artifact_id: str
    identity_index_artifact_id: str
    visual_quality_artifact_id: str
    reaction_candidate_artifact_id: str | None = None
    multicam_visual_artifact_id: str | None = None


class MulticamSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_source_asset_id: str
    alternate_source_asset_ids: list[str] = Field(min_length=1, max_length=8)
    max_offset_seconds: float | None = Field(default=None, gt=0.0, le=3600.0)
    analysis_seconds: float | None = Field(default=None, ge=10.0, le=7200.0)

    @model_validator(mode="after")
    def validate_sources(self) -> "MulticamSyncRequest":
        if len(set(self.alternate_source_asset_ids)) != len(self.alternate_source_asset_ids):
            raise ValueError("alternate_source_asset_ids must be unique")
        if self.primary_source_asset_id in self.alternate_source_asset_ids:
            raise ValueError("primary source cannot also be an alternate")
        return self


class MulticamVisualRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    multicam_sync_artifact_id: str


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edit_plan_artifact_id: str
    camera_edit_plan_artifact_id: str | None = None
    face_index_artifact_id: str | None = None
    identity_index_artifact_id: str | None = None
    target_identity_id: str | None = Field(default=None, min_length=1)
    encoder: str | None = None
    headline: str | None = Field(default=None, max_length=120)
    render_settings: RenderSettings | None = None
    render_settings_override: RenderSettingsPatch | None = None
    export_directory: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def reject_mixed_settings(self) -> RenderRequest:
        if self.render_settings is not None and (
            self.encoder is not None or self.headline is not None or self.render_settings_override is not None
        ):
            raise ValueError("render_settings não pode ser combinado com encoder/headline legados")
        face_fields = (
            self.face_index_artifact_id,
            self.identity_index_artifact_id,
            self.target_identity_id,
        )
        face_mode = bool(
            self.render_settings is not None
            and self.render_settings.framing.mode == "face_static_crop"
        )
        if (face_mode or any(face_fields)) and not all(face_fields):
            raise ValueError("face_static_crop exige face_index, identity_index e target_identity_id")
        return self


class SuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript_artifact_id: str
    analysis_artifact_id: str
    count: int = Field(default=10, ge=1, le=25)
    minimum_seconds: int = Field(default=50, ge=15, le=180)
    maximum_seconds: int = Field(default=120, ge=15, le=180)
    primary_subject: str | None = Field(default=None, max_length=200)
    topic: str | None = Field(default=None, max_length=500)
    instructions: str | None = Field(default=None, max_length=4000)
    provider: Literal["codex_cli", "claude_cli", "local_heuristic"] | None = Field(default=None)

    @model_validator(mode="after")
    def duration_order(self) -> "SuggestionRequest":
        if self.minimum_seconds > self.maximum_seconds:
            raise ValueError("minimum_seconds cannot exceed maximum_seconds")
        return self


def create_app(config: CortexConfig | None = None):
    from fastapi import FastAPI, File, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, StreamingResponse

    settings = config or load_config()
    settings.ensure_runtime_dirs()
    jobs = JobStore(settings.paths.database)
    domain = DomainStore(settings.paths.database)
    app = FastAPI(title=settings.app.name, version="0.1.0")
    app.state.config = settings
    app.state.jobs = jobs
    app.state.domain = domain
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    router_prefix = settings.app.api_prefix.rstrip("/")

    @app.get(f"{router_prefix}/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "cortex", "version": "0.1.0"}

    @app.get(f"{router_prefix}/hardware")
    def hardware():
        return collect_hardware_snapshot()

    @app.get(f"{router_prefix}/project-status")
    def project_status():
        return read_project_status()

    @app.post(f"{router_prefix}/projects", status_code=201)
    def create_project(request: ProjectCreate):
        return domain.create_project(request.name)

    @app.get(f"{router_prefix}/projects")
    def list_projects(limit: int = Query(default=100, ge=1, le=500)):
        return domain.list_projects(limit)

    @app.get(f"{router_prefix}/projects/{{project_id}}")
    def get_project(project_id: str):
        try:
            return domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc

    @app.get(f"{router_prefix}/projects/{{project_id}}/render-presets")
    def list_render_presets(project_id: str):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        return domain.list_render_presets(project_id)

    @app.post(f"{router_prefix}/projects/{{project_id}}/render-presets", status_code=201)
    def create_render_preset(project_id: str, request: RenderPresetCreate):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        try:
            return domain.create_render_preset(RenderPreset(
                project_id=project_id,
                name=request.name,
                settings=request.settings.model_dump(mode="json"),
            ))
        except RenderPresetNameConflictError as exc:
            raise HTTPException(status_code=409, detail="Já existe um preset com este nome") from exc

    @app.put(f"{router_prefix}/projects/{{project_id}}/render-presets/{{preset_id}}")
    def update_render_preset(project_id: str, preset_id: str, request: RenderPresetUpdate):
        try:
            domain.get_project(project_id)
            return domain.update_render_preset(
                project_id,
                preset_id,
                name=request.name,
                settings=(request.settings.model_dump(mode="json") if request.settings else None),
            )
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        except RenderPresetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Preset não encontrado") from exc
        except RenderPresetNameConflictError as exc:
            raise HTTPException(status_code=409, detail="Já existe um preset com este nome") from exc

    @app.post(f"{router_prefix}/projects/{{project_id}}/sources/upload", status_code=201)
    async def upload_source(project_id: str, file: UploadFile = File(...)):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc

        try:
            stored = await store_upload_stream(
                file,
                file.filename or "upload",
                destination_dir=source_dir(settings, project_id),
                allowed_extensions=set(settings.ingest.allowed_extensions),
                max_bytes=settings.ingest.max_upload_bytes,
            )
        except UploadValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        try:
            probe = probe_media(settings.render.ffprobe, stored.path)
        except FFprobeError as exc:
            stored.path.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=f"ffprobe falhou: {exc}") from exc

        stored_path = stored.path.relative_to(settings.paths.data_dir)
        asset = domain.create_source_asset(SourceAsset(
            project_id=project_id,
            kind=SourceKind.UPLOAD,
            original_filename=file.filename,
            stored_path=str(stored_path),
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            probe=probe,
        ))
        return asset

    @app.post(f"{router_prefix}/projects/{{project_id}}/sources/youtube", status_code=201)
    def create_youtube_source(project_id: str, request: YoutubeSourceCreate):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        return jobs.create(JobCreate(
            type=JobType.INGEST_YOUTUBE,
            project_id=project_id,
            payload={"url": request.url},
        ))

    @app.post(f"{router_prefix}/projects/{{project_id}}/transcribe", status_code=201)
    def create_transcribe_job(project_id: str, request: TranscribeRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        try:
            asset = domain.get_source_asset(request.source_asset_id)
        except SourceAssetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Fonte não encontrada") from exc
        if asset.project_id != project_id:
            raise HTTPException(status_code=400, detail="Fonte não pertence a este projeto")

        overrides: dict[str, Any] = {}
        if request.model is not None:
            overrides["model"] = request.model
        if request.language is not None:
            overrides["language"] = request.language
        if request.batch_size is not None:
            overrides["batch_size"] = request.batch_size
        if request.vad is not None:
            overrides["vad"] = request.vad

        return jobs.create(JobCreate(
            type=JobType.TRANSCRIPTION,
            project_id=project_id,
            payload={"source_asset_id": request.source_asset_id, "overrides": overrides},
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/transcripts/{{artifact_id}}")
    def get_transcript(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_transcript_artifact(artifact_id)
        except TranscriptArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Transcript não encontrado") from exc
        if artifact.project_id != project_id:
            raise HTTPException(status_code=404, detail="Transcript não encontrado")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo do transcript não está disponível")
        return {
            "artifact": artifact,
            "document": json.loads(path.read_text(encoding="utf-8")),
        }

    @app.post(f"{router_prefix}/projects/{{project_id}}/analyze", status_code=201)
    def create_analysis_job(project_id: str, request: AnalysisRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        try:
            transcript = domain.get_transcript_artifact(request.transcript_artifact_id)
        except TranscriptArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Transcript não encontrado") from exc
        if transcript.project_id != project_id:
            raise HTTPException(status_code=400, detail="Transcript não pertence a este projeto")
        return jobs.create(JobCreate(
            type=JobType.ANALYSIS,
            project_id=project_id,
            payload={"transcript_artifact_id": transcript.id},
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/analysis/{{artifact_id}}")
    def get_analysis(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Análise não encontrada") from exc
        if artifact.project_id != project_id or artifact.stage != "analysis":
            raise HTTPException(status_code=404, detail="Análise não encontrada")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo da análise não está disponível")
        return {
            "artifact": artifact,
            "document": json.loads(path.read_text(encoding="utf-8")),
        }

    @app.post(f"{router_prefix}/projects/{{project_id}}/scenes", status_code=201)
    def create_scene_index_job(project_id: str, request: SceneIndexRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        try:
            asset = domain.get_source_asset(request.source_asset_id)
        except SourceAssetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Fonte não encontrada") from exc
        if asset.project_id != project_id:
            raise HTTPException(status_code=400, detail="Fonte não pertence a este projeto")
        source_path = settings.paths.data_dir / asset.stored_path
        if not source_path.exists():
            raise HTTPException(
                status_code=409, detail="Arquivo da fonte não está disponível para detecção de cenas"
            )
        return jobs.create(JobCreate(
            type=JobType.SCENE_ANALYSIS,
            project_id=project_id,
            payload={"source_asset_id": asset.id, "scene_threshold": request.scene_threshold},
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/scenes/{{artifact_id}}")
    def get_scene_index(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Índice de cenas não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "scene_index":
            raise HTTPException(status_code=404, detail="Índice de cenas não encontrado")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo do índice de cenas não está disponível")
        return {
            "artifact": artifact,
            "document": json.loads(path.read_text(encoding="utf-8")),
        }

    @app.post(f"{router_prefix}/projects/{{project_id}}/faces", status_code=201)
    def create_face_index_job(project_id: str, request: FaceIndexRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        try:
            asset = domain.get_source_asset(request.source_asset_id)
        except SourceAssetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Fonte não encontrada") from exc
        if asset.project_id != project_id:
            raise HTTPException(status_code=400, detail="Fonte não pertence a este projeto")
        source_path = settings.paths.data_dir / asset.stored_path
        if not source_path.exists():
            raise HTTPException(
                status_code=409, detail="Arquivo da fonte não está disponível para detecção de faces"
            )
        try:
            scene_index = domain.get_stage_artifact(request.scene_index_artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="Índice de cenas não encontrado; execute a detecção de cenas antes das faces",
            ) from exc
        if scene_index.project_id != project_id or scene_index.stage != "scene_index":
            raise HTTPException(status_code=400, detail="Índice de cenas não pertence a este projeto")
        if not Path(scene_index.path).exists():
            raise HTTPException(
                status_code=409, detail="Arquivo do índice de cenas não está disponível para detecção de faces"
            )
        return jobs.create(JobCreate(
            type=JobType.FACE_ANALYSIS,
            project_id=project_id,
            payload={
                "source_asset_id": asset.id,
                "scene_index_artifact_id": scene_index.id,
                "face_sample_fps": request.face_sample_fps,
            },
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/faces/{{artifact_id}}")
    def get_face_index(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Índice de faces não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "face_index":
            raise HTTPException(status_code=404, detail="Índice de faces não encontrado")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo do índice de faces não está disponível")
        return {
            "artifact": artifact,
            "document": json.loads(path.read_text(encoding="utf-8")),
        }

    @app.post(f"{router_prefix}/projects/{{project_id}}/speakers", status_code=201)
    def create_speaker_timeline_job(project_id: str, request: SpeakerTimelineRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        try:
            asset = domain.get_source_asset(request.source_asset_id)
        except SourceAssetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Fonte não encontrada") from exc
        if asset.project_id != project_id:
            raise HTTPException(status_code=400, detail="Fonte não pertence a este projeto")
        if not (settings.paths.data_dir / asset.stored_path).exists():
            raise HTTPException(
                status_code=409,
                detail="Arquivo da fonte não está disponível para tracking de interlocutor",
            )

        upstream_specs = (
            (request.scene_index_artifact_id, "scene_index", "Índice de cenas"),
            (request.face_index_artifact_id, "face_index", "Índice de faces"),
            (request.analysis_artifact_id, "analysis", "Análise"),
        )
        upstreams = {}
        for artifact_id, expected_stage, label in upstream_specs:
            try:
                upstream = domain.get_stage_artifact(artifact_id)
            except StageArtifactNotFoundError as exc:
                raise HTTPException(status_code=404, detail=f"{label} não encontrado") from exc
            if upstream.project_id != project_id or upstream.stage != expected_stage:
                raise HTTPException(status_code=400, detail=f"{label} não pertence a este projeto")
            if not Path(upstream.path).exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"Arquivo de {label.lower()} não está disponível para tracking de interlocutor",
                )
            upstreams[expected_stage] = upstream

        return jobs.create(JobCreate(
            type=JobType.SPEAKER_ANALYSIS,
            project_id=project_id,
            payload={
                "source_asset_id": asset.id,
                "scene_index_artifact_id": upstreams["scene_index"].id,
                "face_index_artifact_id": upstreams["face_index"].id,
                "analysis_artifact_id": upstreams["analysis"].id,
                "sample_fps": request.sample_fps,
            },
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/speakers/{{artifact_id}}")
    def get_speaker_timeline(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Timeline de interlocutores não encontrada") from exc
        if artifact.project_id != project_id or artifact.stage != "speaker_timeline":
            raise HTTPException(status_code=404, detail="Timeline de interlocutores não encontrada")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(
                status_code=410,
                detail="Arquivo da timeline de interlocutores não está disponível",
            )
        try:
            document = SpeakerTimelineDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(
                status_code=410,
                detail="Arquivo da timeline de interlocutores não está disponível",
            ) from exc
        return {"artifact": artifact, "document": document}

    @app.post(f"{router_prefix}/projects/{{project_id}}/cameras", status_code=201)
    def create_camera_timeline_job(project_id: str, request: CameraTimelineRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        upstream_specs = (
            (request.scene_index_artifact_id, "scene_index", "Índice de cenas"),
            (request.face_index_artifact_id, "face_index", "Índice de faces"),
            (request.speaker_timeline_artifact_id, "speaker_timeline", "Timeline de interlocutores"),
        )
        upstreams = {}
        for artifact_id, expected_stage, label in upstream_specs:
            try:
                upstream = domain.get_stage_artifact(artifact_id)
            except StageArtifactNotFoundError as exc:
                raise HTTPException(status_code=404, detail=f"{label} não encontrado") from exc
            if upstream.project_id != project_id or upstream.stage != expected_stage:
                raise HTTPException(status_code=400, detail=f"{label} não pertence a este projeto")
            if not Path(upstream.path).exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"Arquivo de {label.lower()} não está disponível para timeline de cameras",
                )
            upstreams[expected_stage] = upstream
        return jobs.create(JobCreate(
            type=JobType.CAMERA_ANALYSIS,
            project_id=project_id,
            payload={
                "scene_index_artifact_id": upstreams["scene_index"].id,
                "face_index_artifact_id": upstreams["face_index"].id,
                "speaker_timeline_artifact_id": upstreams["speaker_timeline"].id,
            },
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/cameras/{{artifact_id}}")
    def get_camera_timeline(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Timeline de cameras não encontrada") from exc
        if artifact.project_id != project_id or artifact.stage != "camera_timeline":
            raise HTTPException(status_code=404, detail="Timeline de cameras não encontrada")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo da timeline de cameras não disponível")
        try:
            document = CameraTimelineDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(
                status_code=410, detail="Arquivo da timeline de cameras não disponível"
            ) from exc
        return {"artifact": artifact, "document": document}

    @app.post(f"{router_prefix}/projects/{{project_id}}/visual-quality", status_code=201)
    def create_visual_quality_job(project_id: str, request: VisualQualityRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        try:
            asset = domain.get_source_asset(request.source_asset_id)
        except SourceAssetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Fonte não encontrada") from exc
        if asset.project_id != project_id:
            raise HTTPException(status_code=400, detail="Fonte não pertence a este projeto")
        if not (settings.paths.data_dir / asset.stored_path).exists():
            raise HTTPException(
                status_code=409,
                detail="Arquivo da fonte não está disponível para qualidade visual",
            )
        upstream_specs = (
            (request.scene_index_artifact_id, "scene_index", "Índice de cenas"),
            (request.face_index_artifact_id, "face_index", "Índice de faces"),
        )
        upstreams = {}
        for artifact_id, expected_stage, label in upstream_specs:
            try:
                upstream = domain.get_stage_artifact(artifact_id)
            except StageArtifactNotFoundError as exc:
                raise HTTPException(status_code=404, detail=f"{label} não encontrado") from exc
            if upstream.project_id != project_id or upstream.stage != expected_stage:
                raise HTTPException(status_code=400, detail=f"{label} não pertence a este projeto")
            if not Path(upstream.path).exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"Arquivo de {label.lower()} não está disponível para qualidade visual",
                )
            upstreams[expected_stage] = upstream
        return jobs.create(JobCreate(
            type=JobType.VISUAL_QUALITY_ANALYSIS,
            project_id=project_id,
            payload={
                "source_asset_id": asset.id,
                "scene_index_artifact_id": upstreams["scene_index"].id,
                "face_index_artifact_id": upstreams["face_index"].id,
                "sample_fps": request.sample_fps,
            },
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/visual-quality/{{artifact_id}}")
    def get_visual_quality(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Índice de qualidade visual não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "visual_quality_index":
            raise HTTPException(status_code=404, detail="Índice de qualidade visual não encontrado")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo da qualidade visual não disponível")
        try:
            document = VisualQualityDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(
                status_code=410, detail="Arquivo da qualidade visual não disponível"
            ) from exc
        return {"artifact": artifact, "document": document}

    @app.post(f"{router_prefix}/projects/{{project_id}}/identities", status_code=201)
    def create_identity_index_job(project_id: str, request: IdentityIndexRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        upstream_specs = (
            (request.face_index_artifact_id, "face_index", "Índice de faces"),
            (request.camera_timeline_artifact_id, "camera_timeline", "Timeline de cameras"),
        )
        upstreams = {}
        for artifact_id, expected_stage, label in upstream_specs:
            try:
                upstream = domain.get_stage_artifact(artifact_id)
            except StageArtifactNotFoundError as exc:
                raise HTTPException(status_code=404, detail=f"{label} não encontrado") from exc
            if upstream.project_id != project_id or upstream.stage != expected_stage:
                raise HTTPException(status_code=400, detail=f"{label} não pertence a este projeto")
            if not Path(upstream.path).exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"Arquivo de {label.lower()} não está disponível para identidade",
                )
            upstreams[expected_stage] = upstream
        return jobs.create(JobCreate(
            type=JobType.IDENTITY_ANALYSIS,
            project_id=project_id,
            payload={
                "face_index_artifact_id": upstreams["face_index"].id,
                "camera_timeline_artifact_id": upstreams["camera_timeline"].id,
            },
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/identities/{{artifact_id}}")
    def get_identity_index(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Índice de identidade não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "identity_index":
            raise HTTPException(status_code=404, detail="Índice de identidade não encontrado")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo do índice de identidade não disponível")
        try:
            document = IdentityIndexDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(
                status_code=410, detail="Arquivo do índice de identidade não disponível"
            ) from exc
        return {"artifact": artifact, "document": document}

    @app.post(f"{router_prefix}/projects/{{project_id}}/reaction-candidates", status_code=201)
    def create_reaction_candidate_job(project_id: str, request: ReactionCandidateRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        upstream_specs = (
            (request.speaker_timeline_artifact_id, "speaker_timeline", "Timeline de speakers"),
            (request.camera_timeline_artifact_id, "camera_timeline", "Timeline de cameras"),
            (request.identity_index_artifact_id, "identity_index", "Índice de identidade"),
            (request.visual_quality_artifact_id, "visual_quality_index", "Qualidade visual"),
        )
        upstreams = {}
        for artifact_id, expected_stage, label in upstream_specs:
            try:
                upstream = domain.get_stage_artifact(artifact_id)
            except StageArtifactNotFoundError as exc:
                raise HTTPException(status_code=404, detail=f"{label} não encontrado") from exc
            if upstream.project_id != project_id or upstream.stage != expected_stage:
                raise HTTPException(status_code=400, detail=f"{label} não pertence a este projeto")
            if not Path(upstream.path).exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"Arquivo de {label.lower()} não está disponível para reactions",
                )
            upstreams[expected_stage] = upstream
        return jobs.create(JobCreate(
            type=JobType.REACTION_CANDIDATE_ANALYSIS,
            project_id=project_id,
            payload={
                "speaker_timeline_artifact_id": upstreams["speaker_timeline"].id,
                "camera_timeline_artifact_id": upstreams["camera_timeline"].id,
                "identity_index_artifact_id": upstreams["identity_index"].id,
                "visual_quality_artifact_id": upstreams["visual_quality_index"].id,
                "interviewer_identity_id": request.interviewer_identity_id,
                "min_duration_seconds": request.min_duration_seconds,
            },
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/reaction-candidates/{{artifact_id}}")
    def get_reaction_candidates(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Banco de reactions não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "reaction_candidate_index":
            raise HTTPException(status_code=404, detail="Banco de reactions não encontrado")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo do banco de reactions não disponível")
        try:
            document = ReactionCandidateIndexDocument.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(
                status_code=410, detail="Arquivo do banco de reactions não disponível"
            ) from exc
        return {"artifact": artifact, "document": document}

    @app.post(f"{router_prefix}/projects/{{project_id}}/multicam-sync", status_code=201)
    def create_multicam_sync_job(project_id: str, request: MulticamSyncRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        source_ids = [request.primary_source_asset_id, *request.alternate_source_asset_ids]
        sources = []
        for source_id in source_ids:
            try:
                source = domain.get_source_asset(source_id)
            except SourceAssetNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Fonte multicamera não encontrada") from exc
            if source.project_id != project_id:
                raise HTTPException(status_code=400, detail="Fonte multicamera não pertence ao projeto")
            if not (settings.paths.data_dir / source.stored_path).exists():
                raise HTTPException(
                    status_code=409,
                    detail="Arquivo de fonte multicamera não está disponível",
                )
            sources.append(source)
        return jobs.create(JobCreate(
            type=JobType.MULTICAM_SYNC,
            project_id=project_id,
            payload={
                "primary_source_asset_id": sources[0].id,
                "alternate_source_asset_ids": [source.id for source in sources[1:]],
                "max_offset_seconds": request.max_offset_seconds,
                "analysis_seconds": request.analysis_seconds,
            },
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/multicam-sync/{{artifact_id}}")
    def get_multicam_sync(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Sincronização multicamera não encontrada") from exc
        if artifact.project_id != project_id or artifact.stage != "multicam_sync":
            raise HTTPException(status_code=404, detail="Sincronização multicamera não encontrada")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo multicamera não disponível")
        try:
            document = MulticamSyncDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(status_code=410, detail="Arquivo multicamera não disponível") from exc
        return {"artifact": artifact, "document": document}

    @app.post(f"{router_prefix}/projects/{{project_id}}/multicam-visual", status_code=201)
    def create_multicam_visual_job(project_id: str, request: MulticamVisualRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        try:
            sync_artifact = domain.get_stage_artifact(request.multicam_sync_artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Sincronização multicamera não encontrada") from exc
        if sync_artifact.project_id != project_id or sync_artifact.stage != "multicam_sync":
            raise HTTPException(status_code=400, detail="Sincronização multicamera não pertence ao projeto")
        if not Path(sync_artifact.path).exists():
            raise HTTPException(status_code=409, detail="Arquivo multicamera não está disponível")
        return jobs.create(JobCreate(
            type=JobType.MULTICAM_VISUAL_INDEX,
            project_id=project_id,
            payload={"multicam_sync_artifact_id": sync_artifact.id},
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/multicam-visual/{{artifact_id}}")
    def get_multicam_visual(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Índices visuais multicamera não encontrados") from exc
        if artifact.project_id != project_id or artifact.stage != "multicam_visual_index":
            raise HTTPException(status_code=404, detail="Índices visuais multicamera não encontrados")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo visual multicamera não disponível")
        try:
            document = MulticamVisualIndexDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(status_code=410, detail="Arquivo visual multicamera não disponível") from exc
        return {"artifact": artifact, "document": document}

    @app.post(f"{router_prefix}/projects/{{project_id}}/camera-plans", status_code=201)
    def create_camera_edit_plan_job(project_id: str, request: CameraEditPlanRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        upstream_specs = (
            (request.edit_plan_artifact_id, "edit_plan", "Plano de edição"),
            (request.camera_timeline_artifact_id, "camera_timeline", "Timeline de cameras"),
            (request.identity_index_artifact_id, "identity_index", "Índice de identidade"),
            (request.visual_quality_artifact_id, "visual_quality_index", "Qualidade visual"),
        )
        upstreams = {}
        for artifact_id, expected_stage, label in upstream_specs:
            try:
                upstream = domain.get_stage_artifact(artifact_id)
            except StageArtifactNotFoundError as exc:
                raise HTTPException(status_code=404, detail=f"{label} não encontrado") from exc
            if upstream.project_id != project_id or upstream.stage != expected_stage:
                raise HTTPException(status_code=400, detail=f"{label} não pertence a este projeto")
            if not Path(upstream.path).exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"Arquivo de {label.lower()} não está disponível para camera plan",
                )
            upstreams[expected_stage] = upstream
        multicam_visual = None
        reaction_candidates = None
        if request.reaction_candidate_artifact_id is not None:
            try:
                reaction_candidates = domain.get_stage_artifact(request.reaction_candidate_artifact_id)
            except StageArtifactNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Banco de reactions não encontrado") from exc
            if (
                reaction_candidates.project_id != project_id
                or reaction_candidates.stage != "reaction_candidate_index"
            ):
                raise HTTPException(status_code=400, detail="Banco de reactions não pertence a este projeto")
            if not Path(reaction_candidates.path).exists():
                raise HTTPException(status_code=409, detail="Arquivo do banco de reactions não está disponível")
        if request.multicam_visual_artifact_id is not None:
            try:
                multicam_visual = domain.get_stage_artifact(request.multicam_visual_artifact_id)
            except StageArtifactNotFoundError as exc:
                raise HTTPException(
                    status_code=404, detail="Índices visuais multicamera não encontrados"
                ) from exc
            if (
                multicam_visual.project_id != project_id
                or multicam_visual.stage != "multicam_visual_index"
            ):
                raise HTTPException(
                    status_code=400, detail="Índices visuais multicamera não pertencem ao projeto"
                )
            if not Path(multicam_visual.path).exists():
                raise HTTPException(
                    status_code=409, detail="Arquivo visual multicamera não disponível"
                )
        return jobs.create(JobCreate(
            type=JobType.CAMERA_PLANNING,
            project_id=project_id,
            payload={
                "edit_plan_artifact_id": upstreams["edit_plan"].id,
                "camera_timeline_artifact_id": upstreams["camera_timeline"].id,
                "identity_index_artifact_id": upstreams["identity_index"].id,
                "visual_quality_artifact_id": upstreams["visual_quality_index"].id,
                "reaction_candidate_artifact_id": (
                    reaction_candidates.id if reaction_candidates is not None else None
                ),
                "multicam_visual_artifact_id": (
                    multicam_visual.id if multicam_visual is not None else None
                ),
            },
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/camera-plans/{{artifact_id}}")
    def get_camera_edit_plan(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Camera edit plan não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "camera_edit_plan":
            raise HTTPException(status_code=404, detail="Camera edit plan não encontrado")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo do camera edit plan não disponível")
        try:
            document = CameraEditPlanDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(
                status_code=410, detail="Arquivo do camera edit plan não disponível"
            ) from exc
        return {"artifact": artifact, "document": document}

    @app.post(f"{router_prefix}/projects/{{project_id}}/edit-plans", status_code=201)
    def create_edit_plan_job(project_id: str, request: EditPlanRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        try:
            transcript = domain.get_transcript_artifact(request.transcript_artifact_id)
        except TranscriptArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Transcript não encontrado") from exc
        if transcript.project_id != project_id:
            raise HTTPException(status_code=400, detail="Transcript não pertence a este projeto")
        try:
            analysis = domain.get_stage_artifact(request.analysis_artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Análise não encontrada") from exc
        if analysis.project_id != project_id or analysis.stage != "analysis":
            raise HTTPException(status_code=400, detail="Análise não pertence a este projeto")
        if request.scene_index_artifact_id is not None:
            try:
                scene_index = domain.get_stage_artifact(request.scene_index_artifact_id)
            except StageArtifactNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Índice de cenas não encontrado") from exc
            if scene_index.project_id != project_id or scene_index.stage != "scene_index":
                raise HTTPException(status_code=400, detail="Índice de cenas não pertence a este projeto")
        return jobs.create(JobCreate(
            type=JobType.EDIT_PLAN,
            project_id=project_id,
            payload={
                "transcript_artifact_id": request.transcript_artifact_id,
                "analysis_artifact_id": request.analysis_artifact_id,
                "scene_index_artifact_id": request.scene_index_artifact_id,
                "start": request.start,
                "end": request.end,
                "profile": request.profile,
            },
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/edit-plans/{{artifact_id}}")
    def get_edit_plan(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Plano de edição não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "edit_plan":
            raise HTTPException(status_code=404, detail="Plano de edição não encontrado")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo do plano de edição não está disponível")
        return {
            "artifact": artifact,
            "document": json.loads(path.read_text(encoding="utf-8")),
        }

    @app.post(f"{router_prefix}/projects/{{project_id}}/renders", status_code=201)
    def create_render_job(project_id: str, request: RenderRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        try:
            edit_plan = domain.get_stage_artifact(request.edit_plan_artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Plano de edição não encontrado") from exc
        if edit_plan.project_id != project_id or edit_plan.stage != "edit_plan":
            raise HTTPException(status_code=400, detail="Plano de edição não pertence ao projeto")
        camera_plan = None
        if request.camera_edit_plan_artifact_id is not None:
            try:
                camera_plan = domain.get_stage_artifact(request.camera_edit_plan_artifact_id)
            except StageArtifactNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Plano de câmera não encontrado") from exc
            if camera_plan.project_id != project_id or camera_plan.stage != "camera_edit_plan":
                raise HTTPException(status_code=400, detail="Plano de câmera não pertence ao projeto")
        visual_artifacts = {}
        for field, stage, label in (
            (request.face_index_artifact_id, "face_index", "Índice de faces"),
            (request.identity_index_artifact_id, "identity_index", "Índice de identidades"),
        ):
            if field is None:
                continue
            try:
                artifact = domain.get_stage_artifact(field)
            except StageArtifactNotFoundError as exc:
                raise HTTPException(status_code=404, detail=f"{label} não encontrado") from exc
            if artifact.project_id != project_id or artifact.stage != stage:
                raise HTTPException(status_code=400, detail=f"{label} não pertence ao projeto")
            if not Path(artifact.path).is_file():
                raise HTTPException(status_code=409, detail=f"Arquivo de {label.lower()} indisponível")
            visual_artifacts[stage] = artifact
        if visual_artifacts:
            try:
                edit_document = EditPlanDocument.model_validate_json(
                    Path(edit_plan.path).read_text(encoding="utf-8")
                )
                face_document = FaceIndexDocument.model_validate_json(
                    Path(visual_artifacts["face_index"].path).read_text(encoding="utf-8")
                )
                identity_document = IdentityIndexDocument.model_validate_json(
                    Path(visual_artifacts["identity_index"].path).read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError, ValidationError, KeyError) as exc:
                raise HTTPException(status_code=409, detail="Cadeia de identidade indisponível") from exc
            if (
                face_document.source_asset_id != edit_document.source_asset_id
                or identity_document.source_asset_id != edit_document.source_asset_id
                or identity_document.face_index_artifact_id != visual_artifacts["face_index"].id
                or identity_document.face_index_input_hash != visual_artifacts["face_index"].input_hash
                or not any(
                    identity.identity_id == request.target_identity_id
                    and identity.status == "confirmed"
                    for identity in identity_document.identities
                )
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Índices visuais ou identidade confirmada não correspondem à fonte",
                )
        return jobs.create(JobCreate(
            type=JobType.RENDER,
            project_id=project_id,
            payload={
                "edit_plan_artifact_id": edit_plan.id,
                "camera_edit_plan_artifact_id": camera_plan.id if camera_plan else None,
                "face_index_artifact_id": visual_artifacts.get("face_index").id if visual_artifacts.get("face_index") else None,
                "identity_index_artifact_id": visual_artifacts.get("identity_index").id if visual_artifacts.get("identity_index") else None,
                "target_identity_id": request.target_identity_id,
                "encoder": request.encoder,
                "headline": request.headline,
                "render_settings": (
                    request.render_settings.model_dump(mode="json")
                    if request.render_settings is not None else None
                ),
                "render_settings_override": (
                    request.render_settings_override.model_dump(mode="json", exclude_none=True)
                    if request.render_settings_override is not None else None
                ),
                "export_directory": request.export_directory,
            },
        ))

    @app.get(f"{router_prefix}/projects/{{project_id}}/renders/{{artifact_id}}")
    def get_render(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Render não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "render":
            raise HTTPException(status_code=404, detail="Render não encontrado")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Manifesto do render não está disponível")
        return {"artifact": artifact, "document": json.loads(path.read_text(encoding="utf-8"))}

    @app.get(f"{router_prefix}/projects/{{project_id}}/renders/{{artifact_id}}/media")
    def get_render_media(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Render não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "render":
            raise HTTPException(status_code=404, detail="Render não encontrado")

        render_root = renders_dir(settings, project_id).resolve()
        manifest_path = Path(artifact.path).resolve()
        if not manifest_path.is_relative_to(render_root) or not manifest_path.is_file():
            raise HTTPException(status_code=410, detail="Manifesto do render não está disponível")
        try:
            document = RenderDocument.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(
                status_code=410, detail="Manifesto do render não está disponível"
            ) from exc
        if document.project_id != project_id:
            raise HTTPException(status_code=404, detail="Render não encontrado")

        media_path = Path(document.output_path).resolve()
        if (
            not media_path.is_relative_to(render_root)
            or media_path.suffix.lower() != ".mp4"
            or not media_path.is_file()
        ):
            raise HTTPException(status_code=410, detail="Arquivo do render não está disponível")
        return FileResponse(
            media_path,
            media_type="video/mp4",
            filename=f"cortex-render-{artifact.id[:12]}.mp4",
        )

    @app.get(f"{router_prefix}/projects/{{project_id}}/renders/{{artifact_id}}/subtitles")
    def get_render_subtitles(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Render não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "render":
            raise HTTPException(status_code=404, detail="Render não encontrado")

        render_root = renders_dir(settings, project_id).resolve()
        manifest_path = Path(artifact.path).resolve()
        if not manifest_path.is_relative_to(render_root) or not manifest_path.is_file():
            raise HTTPException(status_code=410, detail="Manifesto do render não está disponível")
        try:
            document = RenderDocument.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(
                status_code=410, detail="Manifesto do render não está disponível"
            ) from exc
        if document.project_id != project_id:
            raise HTTPException(status_code=404, detail="Render não encontrado")
        if document.subtitles_path is None:
            raise HTTPException(status_code=404, detail="Render não possui sidecar SRT")
        subtitles_path = Path(document.subtitles_path).resolve()
        if (
            not subtitles_path.is_relative_to(render_root)
            or subtitles_path.suffix.lower() != ".srt"
            or not subtitles_path.is_file()
        ):
            raise HTTPException(status_code=410, detail="Sidecar SRT não está disponível")
        return FileResponse(
            subtitles_path,
            media_type="application/x-subrip",
            filename=f"cortex-render-{artifact.id[:12]}.srt",
        )

    @app.get(f"{router_prefix}/projects/{{project_id}}/artifacts")
    def list_stage_artifacts(project_id: str, stage: str | None = None):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        return domain.list_stage_artifacts(project_id, stage)

    @app.post(f"{router_prefix}/projects/{{project_id}}/suggest", status_code=201)
    def create_suggestion_job(project_id: str, request: SuggestionRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        try:
            artifact = domain.get_transcript_artifact(request.transcript_artifact_id)
        except TranscriptArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Transcript não encontrado") from exc
        if artifact.project_id != project_id:
            raise HTTPException(status_code=400, detail="Transcript não pertence a este projeto")
        try:
            analysis = domain.get_stage_artifact(request.analysis_artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Análise não encontrada") from exc
        if analysis.project_id != project_id or analysis.stage != "analysis":
            raise HTTPException(status_code=400, detail="Análise não pertence a este projeto")
        brief = request.model_dump(
            exclude={"transcript_artifact_id", "analysis_artifact_id", "provider"},
            exclude_none=True,
        )
        return jobs.create(JobCreate(
            type=JobType.SUGGESTION,
            project_id=project_id,
            payload={
                "transcript_artifact_id": request.transcript_artifact_id,
                "analysis_artifact_id": request.analysis_artifact_id,
                "brief": brief,
                "provider": request.provider,
            },
        ))

    @app.post(f"{router_prefix}/jobs", status_code=201)
    def create_job(request: JobCreate):
        return jobs.create(request)

    @app.get(f"{router_prefix}/jobs")
    def list_jobs(limit: int = Query(default=50, ge=1, le=200)):
        return jobs.list(limit)

    @app.get(f"{router_prefix}/jobs/{{job_id}}")
    def get_job(job_id: str):
        try:
            return jobs.get(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job não encontrado") from exc

    @app.post(f"{router_prefix}/jobs/{{job_id}}/cancel")
    def cancel_job(job_id: str):
        try:
            return jobs.cancel(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job não encontrado") from exc
        except InvalidJobTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(f"{router_prefix}/jobs/{{job_id}}/events")
    async def job_events(job_id: str):
        try:
            jobs.get(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job não encontrado") from exc

        async def stream() -> AsyncIterator[str]:
            previous = None
            while True:
                job = jobs.get(job_id)
                serialized = job.model_dump_json()
                if serialized != previous:
                    yield f"event: job\ndata: {serialized}\n\n"
                    previous = serialized
                if job.status in {
                    JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED
                }:
                    break
                await asyncio.sleep(0.75)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def run() -> None:
    import uvicorn

    config = load_config()
    uvicorn.run(create_app(config), host=config.app.host, port=config.app.port)
