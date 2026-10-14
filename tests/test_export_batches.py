from __future__ import annotations

from pathlib import Path

from asgi_client import ASGITestClient as TestClient

from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact, WorkflowRun
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import JobStatus, JobType, JobUpdate, PipelineStage
from cortex.worker import process_next, reconcile_export_batches

from conftest import FakeTranscriptionEngine


def _config(tmp_path: Path) -> CortexConfig:
    base = load_config()
    return base.model_copy(update={"paths": base.paths.model_copy(update={
        "data_dir": tmp_path, "projects_dir": tmp_path / "projects",
        "cache_dir": tmp_path / "cache", "output_dir": tmp_path / "output",
        "database": tmp_path / "cortex.sqlite3",
    })})


def _settings() -> dict:
    return {
        "schema_version": 1, "encoder": "libx264",
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "framing": {"schema_version": 1, "mode": "blurred_background"},
        "captions": {
            "enabled": True, "font_family": "Montserrat", "font_size": 56,
            "words_per_cue": 5, "outline": True,
        },
        "headline": {
            "enabled": True, "font_family": "Montserrat", "font_size": 48,
            "duration_seconds": 4,
        },
        "subtitles": {"sidecar_srt": True},
    }


def _workflow(tmp_path: Path, brief: dict | None = None):
    config = _config(tmp_path)
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Batch")
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, stored_path="source.mp4",
        sha256="a" * 64, size_bytes=1,
    ))
    artifacts = {}
    for stage in ("transcript", "analysis", "suggestion"):
        artifact = domain.create_stage_artifact(StageArtifact(
            project_id=project.id, stage=stage, path=f"{stage}.json", input_hash=stage,
        ))
        artifacts[stage] = artifact.id
    run = domain.create_workflow_run(WorkflowRun(
        project_id=project.id, source_asset_id=source.id, status="ready_for_review",
        stage="review", artifacts=artifacts,
        brief=brief or {"maximum_seconds": 120},
    ))
    return config, domain, project, run


def test_revision_and_export_batch_are_idempotent_and_reconcilable(tmp_path: Path) -> None:
    config, domain, project, run = _workflow(tmp_path)
    client = TestClient(create_app(config))
    revision_response = client.post(
        f"/api/v1/projects/{project.id}/runs/{run.id}/revisions",
        json={
            "selection_artifact_id": run.artifacts["suggestion"],
            "selected_clips": [{
                "key": "clip-1", "rank": 1, "title": "Primeiro", "headline": "Título",
                "start_second": 0, "end_second": 10, "pacing": "balanced",
            }],
            "render_settings": _settings(), "caption_edits": {}, "headlines": {},
            "visual_artifacts": {},
        },
    )
    assert revision_response.status_code == 201, revision_response.text
    revision = revision_response.json()
    assert revision["version"] == 1
    assert client.get(
        f"/api/v1/projects/{project.id}/runs/{run.id}/revision"
    ).json()["id"] == revision["id"]

    endpoint = f"/api/v1/projects/{project.id}/runs/{run.id}/export-batches"
    first = client.post(endpoint, json={
        "revision_id": revision["id"], "idempotency_key": "browser-request-1",
    })
    second = client.post(endpoint, json={
        "revision_id": revision["id"], "idempotency_key": "browser-request-1",
    })
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    batch = first.json()
    assert batch["status"] == "running"
    assert batch["items"][0]["status"] == "planning"

    jobs = JobStore(config.paths.database)
    claimed = jobs.claim_next(worker_pid=999999)
    assert claimed is not None
    jobs.update(claimed.id, JobUpdate(
        status=JobStatus.SUCCEEDED,
        result={"edit_plan_artifact_id": "edl"},
    ))
    reconcile_export_batches(jobs, domain)
    resumed = domain.get_export_batch(batch["id"])
    assert resumed.items[0].status == "rendering"
    assert resumed.items[0].active_job_id != claimed.id
    assert len(jobs.list()) == 2


