"""HTTP contracts for persisted review revisions and durable export batches."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from cortex.domain.models import (
    ClipRevision,
    ExportBatch,
    ExportItem,
    ExportItemStatus,
    ExportStatus,
    WorkflowStatus,
)
from cortex.domain.store import (
    ClipRevisionNotFoundError,
    DomainStore,
    ExportBatchNotFoundError,
    ExportItemNotFoundError,
    StageArtifactNotFoundError,
    WorkflowRunNotFoundError,
)
from cortex.export_batches import advance_export_batch
from cortex.jobs import InvalidJobTransitionError, JobNotFoundError, JobStore
from cortex.render.schemas import RenderSettings
from cortex.schemas import JobCreate, JobType


class RevisionClip(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=300)
    start_second: float = Field(ge=0, allow_inf_nan=False)
    end_second: float = Field(gt=0, allow_inf_nan=False)
    pacing: str = Field(default="balanced", pattern="^(dynamic|balanced|contemplative)$")
    headline: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def clip_order(self) -> "RevisionClip":
        if self.end_second <= self.start_second:
            raise ValueError("end_second deve ser maior que start_second")
        return self


class ClipRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_artifact_id: str
    selected_clips: list[RevisionClip] = Field(min_length=1, max_length=25)
    render_settings: RenderSettings
    caption_edits: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    headlines: dict[str, str] = Field(default_factory=dict)
    visual_artifacts: dict[str, str] = Field(default_factory=dict)
    interviewer_identity_id: str | None = None
    reuse_reactions: bool = False


class ExportBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str
    idempotency_key: str = Field(min_length=1, max_length=240)


def _run_and_revision(domain: DomainStore, project_id: str, run_id: str, revision_id: str):
    try:
        run = domain.get_workflow_run(run_id)
        revision = domain.get_clip_revision(revision_id)
    except (WorkflowRunNotFoundError, ClipRevisionNotFoundError) as exc:
        raise HTTPException(
            status_code=404, detail="Processamento ou revisão não encontrado",
        ) from exc
    if run.project_id != project_id or revision.workflow_run_id != run.id:
        raise HTTPException(status_code=404, detail="Processamento ou revisão não encontrado")
    return run, revision


def _plan_payload(batch: ExportBatch, item: ExportItem, revision, run) -> dict[str, Any]:
    return {
        "transcript_artifact_id": run.artifacts["transcript"],
        "analysis_artifact_id": run.artifacts["analysis"],
        "scene_index_artifact_id": revision.visual_artifacts.get("scene"),
        "start": item.clip["start_second"], "end": item.clip["end_second"],
        "profile": item.clip["pacing"],
        "maximum_seconds": run.brief.get("maximum_seconds"),
        "approximate_edl": item.clip.get("approximate_edl"),
        "confirmed_voice": run.brief.get("confirmed_voice"),
        "export_batch_id": batch.id, "export_item_id": item.id,
    }


def create_review_router(prefix: str, domain: DomainStore, jobs: JobStore) -> APIRouter:
    router = APIRouter(prefix=prefix)

    @router.post("/projects/{project_id}/runs/{run_id}/revisions", status_code=201)
    def create_clip_revision(project_id: str, run_id: str, request: ClipRevisionCreate):
        try:
            run = domain.get_workflow_run(run_id)
            selection = domain.get_stage_artifact(request.selection_artifact_id)
        except WorkflowRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Processamento não encontrado") from exc
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Seleção não encontrada") from exc
        if run.project_id != project_id or selection.project_id != project_id:
            raise HTTPException(status_code=404, detail="Processamento não encontrado")
        if selection.stage != "suggestion" or run.artifacts.get("suggestion") != selection.id:
            raise HTTPException(status_code=400, detail="Seleção não pertence ao processamento")
        keys = [clip.key for clip in request.selected_clips]
        if len(keys) != len(set(keys)):
            raise HTTPException(status_code=422, detail="Cortes selecionados possuem chaves duplicadas")
        return domain.create_clip_revision(ClipRevision(
            project_id=project_id, workflow_run_id=run_id,
            selection_artifact_id=selection.id,
            selected_clips=[clip.model_dump(mode="json") for clip in request.selected_clips],
            render_settings=request.render_settings.model_dump(mode="json"),
            caption_edits=request.caption_edits, headlines=request.headlines,
            visual_artifacts=request.visual_artifacts,
            interviewer_identity_id=request.interviewer_identity_id,
            reuse_reactions=request.reuse_reactions,
        ))

    @router.get("/projects/{project_id}/runs/{run_id}/revision")
    def get_latest_clip_revision(project_id: str, run_id: str):
        try:
            run = domain.get_workflow_run(run_id)
        except WorkflowRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Processamento não encontrado") from exc
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Processamento não encontrado")
        revision = domain.latest_clip_revision(run_id)
        if revision is None:
            raise HTTPException(status_code=404, detail="Revisão ainda não foi salva")
        return revision

    @router.post("/projects/{project_id}/runs/{run_id}/export-batches", status_code=201)
    def create_export_batch(project_id: str, run_id: str, request: ExportBatchCreate):
        run, revision = _run_and_revision(domain, project_id, run_id, request.revision_id)
        missing = {"transcript", "analysis"} - run.artifacts.keys()
        if missing:
            raise HTTPException(
                status_code=409,
                detail=f"Processamento ainda não possui artifacts: {', '.join(sorted(missing))}",
            )
        items = []
        for position, clip in enumerate(revision.selected_clips):
            key = str(clip["key"])
            effective = json.loads(json.dumps(revision.render_settings))
            effective.setdefault("captions", {})["corrections"] = revision.caption_edits.get(key, [])
            effective.setdefault("headline", {})["text"] = (
                revision.headlines.get(key, "").strip() or clip.get("headline") or clip["title"]
            )
            items.append(ExportItem(
                batch_id="pending", project_id=project_id, position=position,
                clip_key=key, title=clip["title"], clip=clip, render_settings=effective,
            ))
        batch = ExportBatch(
            project_id=project_id, workflow_run_id=run.id, revision_id=revision.id,
            idempotency_key=request.idempotency_key, status=ExportStatus.QUEUED,
        )
        items = [item.model_copy(update={"batch_id": batch.id}) for item in items]
        batch = batch.model_copy(update={"items": items})
        with domain.transaction() as connection:
            existing = domain.create_export_batch(batch, connection=connection)
            if existing.id != batch.id:
                return existing
            if revision.reuse_reactions:
                required = {"speaker", "camera", "identity", "quality"}
                if (not revision.interviewer_identity_id
                        or not required <= revision.visual_artifacts.keys()):
                    raise HTTPException(
                        status_code=409,
                        detail="Reações exigem análise visual e entrevistador confirmado",
                    )
                job = jobs.create(JobCreate(
                    type=JobType.REACTION_CANDIDATE_ANALYSIS, project_id=project_id,
                    payload={
                        "speaker_timeline_artifact_id": revision.visual_artifacts["speaker"],
                        "camera_timeline_artifact_id": revision.visual_artifacts["camera"],
                        "identity_index_artifact_id": revision.visual_artifacts["identity"],
                        "visual_quality_artifact_id": revision.visual_artifacts["quality"],
                        "interviewer_identity_id": revision.interviewer_identity_id,
                        "min_duration_seconds": 0.7, "export_batch_id": batch.id,
                    }, idempotency_key=f"export:{batch.id}:reactions",
                ), connection=connection)
                batch = domain.update_export_batch(
                    batch.id, connection=connection, status=ExportStatus.RUNNING,
                    active_job_id=job.id,
                )
            else:
                first = items[0]
                job = jobs.create(JobCreate(
                    type=JobType.EDIT_PLAN, project_id=project_id,
                    payload=_plan_payload(batch, first, revision, run),
                    idempotency_key=f"export:{batch.id}:item:{first.id}:plan:1",
                ), connection=connection)
                domain.update_export_item(
                    first.id, connection=connection, status=ExportItemStatus.PLANNING,
                    active_job_id=job.id,
                )
                batch = domain.update_export_batch(
                    batch.id, connection=connection, status=ExportStatus.RUNNING,
                    active_job_id=job.id,
                )
            domain.update_workflow_run(
                run.id, connection=connection, status=WorkflowStatus.RENDERING,
                stage="render", progress=0, active_job_id=job.id,
                message=f"Exportando lote com {len(items)} corte(s)", error=None,
            )
        return domain.get_export_batch(batch.id)

    @router.get("/projects/{project_id}/runs/{run_id}/export-batches")
    def list_export_batches(project_id: str, run_id: str):
        try:
            run = domain.get_workflow_run(run_id)
        except WorkflowRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Processamento não encontrado") from exc
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Processamento não encontrado")
        return domain.list_export_batches(run_id)

    def owned_batch(project_id: str, batch_id: str) -> ExportBatch:
        try:
            batch = domain.get_export_batch(batch_id)
        except ExportBatchNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Lote não encontrado") from exc
        if batch.project_id != project_id:
            raise HTTPException(status_code=404, detail="Lote não encontrado")
        return batch

    @router.get("/projects/{project_id}/export-batches/{batch_id}")
    def get_export_batch(project_id: str, batch_id: str):
        return owned_batch(project_id, batch_id)

    @router.post("/projects/{project_id}/export-batches/{batch_id}/cancel")
    def cancel_export_batch(project_id: str, batch_id: str):
        batch = owned_batch(project_id, batch_id)
        if batch.status not in {ExportStatus.QUEUED, ExportStatus.RUNNING}:
            return batch
        with domain.transaction() as connection:
            if batch.active_job_id:
                try:
                    jobs.cancel(batch.active_job_id, connection=connection)
                except (InvalidJobTransitionError, JobNotFoundError):
                    pass
            for item in batch.items:
                if item.status not in {
                    ExportItemStatus.SUCCEEDED, ExportItemStatus.FAILED,
                    ExportItemStatus.CANCELLED,
                }:
                    domain.update_export_item(
                        item.id, connection=connection,
                        status=ExportItemStatus.CANCELLED, active_job_id=None,
                    )
            domain.update_export_batch(
                batch.id, connection=connection, status=ExportStatus.CANCELLED,
                active_job_id=None, error="Cancelado pelo usuário",
            )
            domain.update_workflow_run(
                batch.workflow_run_id, connection=connection,
                status=WorkflowStatus.READY_FOR_REVIEW, stage="review",
                active_job_id=None, message="Exportação cancelada; revisão preservada",
            )
        return domain.get_export_batch(batch.id)

    @router.post("/projects/{project_id}/export-batches/{batch_id}/items/{item_id}/cancel")
    def cancel_export_item(project_id: str, batch_id: str, item_id: str):
        batch = owned_batch(project_id, batch_id)
        try:
            item = domain.get_export_item(item_id)
        except ExportItemNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Item não encontrado") from exc
        if item.batch_id != batch.id:
            raise HTTPException(status_code=404, detail="Item não encontrado")
        if item.active_job_id:
            try:
                final = jobs.cancel(item.active_job_id)
            except InvalidJobTransitionError:
                final = jobs.get(item.active_job_id)
            advance_export_batch(final, jobs, domain)
        elif item.status == ExportItemStatus.QUEUED:
            with domain.transaction() as connection:
                domain.update_export_item(
                    item.id, connection=connection, status=ExportItemStatus.CANCELLED,
                )
        return domain.get_export_batch(batch.id)

    @router.post("/projects/{project_id}/export-batches/{batch_id}/retry")
    def retry_export_failures(project_id: str, batch_id: str):
        batch = owned_batch(project_id, batch_id)
        try:
            revision = domain.get_clip_revision(batch.revision_id)
            run = domain.get_workflow_run(batch.workflow_run_id)
        except (ClipRevisionNotFoundError, WorkflowRunNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="Lote não encontrado") from exc
        failed = [item for item in batch.items if item.status == ExportItemStatus.FAILED]
        if not failed:
            raise HTTPException(status_code=409, detail="Lote não possui falhas para repetir")
        with domain.transaction() as connection:
            for item in failed:
                domain.update_export_item(
                    item.id, connection=connection, status=ExportItemStatus.QUEUED,
                    active_job_id=None, edit_plan_artifact_id=None,
                    camera_edit_plan_artifact_id=None, render_artifact_id=None,
                    attempt=item.attempt + 1, error=None,
                )
            first = domain.get_export_item(failed[0].id, connection=connection)
            job = jobs.create(JobCreate(
                type=JobType.EDIT_PLAN, project_id=project_id,
                payload=_plan_payload(batch, first, revision, run),
                idempotency_key=f"export:{batch.id}:item:{first.id}:plan:{first.attempt}",
            ), connection=connection)
            domain.update_export_item(
                first.id, connection=connection, status=ExportItemStatus.PLANNING,
                active_job_id=job.id,
            )
            domain.update_export_batch(
                batch.id, connection=connection, status=ExportStatus.RUNNING,
                active_job_id=job.id, error=None,
            )
            domain.update_workflow_run(
                run.id, connection=connection, status=WorkflowStatus.RENDERING,
                stage="render", active_job_id=job.id, progress=0,
                message=f"Repetindo {len(failed)} falha(s)", error=None,
            )
        return domain.get_export_batch(batch.id)

    return router
