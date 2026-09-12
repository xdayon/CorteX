from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cortex.analyze.camera_schemas import CameraTimelineDocument
from cortex.analyze.face_schemas import FaceIndexDocument
from cortex.analyze.identity_schemas import IdentityIndexDocument
from cortex.analyze.reaction_candidate_schemas import ReactionCandidateIndexDocument
from cortex.analyze.scene_schemas import SceneIndexDocument
from cortex.analyze.speaker_schemas import SpeakerTimelineDocument
from cortex.analyze.visual_quality_schemas import VisualQualityDocument
from cortex.analyze.scope import SourceRange
from cortex.config import CortexConfig, load_config
from cortex.domain.models import RenderPreset, SourceAsset, SourceKind, WorkflowRun, WorkflowStatus
from cortex.domain.store import (
    DomainStore,
    ProjectNotFoundError,
    RenderPresetNameConflictError,
    RenderPresetNotFoundError,
    SourceAssetNotFoundError,
    StageArtifactNotFoundError,
    TranscriptArtifactNotFoundError,
    WorkflowRunNotFoundError,
)
from cortex.edit.camera_plan_schemas import CameraEditPlanDocument
from cortex.edit.schemas import EditPlanDocument
from cortex.hardware import collect_hardware_snapshot
from cortex.ingest.ffprobe import FFprobeError, duration_seconds, probe_media
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


class EpisodeVoiceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    speaker: str


class EpisodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2048)
    participant_count: int | None = Field(default=None, ge=1, le=20)


class EpisodePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archived: bool = Field(strict=True)


class WorkflowRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: str
    primary_subject: str = Field(default="Dayon", min_length=1, max_length=100)
    new_clips_only: bool = False
    count: int = Field(default=10, ge=1, le=25)
    minimum_seconds: float = Field(default=40, ge=15, le=180)
    maximum_seconds: float = Field(default=120, ge=15, le=180)
    topic: str | None = Field(default=None, max_length=500)
    instructions: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def duration_order(self) -> "WorkflowRunCreate":
        if self.maximum_seconds < self.minimum_seconds:
            raise ValueError("maximum_seconds deve ser maior ou igual a minimum_seconds")
        return self


class WorkflowIdentitySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interviewer_identity_id: str = Field(min_length=1)
    scene_index_artifact_id: str
    face_index_artifact_id: str
    speaker_timeline_artifact_id: str
    camera_timeline_artifact_id: str
    visual_quality_artifact_id: str
    identity_index_artifact_id: str


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
    maximum_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    # None = use config.edit.jl_cut_enabled_default (currently False; the
    # renderer honors J/L offsets since Gate 4 Session H, but the feature
    # stays opt-in — pass True explicitly to request it for this plan).
    jl_cut: bool | None = None
    max_jl_offset_seconds: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def clip_order(self) -> "EditPlanRequest":
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class SceneIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ranges: list[SourceRange] | None = Field(default=None, min_length=1, max_length=100)
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
        if (self.render_settings is not None
                and self.render_settings.framing.mode == "speaker_auto"
                and not self.camera_edit_plan_artifact_id):
            raise ValueError("speaker_auto exige camera_edit_plan_artifact_id")
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
    from cortex.library import EpisodeLibrary
    library = EpisodeLibrary(settings, domain)
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

    @app.get(f"{router_prefix}/episodes")
    def list_episodes(include_archived: bool = False):
        return [library.detail(entry) for entry in library.entries(include_archived=include_archived)]

    @app.post(f"{router_prefix}/episodes", status_code=201)
    def register_episode(request: EpisodeCreate):
        try:
            return library.detail(library.register(request.url, request.participant_count))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(f"{router_prefix}/episodes/{{episode_id}}/metadata")
    def refresh_episode_metadata(episode_id: str):
        try:
            episode = library.get(episode_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not episode.url:
            raise HTTPException(status_code=400, detail="Episódio local não possui link do YouTube")
        return library.detail(library.refresh_metadata(episode_id))

    @app.patch(f"{router_prefix}/episodes/{{episode_id}}")
    def update_episode(episode_id: str, request: EpisodePatch):
        try:
            return library.detail(library.set_archived(episode_id, request.archived))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(f"{router_prefix}/diarization-status")
    def diarization_status():
        from cortex.diarize.service import readiness
        return readiness()

    @app.post(f"{router_prefix}/episodes/{{episode_id}}/diarization")
    def start_episode_diarization(episode_id: str):
        try:
            entry = library.get(episode_id)
            detail = library.detail(entry)
            source = detail["source"]
            if not source:
                raise ValueError("Baixe o episódio primeiro")
            from cortex.diarize.service import readiness
            state = readiness()
            if not state["ready"]:
                raise ValueError(" ".join(state.get("missing", [])) or "Diarização indisponível; confira a configuração de vozes.")
            active = next((j for j in detail["jobs"] if j["type"] == "diarization" and j["status"] in {"running", "queued"}), None)
            if active:
                return jobs.get(active["id"])
            return jobs.create(JobCreate(type=JobType.DIARIZATION, project_id=source["project_id"], payload={"source_asset_id":source["id"], "speakers":entry.participant_count}))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def episode_diarization(episode_id, artifact_id):
        from cortex.diarize.service import DiarizationDocument
        entry = library.get(episode_id)
        artifact = domain.get_stage_artifact(artifact_id)
        if artifact.stage != "diarization" or artifact.project_id not in entry.project_ids:
            raise ValueError("Vozes não pertencem ao episódio")
        return entry, artifact, DiarizationDocument.model_validate_json(Path(artifact.path).read_text())

    @app.get(f"{router_prefix}/episodes/{{episode_id}}/diarization/{{artifact_id}}")
    def get_episode_diarization(episode_id: str, artifact_id: str):
        try:
            return episode_diarization(episode_id, artifact_id)[2]
        except (ValueError, LookupError, OSError) as exc:
            raise HTTPException(status_code=404, detail="Diarização indisponível") from exc

    @app.put(f"{router_prefix}/episodes/{{episode_id}}/voice")
    def select_episode_voice(episode_id: str, request: EpisodeVoiceSelection):
        try:
            entry, artifact, document = episode_diarization(episode_id, request.artifact_id)
            if request.speaker not in {turn.speaker for turn in document.turns}:
                raise ValueError("Voz não existe nesta análise")
            entry.subject_reference = {"diarization_artifact_id":artifact.id, "speaker":request.speaker}
            library.save(entry)
            return library.detail(entry)
        except (ValueError, LookupError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(f"{router_prefix}/episodes/{{episode_id}}")
    def get_episode(episode_id: str):
        try:
            return library.detail(library.get(episode_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(f"{router_prefix}/episodes/{{episode_id}}/download")
    def download_episode(episode_id: str):
        try:
            return library.download(library.get(episode_id), jobs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(f"{router_prefix}/caption-fonts/{{family}}/{{weight}}")
    def caption_font(family: str, weight: int):
        if family not in {"Montserrat", "Lato", "DejaVu Sans"} or weight not in {400, 800, 900}:
            raise HTTPException(status_code=404, detail="Fonte indisponível")
        try:
            result = subprocess.run(
                ["fc-match", "-f", "%{file}", f"{family}:weight={ {400: 'regular', 800: 'extrabold', 900: 'black'}[weight]}"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            path = Path(result.stdout.strip())
            if not path.is_file():
                raise ValueError("Fonte não encontrada")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=503, detail="Fonte local indisponível") from exc
        return FileResponse(path, media_type="font/ttf")

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

    @app.get(f"{router_prefix}/projects/{{project_id}}/sources")
    def list_sources(project_id: str):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        return domain.list_source_assets(project_id)

    def _preview_source(project_id: str, source_asset_id: str):
        try:
            asset = domain.get_source_asset(source_asset_id)
        except SourceAssetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Fonte não encontrada") from exc
        if asset.project_id != project_id:
            raise HTTPException(status_code=404, detail="Fonte não encontrada")
        project_source_root = source_dir(settings, project_id).resolve()
        source_path = (settings.paths.data_dir / asset.stored_path).resolve()
        if not source_path.is_relative_to(project_source_root) or not source_path.is_file():
            raise HTTPException(status_code=410, detail="Arquivo da fonte não está disponível")
        return asset, source_path

    def _extract_jpeg(source_path: Path, output_path: Path, time_seconds: float, vf: str) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_name(f".{output_path.stem}.tmp.jpg")
        command = [
            str(settings.render.ffmpeg), "-y", "-v", "error", "-ss", f"{time_seconds:.6f}",
            "-i", str(source_path), "-frames:v", "1", "-vf", vf,
            "-q:v", "3", str(temporary_path),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(status_code=500, detail=f"Falha ao gerar preview: {exc}") from exc
        if completed.returncode != 0 or not temporary_path.is_file():
            temporary_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=500,
                detail=f"Falha ao gerar preview: {(completed.stderr or '')[-1000:]}",
            )
        temporary_path.replace(output_path)

    @app.get(f"{router_prefix}/projects/{{project_id}}/sources/{{source_asset_id}}/preview")
    def get_source_preview(
        project_id: str,
        source_asset_id: str,
        time_seconds: float = Query(default=0.0, ge=0.0),
    ):
        asset, source_path = _preview_source(project_id, source_asset_id)
        duration = duration_seconds(asset.probe)
        effective_time = min(time_seconds, max(0.0, duration - 0.05)) if duration > 0 else time_seconds
        cache_key = hashlib.sha256(
            f"source-preview-v1:{asset.sha256}:{effective_time:.3f}".encode()
        ).hexdigest()[:20]
        preview_path = source_dir(settings, project_id) / "previews" / f"frame-{cache_key}.jpg"
        if not preview_path.is_file():
            _extract_jpeg(
                source_path, preview_path, effective_time,
                "scale=720:-2:force_original_aspect_ratio=decrease",
            )
        return FileResponse(
            preview_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get(f"{router_prefix}/projects/{{project_id}}/sources/{{source_asset_id}}/media")
    def get_source_media(project_id: str, source_asset_id: str):
        _asset, source_path = _preview_source(project_id, source_asset_id)
        return FileResponse(source_path, filename=source_path.name)

    @app.post(f"{router_prefix}/projects/{{project_id}}/runs", status_code=201)
    def create_workflow_run(project_id: str, request: WorkflowRunCreate):
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

        brief = request.model_dump(exclude={"source_asset_id"}, exclude_none=True)
        if asset.source_url:
            from cortex.library import youtube_id
            try:
                entry = library.get(youtube_id(asset.source_url))
                reference = entry.subject_reference
                if reference.get("diarization_artifact_id"):
                    _, voice_artifact, voice_doc = episode_diarization(entry.id, reference["diarization_artifact_id"])
                    if voice_artifact.metadata.get("source_sha256") == asset.sha256:
                        brief["confirmed_voice"] = {**reference, "turns":[t.model_dump() for t in voice_doc.turns]}
            except ValueError:
                pass
        if request.new_clips_only:
            from cortex.library import youtube_id
            project_ids = [project_id]
            if asset.source_url:
                try:
                    project_ids = library.get(youtube_id(asset.source_url)).project_ids
                except ValueError:
                    pass
            excluded = []
            for previous_project in project_ids:
                for artifact in domain.list_stage_artifacts(previous_project, stage="suggestion"):
                    if Path(artifact.path).is_file():
                        document = json.loads(Path(artifact.path).read_text())
                        excluded.extend({"start": c["start_second"], "end": c["end_second"]}
                                        for c in document.get("selection", {}).get("clips", []))
            brief["exclude_ranges"] = sorted(excluded, key=lambda r: (r["start"], r["end"]))
        run = domain.create_workflow_run(WorkflowRun(
            project_id=project_id,
            source_asset_id=asset.id,
            status=WorkflowStatus.RUNNING,
            stage="transcribe",
            progress=2,
            message="Transcrição na fila",
            brief=brief,
        ))
        job = jobs.create(JobCreate(
            type=JobType.TRANSCRIPTION,
            project_id=project_id,
            payload={"source_asset_id": asset.id, "workflow_run_id": run.id, "overrides": {}},
        ))
        return domain.update_workflow_run(run.id, active_job_id=job.id)

    @app.get(f"{router_prefix}/projects/{{project_id}}/runs")
    def list_workflow_runs(project_id: str):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        return domain.list_workflow_runs(project_id)

    @app.get(f"{router_prefix}/projects/{{project_id}}/runs/{{run_id}}")
    def get_workflow_run(project_id: str, run_id: str):
        try:
            run = domain.get_workflow_run(run_id)
        except WorkflowRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Processamento não encontrado") from exc
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Processamento não encontrado")
        return run

    @app.put(f"{router_prefix}/projects/{{project_id}}/runs/{{run_id}}/identity")
    def select_workflow_identity(
        project_id: str, run_id: str, request: WorkflowIdentitySelection,
    ):
        try:
            run = domain.get_workflow_run(run_id)
        except WorkflowRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Processamento não encontrado") from exc
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Processamento não encontrado")
        artifact_specs = {
            "scene_index": (request.scene_index_artifact_id, "scene_index"),
            "face_index": (request.face_index_artifact_id, "face_index"),
            "speaker_timeline": (request.speaker_timeline_artifact_id, "speaker_timeline"),
            "camera_timeline": (request.camera_timeline_artifact_id, "camera_timeline"),
            "visual_quality": (request.visual_quality_artifact_id, "visual_quality_index"),
            "identity_index": (request.identity_index_artifact_id, "identity_index"),
        }
        artifacts = dict(run.artifacts)
        resolved = {}
        for key, (artifact_id, expected_stage) in artifact_specs.items():
            try:
                artifact = domain.get_stage_artifact(artifact_id)
            except StageArtifactNotFoundError as exc:
                raise HTTPException(status_code=404, detail=f"Artifact {key} não encontrado") from exc
            if artifact.project_id != project_id or artifact.stage != expected_stage:
                raise HTTPException(status_code=400, detail=f"Artifact {key} não pertence ao processamento")
            resolved[key] = artifact
            artifacts[key] = artifact.id
        try:
            source = domain.get_source_asset(run.source_asset_id)
            analysis = domain.get_stage_artifact(run.artifacts.get("analysis", ""))
        except (SourceAssetNotFoundError, StageArtifactNotFoundError) as exc:
            raise HTTPException(status_code=400, detail="Fonte ou análise do processamento ausente") from exc
        if source.project_id != project_id or analysis.project_id != project_id or analysis.stage != "analysis":
            raise HTTPException(status_code=400, detail="Fonte ou análise não pertence ao processamento")
        resolved["analysis"] = analysis
        document_specs = {
            "scene_index": (SceneIndexDocument, ()),
            "face_index": (FaceIndexDocument, ("scene_index",)),
            "speaker_timeline": (SpeakerTimelineDocument, ("scene_index", "face_index", "analysis")),
            "camera_timeline": (CameraTimelineDocument, ("scene_index", "face_index", "speaker_timeline")),
            "visual_quality": (VisualQualityDocument, ("scene_index", "face_index")),
            "identity_index": (IdentityIndexDocument, ("face_index", "camera_timeline")),
        }
        documents = {}
        for key, (schema, dependencies) in document_specs.items():
            artifact = resolved[key]
            try:
                document = schema.model_validate_json(Path(artifact.path).read_text(encoding="utf-8"))
            except OSError as exc:
                raise HTTPException(status_code=410, detail=f"Artifact {key} não está disponível") from exc
            except (ValidationError, UnicodeError) as exc:
                raise HTTPException(status_code=400, detail=f"Artifact {key} inválido") from exc
            if (
                document.project_id != project_id
                or document.source_asset_id != source.id
                or document.source_sha256 != source.sha256
                or document.input_hash != artifact.input_hash
            ):
                raise HTTPException(status_code=400, detail=f"Artifact {key} não corresponde à fonte do processamento")
            for dependency in dependencies:
                upstream = resolved[dependency]
                if getattr(document, f"{dependency}_artifact_id") != upstream.id:
                    raise HTTPException(status_code=400, detail=f"Dependência {dependency} divergente em {key}")
                hash_field = f"{dependency}_input_hash"
                if hasattr(document, hash_field) and getattr(document, hash_field) != upstream.input_hash:
                    raise HTTPException(status_code=400, detail=f"Hash de {dependency} divergente em {key}")
            documents[key] = document
        identity_document = documents["identity_index"]
        if not any(
            item.identity_id == request.interviewer_identity_id and item.status == "confirmed"
            for item in identity_document.identities
        ):
            raise HTTPException(status_code=400, detail="Escolha uma identidade confirmada")
        return domain.update_workflow_run(
            run.id,
            interviewer_identity_id=request.interviewer_identity_id,
            artifacts=artifacts,
            message="Entrevistador confirmado",
        )

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
            payload={"source_asset_id": asset.id, "scene_threshold": request.scene_threshold,
                     "source_ranges": [r.model_dump() for r in request.source_ranges] if request.source_ranges else None},
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

    @app.get(
        f"{router_prefix}/projects/{{project_id}}/identities/{{artifact_id}}/"
        "{identity_id}/preview"
    )
    def get_identity_preview(project_id: str, artifact_id: str, identity_id: str):
        try:
            identity_artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Índice de identidade não encontrado") from exc
        if identity_artifact.project_id != project_id or identity_artifact.stage != "identity_index":
            raise HTTPException(status_code=404, detail="Índice de identidade não encontrado")
        identity_path = Path(identity_artifact.path)
        if not identity_path.is_file():
            raise HTTPException(status_code=410, detail="Arquivo do índice de identidade indisponível")
        try:
            identity_document = IdentityIndexDocument.model_validate_json(
                identity_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(status_code=410, detail="Índice de identidade inválido") from exc
        identity = next(
            (
                item for item in identity_document.identities
                if item.identity_id == identity_id and item.status == "confirmed"
            ),
            None,
        )
        if identity is None:
            raise HTTPException(status_code=404, detail="Identidade confirmada não encontrada")
        try:
            face_artifact = domain.get_stage_artifact(identity_document.face_index_artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=410, detail="Índice de faces não encontrado") from exc
        if face_artifact.project_id != project_id or face_artifact.stage != "face_index":
            raise HTTPException(status_code=410, detail="Índice de faces não corresponde à identidade")
        face_path = Path(face_artifact.path)
        if not face_path.is_file():
            raise HTTPException(status_code=410, detail="Arquivo do índice de faces indisponível")
        try:
            face_document = FaceIndexDocument.model_validate_json(
                face_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(status_code=410, detail="Índice de faces inválido") from exc
        if (
            identity_document.source_asset_id != face_document.source_asset_id
            or identity_document.face_index_input_hash != face_artifact.input_hash
        ):
            raise HTTPException(status_code=410, detail="Cadeia de identidade e faces inválida")

        observations = {
            item.observation_id: item for item in identity_document.observations
            if item.observation_id in identity.observation_ids
        }
        candidates = []
        for observation_id in identity.observation_ids:
            observation = observations.get(observation_id)
            if observation is None:
                continue
            parts = observation_id.split("-")
            if len(parts) != 3 or parts[0] != "face":
                continue
            try:
                frame_index, face_index = int(parts[1]), int(parts[2])
                frame = face_document.frames[frame_index]
                face = frame.faces[face_index]
            except (ValueError, IndexError):
                continue
            # Identity observations are created from these exact indexes. Track
            # IDs describe screen position and are intentionally not unique per
            # frame, so using them here could return another person's face.
            quality = face.width * face.height * face.score
            candidates.append((quality, observation.time_us / 1_000_000, face))
        if not candidates:
            raise HTTPException(status_code=409, detail="A identidade não possui amostra visual utilizável")

        asset, source_path = _preview_source(project_id, identity_document.source_asset_id)
        video_stream = next(
            (item for item in asset.probe.get("streams", []) if item.get("codec_type") == "video"),
            None,
        )
        if video_stream is None:
            raise HTTPException(status_code=409, detail="Fonte sem dimensões para preview de rosto")
        source_width = int(video_stream["width"])
        source_height = int(video_stream["height"])
        _, time_seconds, face = max(candidates, key=lambda item: item[0])
        center_x = (face.x + face.width / 2) * source_width
        center_y = (face.y + face.height / 2) * source_height
        crop_size = max(64, round(max(face.width * source_width, face.height * source_height) * 1.8))
        crop_size = min(crop_size, source_width, source_height)
        crop_x = max(0, min(source_width - crop_size, round(center_x - crop_size / 2)))
        crop_y = max(0, min(source_height - crop_size, round(center_y - crop_size / 2)))
        cache_key = hashlib.sha256(
            f"identity-preview-v2:{identity_artifact.input_hash}:{identity_id}".encode()
        ).hexdigest()[:20]
        preview_path = identity_path.parent / "previews" / f"face-{cache_key}.jpg"
        if not preview_path.is_file():
            _extract_jpeg(
                source_path,
                preview_path,
                time_seconds,
                f"crop={crop_size}:{crop_size}:{crop_x}:{crop_y},scale=240:240",
            )
        return FileResponse(
            preview_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

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
                "maximum_seconds": request.maximum_seconds,
                "jl_cut": request.jl_cut,
                "max_jl_offset_seconds": request.max_jl_offset_seconds,
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

    @app.get(f"{router_prefix}/projects/{{project_id}}/renders/{{artifact_id}}/report")
    def get_render_report(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Render não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "render":
            raise HTTPException(status_code=404, detail="Render não encontrado")
        render_root = renders_dir(settings, project_id).resolve()
        manifest_path = Path(artifact.path).resolve()
        if not manifest_path.is_relative_to(render_root) or not manifest_path.is_file():
            raise HTTPException(status_code=410, detail="Relatório do render não está disponível")
        try:
            document = RenderDocument.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(
                status_code=410, detail="Relatório do render não está disponível"
            ) from exc
        if document.project_id != project_id or document.publication is None:
            raise HTTPException(status_code=410, detail="Relatório do render não está disponível")
        return FileResponse(
            manifest_path,
            media_type="application/json",
            filename=f"cortex-quality-report-{artifact.id[:12]}.json",
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
            exclude={"transcript_artifact_id", "analysis_artifact_id"},
            exclude_none=True,
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

    @app.get(f"{router_prefix}/projects/{{project_id}}/suggestions/{{artifact_id}}")
    def get_suggestion(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Sugestão não encontrada") from exc
        if artifact.project_id != project_id or artifact.stage != "suggestion":
            raise HTTPException(status_code=404, detail="Sugestão não encontrada")
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Arquivo da sugestão não está disponível")
        return {"artifact": artifact, "document": json.loads(path.read_text(encoding="utf-8"))}

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

    if settings.app.studio_dir is not None:
        from cortex.studio import mount_studio

        mount_studio(app, settings.app.studio_dir)
    return app


def run() -> None:
    import uvicorn

    config = load_config()
    uvicorn.run(create_app(config), host=config.app.host, port=config.app.port)