def test_revision_versions_are_monotonic(tmp_path: Path) -> None:
    config, _domain, project, run = _workflow(tmp_path)
    client = TestClient(create_app(config))
    body = {
        "selection_artifact_id": run.artifacts["suggestion"],
        "selected_clips": [{
            "key": "clip-1", "title": "Primeiro", "start_second": 0,
            "end_second": 10, "pacing": "balanced",
        }],
        "render_settings": _settings(),
    }
    endpoint = f"/api/v1/projects/{project.id}/runs/{run.id}/revisions"
    assert client.post(endpoint, json=body).json()["version"] == 1
    assert client.post(endpoint, json=body).json()["version"] == 2


def test_three_item_batch_finishes_after_client_and_worker_restart(
    tmp_path: Path, monkeypatch,
) -> None:
    config, domain, project, run = _workflow(tmp_path)
    client = TestClient(create_app(config))
    clips = [{
        "key": f"clip-{index}", "title": f"Corte {index}",
        "start_second": index * 10, "end_second": index * 10 + 8,
        "pacing": "balanced",
    } for index in range(3)]
    revision = client.post(
        f"/api/v1/projects/{project.id}/runs/{run.id}/revisions",
        json={"selection_artifact_id": run.artifacts["suggestion"],
              "selected_clips": clips, "render_settings": _settings()},
    ).json()
    batch_id = client.post(
        f"/api/v1/projects/{project.id}/runs/{run.id}/export-batches",
        json={"revision_id": revision["id"], "idempotency_key": "three-clips"},
    ).json()["id"]
    del client  # the server-side queue must not depend on this browser session

    def finish_plan(job, _config, jobs, _domain, _provider=None):
        jobs.update(job.id, JobUpdate(
            status=JobStatus.SUCCEEDED, stage=PipelineStage.COMPLETE,
            result={"edit_plan_artifact_id": f"edl-{job.payload['export_item_id']}"},
        ))

    def finish_render(job, _config, jobs, _domain):
        jobs.update(job.id, JobUpdate(
            status=JobStatus.SUCCEEDED, stage=PipelineStage.COMPLETE,
            result={"render_artifact_id": f"render-{job.payload['export_item_id']}"},
        ))

    import cortex.worker as worker
    monkeypatch.setitem(worker._HANDLERS, JobType.EDIT_PLAN, finish_plan)
    monkeypatch.setitem(worker._HANDLERS, JobType.RENDER, finish_render)
    jobs = JobStore(config.paths.database)
    assert process_next(config, jobs, domain, FakeTranscriptionEngine())
    assert process_next(config, jobs, domain, FakeTranscriptionEngine())

    # Recreate stores as a restarted worker would, then finish the remaining items.
    jobs = JobStore(config.paths.database)
    domain = DomainStore(config.paths.database)
    for _ in range(4):
        assert process_next(config, jobs, domain, FakeTranscriptionEngine())
    batch = domain.get_export_batch(batch_id)
    assert batch.status == "succeeded"
    assert [item.render_artifact_id for item in batch.items] == [
        f"render-{item.id}" for item in batch.items
    ]


def test_confirmed_voice_reaches_render_job_payload(tmp_path: Path, monkeypatch) -> None:
    voice = {"speaker": "S1", "turns": [{"start": 0, "end": 5, "speaker": "S1"}]}
    config, domain, project, run = _workflow(
        tmp_path, {"maximum_seconds": 120, "confirmed_voice": voice})
    client = TestClient(create_app(config))
    revision = client.post(
        f"/api/v1/projects/{project.id}/runs/{run.id}/revisions",
        json={"selection_artifact_id": run.artifacts["suggestion"],
              "selected_clips": [{"key": "c", "title": "C", "start_second": 0, "end_second": 8,
                                  "pacing": "balanced"}], "render_settings": _settings()},
    ).json()
    client.post(
        f"/api/v1/projects/{project.id}/runs/{run.id}/export-batches",
        json={"revision_id": revision["id"], "idempotency_key": "voice"},
    )
    seen = []

    def finish_plan(job, _config, jobs, _domain, _provider=None):
        jobs.update(job.id, JobUpdate(
            status=JobStatus.SUCCEEDED, stage=PipelineStage.COMPLETE,
            result={"edit_plan_artifact_id": "edl"},
        ))

    def finish_render(job, _config, jobs, _domain):
        seen.append(job.payload)
        jobs.update(job.id, JobUpdate(
            status=JobStatus.SUCCEEDED, stage=PipelineStage.COMPLETE,
            result={"render_artifact_id": "render"},
        ))

    import cortex.worker as worker
    monkeypatch.setitem(worker._HANDLERS, JobType.EDIT_PLAN, finish_plan)
    monkeypatch.setitem(worker._HANDLERS, JobType.RENDER, finish_render)
    jobs = JobStore(config.paths.database)
    for _ in range(3):
        process_next(config, jobs, domain, FakeTranscriptionEngine())
    assert seen and seen[0]["confirmed_voice"] == voice


