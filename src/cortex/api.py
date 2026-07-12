from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind
from cortex.domain.store import (
    DomainStore,
    ProjectNotFoundError,
    SourceAssetNotFoundError,
    StageArtifactNotFoundError,
    TranscriptArtifactNotFoundError,
)
from cortex.hardware import collect_hardware_snapshot
from cortex.ingest.ffprobe import FFprobeError, probe_media
from cortex.ingest.upload import UploadValidationError, store_upload_stream
from cortex.jobs import InvalidJobTransitionError, JobNotFoundError, JobStore
from cortex.paths import renders_dir, source_dir
from cortex.render.schemas import RenderDocument, RenderSettings, RenderSettingsPatch
from cortex.schemas import JobCreate, JobStatus, JobType


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)


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


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edit_plan_artifact_id: str
    encoder: str | None = None
    headline: str | None = Field(default=None, max_length=120)
    render_settings: RenderSettings | None = None
    render_settings_override: RenderSettingsPatch | None = None

    @model_validator(mode="after")
    def reject_mixed_settings(self) -> RenderRequest:
        if self.render_settings is not None and (
            self.encoder is not None or self.headline is not None or self.render_settings_override is not None
        ):
            raise ValueError("render_settings não pode ser combinado com encoder/headline legados")
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
        return jobs.create(JobCreate(
            type=JobType.RENDER,
            project_id=project_id,
            payload={
                "edit_plan_artifact_id": edit_plan.id,
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
            exclude={"transcript_artifact_id", "analysis_artifact_id"}, exclude_none=True
        )
        return jobs.create(JobCreate(
            type=JobType.SUGGESTION,
            project_id=project_id,
            payload={
                "transcript_artifact_id": request.transcript_artifact_id,
                "analysis_artifact_id": request.analysis_artifact_id,
                "brief": brief,
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
