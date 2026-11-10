from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from asgi_client import ASGITestClient as TestClient

from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import StageArtifact
from cortex.drive import DriveError, drive_target, upload_file
from cortex.jobs import JobStore
from cortex.schemas import JobStatus, JobType, JobUpdate, PipelineStage
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine
from test_export_batches import _settings, _workflow
from test_render_media_api import _document


@pytest.fixture
def sao_paulo(monkeypatch):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _fake_rclone(tmp_path: Path, *, stderr: str = "", code: int = 0) -> tuple[Path, Path]:
    log = tmp_path / "rclone-args.txt"
    message = tmp_path / "rclone-stderr.txt"
    message.write_text(stderr)
    script = tmp_path / "rclone"
    script.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" >> '{log}'\n"
        f"cat '{message}' >&2\n"
        f"exit {code}\n"
    )
    script.chmod(0o755)
    return script, log


def _with_drive(config: CortexConfig, rclone: Path) -> CortexConfig:
    return config.model_copy(update={"drive": config.drive.model_copy(update={
        "enabled": True, "rclone": str(rclone), "remote": "gdrive", "folder": "CorteX",
    })})


def test_target_is_episode_then_local_day_then_numbered_headline(tmp_path: Path, sao_paulo) -> None:
    config = _with_drive(_workflow(tmp_path)[0], tmp_path / "rclone")
    # 02:30 UTC on the 11th is still the evening of the 10th in São Paulo.
    target = drive_target(
        config, episode_title='Flow #12: "Dayon" / IA?',
        exported_at=datetime(2026, 11, 11, 2, 30, tzinfo=timezone.utc),
        position=2, headline="A IA vai te substituir?",
    )
    assert target == "gdrive:CorteX/Flow #12 Dayon IA/2026-11-10/03 - A IA vai te substituir.mp4"


def test_upload_runs_copyto_with_the_exact_target(tmp_path: Path) -> None:
    rclone, log = _fake_rclone(tmp_path)
    config = _with_drive(_workflow(tmp_path)[0], rclone)
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"mp4")
    upload_file(config, media, "gdrive:CorteX/E/2026-11-10/01 - X.mp4")
    args = log.read_text().splitlines()
    assert args[:3] == ["copyto", str(media), "gdrive:CorteX/E/2026-11-10/01 - X.mp4"]


def test_missing_login_and_missing_binary_are_explained(tmp_path: Path) -> None:
    rclone, _ = _fake_rclone(tmp_path, stderr='Failed to create file system for "gdrive:CorteX": didn\'t find section in config file', code=1)
    config = _with_drive(_workflow(tmp_path)[0], rclone)
    with pytest.raises(DriveError, match="não conectado.*rclone config create gdrive"):
        upload_file(config, tmp_path, "gdrive:CorteX/x.mp4")
    with pytest.raises(DriveError, match="rclone não encontrado"):
        upload_file(_with_drive(config, tmp_path / "nope"), tmp_path, "gdrive:CorteX/x.mp4")


def test_export_render_queues_upload_that_marks_the_clip_saved(tmp_path: Path, monkeypatch) -> None:
    config, domain, project, run = _workflow(tmp_path)
    rclone, log = _fake_rclone(tmp_path)
    config = _with_drive(config, rclone)
    client = TestClient(create_app(config))
    clip = {"key": "clip-0", "title": "Corte 0", "start_second": 0, "end_second": 8, "pacing": "balanced"}
    revision = client.post(
        f"/api/v1/projects/{project.id}/runs/{run.id}/revisions",
        json={"selection_artifact_id": run.artifacts["suggestion"],
              "selected_clips": [clip], "render_settings": _settings()},
    ).json()
    batch_id = client.post(
        f"/api/v1/projects/{project.id}/runs/{run.id}/export-batches",
        json={"revision_id": revision["id"], "idempotency_key": "drive"},
    ).json()["id"]

    def finish_plan(job, _config, jobs, _domain, _provider=None):
        jobs.update(job.id, JobUpdate(status=JobStatus.SUCCEEDED, stage=PipelineStage.COMPLETE,
                                      result={"edit_plan_artifact_id": "plan"}))

    def finish_render(job, _config, jobs, domain):
        media = tmp_path / "render.mp4"
        media.write_bytes(b"mp4")
        manifest = tmp_path / "render.json"
        manifest.write_text(_document(project.id, media).model_dump_json())
        artifact = domain.create_stage_artifact(StageArtifact(
            project_id=project.id, stage="render", path=str(manifest), input_hash="render",
        ))
        jobs.update(job.id, JobUpdate(status=JobStatus.SUCCEEDED, stage=PipelineStage.COMPLETE,
                                      result={"render_artifact_id": artifact.id}))

    import cortex.worker as worker
    monkeypatch.setitem(worker._HANDLERS, JobType.EDIT_PLAN, finish_plan)
    monkeypatch.setitem(worker._HANDLERS, JobType.RENDER, finish_render)
    jobs = JobStore(config.paths.database)
    for _ in range(3):
        assert process_next(config, jobs, domain, FakeTranscriptionEngine())
    assert not process_next(config, jobs, domain, FakeTranscriptionEngine())

    assert domain.get_export_batch(batch_id).status == "succeeded"
    upload = next(job for job in jobs.list(limit=20) if job.type == JobType.DRIVE_UPLOAD)
    assert upload.status == JobStatus.SUCCEEDED, upload.error
    target = upload.result["drive_path"]
    assert target.startswith("gdrive:CorteX/Batch/") and target.endswith("/01 - cortex-render-"
                                                                          f"{upload.result['render_artifact_id'][:12]}.mp4")
    assert log.read_text().splitlines()[2] == target
    state = next(s for s in domain.list_clip_states([project.id]) if s.clip_key == "clip-0")
    assert state.saved and state.saved_at is not None


def test_tests_never_reach_the_real_drive() -> None:
    assert load_config().drive.enabled is False
