"""Durable export-batch orchestration.

This module owns transitions between planning, camera planning, rendering, and
the next item.  All state changes that enqueue a successor job share the same
SQLite transaction, so a process restart cannot leave a batch half-advanced.
"""

from __future__ import annotations

from cortex.domain.models import (
    ExportBatch,
    ExportItem,
    ExportItemStatus,
    ExportStatus,
    WorkflowStatus,
)
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import Job, JobCreate, JobStatus, JobType


def export_plan_payload(batch: ExportBatch, item: ExportItem, revision, run) -> dict:
    """The one edit-plan payload for export items (first item, next items and
    retries alike), so every exported clip gets the same refine/duration rules."""
    return {
        "transcript_artifact_id": run.artifacts["transcript"],
        "analysis_artifact_id": run.artifacts["analysis"],
        "scene_index_artifact_id": revision.visual_artifacts.get("scene"),
        "start": item.clip["start_second"], "end": item.clip["end_second"],
        "profile": item.clip["pacing"],
        "maximum_seconds": run.brief.get("maximum_seconds"),
        "minimum_seconds": run.brief.get("minimum_seconds"),
        # The AI headline, not the user's edited text: refine results are cached
        # by input, and the editor preview uses the same value, so editing the
        # on-screen headline never triggers (and bills) a new refine call.
        "headline": item.clip.get("headline") or None,
        "approximate_edl": item.clip.get("approximate_edl"),
        "confirmed_voice": run.brief.get("confirmed_voice"),
        "primary_subject": run.brief.get("primary_subject"),
        "tighten": True,
        "export_batch_id": batch.id, "export_item_id": item.id,
    }


def _queue_export_plan(
    *, batch: ExportBatch, item: ExportItem, revision, run, jobs: JobStore,
    domain: DomainStore, connection,
) -> None:
    job = jobs.create(JobCreate(
        type=JobType.EDIT_PLAN,
        project_id=batch.project_id,
        payload=export_plan_payload(batch, item, revision, run),
        idempotency_key=f"export:{batch.id}:item:{item.id}:plan:{item.attempt}",
    ), connection=connection)
    domain.update_export_item(
        item.id, connection=connection, status=ExportItemStatus.PLANNING,
        active_job_id=job.id, error=None,
    )
    domain.update_export_batch(
        batch.id, connection=connection, status=ExportStatus.RUNNING,
        active_job_id=job.id, error=None,
    )


def _queue_export_render(
    *, batch: ExportBatch, item: ExportItem, run, jobs: JobStore,
    domain: DomainStore, connection,
) -> None:
    job = jobs.create(JobCreate(
        type=JobType.RENDER,
        project_id=batch.project_id,
        payload={
            "edit_plan_artifact_id": item.edit_plan_artifact_id,
            "camera_edit_plan_artifact_id": item.camera_edit_plan_artifact_id,
            "render_settings": item.render_settings,
            "confirmed_voice": run.brief.get("confirmed_voice"),
            "export_batch_id": batch.id, "export_item_id": item.id,
        },
        idempotency_key=f"export:{batch.id}:item:{item.id}:render:{item.attempt}",
    ), connection=connection)
    domain.update_export_item(
        item.id, connection=connection, status=ExportItemStatus.RENDERING,
        active_job_id=job.id,
    )
    domain.update_export_batch(batch.id, connection=connection, active_job_id=job.id)


def _queue_next_export_item(
    *, batch: ExportBatch, revision, run, jobs: JobStore, domain: DomainStore,
    connection,
) -> None:
    current = domain.get_export_batch(batch.id, connection=connection)
    next_item = next(
        (item for item in current.items if item.status == ExportItemStatus.QUEUED), None,
    )
    if next_item is not None:
        _queue_export_plan(
            batch=current, item=next_item, revision=revision, run=run,
            jobs=jobs, domain=domain, connection=connection,
        )
        return
    failures = [item for item in current.items if item.status == ExportItemStatus.FAILED]
    cancelled = [item for item in current.items if item.status == ExportItemStatus.CANCELLED]
    successes = [item for item in current.items if item.status == ExportItemStatus.SUCCEEDED]
    incomplete = [*failures, *cancelled]
    status = (
        ExportStatus.PARTIAL if incomplete and successes
        else ExportStatus.FAILED if incomplete
        else ExportStatus.SUCCEEDED
    )
    domain.update_export_batch(
        current.id, connection=connection, status=status, active_job_id=None,
        error=(f"{len(incomplete)} item(ns) não concluídos" if incomplete else None),
    )
    domain.update_workflow_run(
        run.id, connection=connection,
        status=(WorkflowStatus.COMPLETE if successes else WorkflowStatus.FAILED),
        stage="complete" if successes else "render",
        progress=100, active_job_id=None,
        message=("Exportação concluída" if not failures else
                 f"Exportação concluída com {len(failures)} falha(s)"),
    )