def test_export_items_carry_emphasis_headline_and_minimum(tmp_path: Path) -> None:
    config, _domain, project, run = _workflow(
        tmp_path, brief={"maximum_seconds": 120, "minimum_seconds": 40},
    )
    client = TestClient(create_app(config))
    clips = [
        {"key": "a", "rank": 1, "title": "A", "headline": "Sugestão A", "start_second": 0,
         "end_second": 60, "pacing": "dynamic", "emphasis_word_indices": [3, 4]},
        {"key": "b", "rank": 2, "title": "B", "headline": "Sugestão B", "start_second": 70,
         "end_second": 130, "pacing": "dynamic", "emphasis_word_indices": [9]},
    ]
    revision = client.post(
        f"/api/v1/projects/{project.id}/runs/{run.id}/revisions",
        json={
            "selection_artifact_id": run.artifacts["suggestion"], "selected_clips": clips,
            "render_settings": _settings(), "headlines": {"a": "Minha manchete"},
            "emphasis": {"b": []},
        },
    ).json()
    batch = client.post(
        f"/api/v1/projects/{project.id}/runs/{run.id}/export-batches",
        json={"revision_id": revision["id"], "idempotency_key": "emphasis"},
    ).json()
    settings = {item["clip_key"]: item["render_settings"] for item in batch["items"]}
    assert settings["a"]["captions"]["emphasis_word_indices"] == [3, 4]
    assert settings["b"]["captions"]["emphasis_word_indices"] == []
    assert settings["a"]["headline"]["text"] == "Minha manchete"
    plan_job = next(job for job in JobStore(config.paths.database).list() if job.type == "edit_plan")
    assert plan_job.payload["tighten"] is True
    assert plan_job.payload["minimum_seconds"] == 40
    # Refine is keyed on the AI headline so editing the text never re-bills it.
    assert plan_job.payload["headline"] == "Sugestão A"


def test_export_plan_payload_carries_teaser_only_when_toggled(tmp_path: Path) -> None:
    config, _domain, project, run = _workflow(tmp_path)
    client = TestClient(create_app(config))
    clip = {"key": "a", "rank": 1, "title": "A", "headline": "A", "start_second": 0,
            "end_second": 60, "pacing": "dynamic",
            "teaser": {"start": 50.0, "end": 53.5, "text": "frase final"}}

    def export(teaser_on: bool, key: str):
        settings = {**_settings(), "transitions": {"teaser": teaser_on}}
        revision = client.post(
            f"/api/v1/projects/{project.id}/runs/{run.id}/revisions",
            json={"selection_artifact_id": run.artifacts["suggestion"],
                  "selected_clips": [clip], "render_settings": settings},
        ).json()
        client.post(
            f"/api/v1/projects/{project.id}/runs/{run.id}/export-batches",
            json={"revision_id": revision["id"], "idempotency_key": key},
        )
        return [job for job in JobStore(config.paths.database).list() if job.type == "edit_plan"]

    off = export(False, "teaser-off")[0]
    assert "teaser" not in off.payload
    on = export(True, "teaser-on")[0]
    assert on.payload["teaser"] == {"start": 50.0, "end": 53.5}