def advance_export_batch(job: Job, jobs: JobStore, domain: DomainStore) -> bool:
    """Advance a batch after one of its jobs reaches a terminal state."""
    batch_id = job.payload.get("export_batch_id")
    if not batch_id:
        return False
    batch = domain.get_export_batch(batch_id)
    if batch.active_job_id != job.id:
        return True
    final = jobs.get(job.id)
    if final.status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
        return True
    revision = domain.get_clip_revision(batch.revision_id)
    run = domain.get_workflow_run(batch.workflow_run_id)
    item_id = job.payload.get("export_item_id")
    with domain.transaction() as connection:
        batch = domain.get_export_batch(batch.id, connection=connection)
        if item_id is None:
            if final.status == JobStatus.SUCCEEDED and final.result is not None:
                batch = domain.update_export_batch(
                    batch.id, connection=connection,
                    reaction_candidate_artifact_id=final.result[
                        "reaction_candidate_artifact_id"
                    ],
                    active_job_id=None,
                )
            else:
                batch = domain.update_export_batch(
                    batch.id, connection=connection, active_job_id=None,
                    error="Reações indisponíveis; exportação seguirá sem elas",
                )
            _queue_next_export_item(
                batch=batch, revision=revision, run=run, jobs=jobs,
                domain=domain, connection=connection,
            )
            return True

        item = domain.get_export_item(item_id, connection=connection)
        if final.status != JobStatus.SUCCEEDED or final.result is None:
            domain.update_export_item(
                item.id, connection=connection,
                status=(ExportItemStatus.CANCELLED if final.status == JobStatus.CANCELLED
                        else ExportItemStatus.FAILED),
                active_job_id=None, error=final.error or final.message,
            )
            _queue_next_export_item(
                batch=batch, revision=revision, run=run, jobs=jobs,
                domain=domain, connection=connection,
            )
            return True

        if job.type == JobType.EDIT_PLAN:
            item = domain.update_export_item(
                item.id, connection=connection,
                edit_plan_artifact_id=final.result["edit_plan_artifact_id"],
                active_job_id=None,
            )
            framing_mode = item.render_settings.get("framing", {}).get("mode")
            if framing_mode in {"speaker_auto", "speaker_face"}:
                required = {"camera", "identity", "quality"}
                if not required <= revision.visual_artifacts.keys():
                    domain.update_export_item(
                        item.id, connection=connection, status=ExportItemStatus.FAILED,
                        error="Enquadramento automático sem artifacts visuais",
                    )
                    _queue_next_export_item(
                        batch=batch, revision=revision, run=run, jobs=jobs,
                        domain=domain, connection=connection,
                    )
                    return True
                camera_job = jobs.create(JobCreate(
                    type=JobType.CAMERA_PLANNING, project_id=batch.project_id,
                    payload={
                        "edit_plan_artifact_id": item.edit_plan_artifact_id,
                        "camera_timeline_artifact_id": revision.visual_artifacts["camera"],
                        "identity_index_artifact_id": revision.visual_artifacts["identity"],
                        "visual_quality_artifact_id": revision.visual_artifacts["quality"],
                        "reaction_candidate_artifact_id": (
                            batch.reaction_candidate_artifact_id
                        ),
                        "export_batch_id": batch.id, "export_item_id": item.id,
                    },
                    idempotency_key=(
                        f"export:{batch.id}:item:{item.id}:camera:{item.attempt}"
                    ),
                ), connection=connection)
                domain.update_export_item(
                    item.id, connection=connection,
                    status=ExportItemStatus.CAMERA_PLANNING,
                    active_job_id=camera_job.id,
                )
                domain.update_export_batch(
                    batch.id, connection=connection, active_job_id=camera_job.id,
                )
            else:
                _queue_export_render(
                    batch=batch, item=item, run=run, jobs=jobs,
                    domain=domain, connection=connection,
                )
            return True

        if job.type == JobType.CAMERA_PLANNING:
            item = domain.update_export_item(
                item.id, connection=connection,
                camera_edit_plan_artifact_id=final.result[
                    "camera_edit_plan_artifact_id"
                ],
                active_job_id=None,
            )
            _queue_export_render(
                batch=batch, item=item, run=run, jobs=jobs,
                domain=domain, connection=connection,
            )
            return True

        if job.type == JobType.RENDER:
            domain.update_export_item(
                item.id, connection=connection, status=ExportItemStatus.SUCCEEDED,
                active_job_id=None, render_artifact_id=final.result["render_artifact_id"],
            )
            _queue_next_export_item(
                batch=batch, revision=revision, run=run, jobs=jobs,
                domain=domain, connection=connection,
            )
    return True


def reconcile_export_batches(jobs: JobStore, domain: DomainStore) -> None:
    """Finish transitions interrupted after a job reached a terminal state."""
    for batch in domain.list_incomplete_export_batches():
        if not batch.active_job_id:
            continue
        try:
            job = jobs.get(batch.active_job_id)
        except Exception:
            continue
        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            advance_export_batch(job, jobs, domain)
