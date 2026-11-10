"""Google Drive delivery of exported clips through rclone.

Each exported clip goes to ``<remote>:<folder>/<episode>/<YYYY-MM-DD>/<NN - headline>.mp4``
right after its render succeeds (``drive.*`` in config). The upload is its own job: a
missing login or a network failure never fails the export, it is reported on the job
and the clip stays unsaved. A successful upload marks the clip as saved ("Salvo").

rclone runs with an argument array, a timeout and cancellation; its login lives in
rclone's own config (``rclone config``), never in this repository.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from cortex.config import CortexConfig, load_config
from cortex.domain.models import ClipState, ExportItem, ExportItemStatus
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.schemas import Job, JobCreate, JobStatus, JobType, JobUpdate, PipelineStage, utc_now

_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')
_STDERR_LIMIT = 600


class DriveError(RuntimeError):
    """Upload could not run or rclone reported a failure; the message is shown to the user."""


class DriveCancelled(RuntimeError):
    pass


def safe_name(text: str, limit: int = 80) -> str:
    """A file/folder name without characters Drive clients or phones reject."""
    name = re.sub(r"\s+", " ", _UNSAFE.sub(" ", text or "")).strip(" .")
    return name[:limit].rstrip(" .")


def drive_target(
    config: CortexConfig, *, episode_title: str, exported_at: datetime, position: int, headline: str,
) -> str:
    """rclone destination of one exported clip; the date is the local export day."""
    episode = safe_name(episode_title) or "Episódio sem título"
    day = exported_at.astimezone().date().isoformat()
    clip = safe_name(headline) or "Corte"
    folder = config.drive.folder.strip("/")
    return f"{config.drive.remote}:{folder}/{episode}/{day}/{position + 1:02d} - {clip}.mp4"


def rclone_binary(config: CortexConfig) -> str:
    configured = str(config.drive.rclone)
    found = configured if Path(configured).is_absolute() and Path(configured).is_file() else shutil.which(configured)
    if not found:
        raise DriveError(
            f"rclone não encontrado ({configured}). Instale com `brew install rclone` e confira drive.rclone no config."
        )
    return found


def _explain(stderr: str, remote: str) -> str:
    tail = stderr.strip()[-_STDERR_LIMIT:]
    if "didn't find section in config file" in stderr or "config file not found" in stderr:
        return (f"Google Drive não conectado: o remote '{remote}' não existe no rclone. "
                f"Rode `rclone config create {remote} drive scope=drive.file` e faça o login.")
    if "invalid_grant" in stderr or "token expired" in stderr.lower():
        return f"Login do Google Drive expirou: rode `rclone config reconnect {remote}:`. Detalhe: {tail}"
    return f"rclone falhou: {tail or 'sem detalhe'}"


def upload_file(
    config: CortexConfig, source: Path, target: str, should_cancel: Callable[[], bool] = lambda: False,
) -> None:
    """Copy one file to its exact Drive path (rclone checks size and hash after the copy)."""
    command = [
        rclone_binary(config), "copyto", str(source), target,
        "--retries", "3", "--low-level-retries", "10",
        "--contimeout", "30s", "--timeout", "120s", "--stats", "0", "-q",
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except OSError as exc:
        raise DriveError(f"rclone não pôde ser executado: {exc}") from exc
    deadline = time.monotonic() + config.drive.timeout_seconds
    while True:
        try:
            _, stderr = process.communicate(timeout=1)
            break
        except subprocess.TimeoutExpired:
            cancelled = should_cancel()
            if cancelled or time.monotonic() > deadline:
                process.kill()
                process.communicate()
                if cancelled:
                    raise DriveCancelled() from None
                raise DriveError(f"Upload para o Drive passou de {config.drive.timeout_seconds:.0f} s") from None
    if process.returncode != 0:
        raise DriveError(_explain(stderr or "", config.drive.remote))


def _episode_title(config: CortexConfig, domain: DomainStore, project_id: str) -> str:
    from cortex.library import EpisodeLibrary

    episode = EpisodeLibrary(config, domain).for_project(project_id)
    if episode is not None and episode.title:
        return episode.title
    return domain.get_project(project_id).name


def _mark_saved(domain: DomainStore, item: ExportItem, workflow_run_id: str) -> None:
    current = next(
        (state for state in domain.list_clip_states([item.project_id])
         if state.workflow_run_id == workflow_run_id and state.clip_key == item.clip_key),
        None,
    ) or ClipState(project_id=item.project_id, workflow_run_id=workflow_run_id, clip_key=item.clip_key)
    if not current.saved:
        domain.upsert_clip_state(current.model_copy(update={"saved": True, "saved_at": utc_now()}))


def upload_export_item(
    config: CortexConfig, domain: DomainStore, item_id: str,
    should_cancel: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda message: None,
) -> dict:
    from cortex.api import render_download_name
    from cortex.render.schemas import RenderDocument

    item = domain.get_export_item(item_id)
    if item.status != ExportItemStatus.SUCCEEDED or not item.render_artifact_id:
        raise DriveError("Este corte ainda não terminou de exportar")
    batch = domain.get_export_batch(item.batch_id)
    artifact = domain.get_stage_artifact(item.render_artifact_id)
    document = RenderDocument.model_validate_json(Path(artifact.path).read_text(encoding="utf-8"))
    media = Path(document.output_path)
    if not media.is_file():
        raise DriveError(f"Arquivo do corte não existe mais: {media}")
    headline = render_download_name(document, artifact.id, "")
    target = drive_target(
        config, episode_title=_episode_title(config, domain, item.project_id),
        exported_at=artifact.created_at, position=item.position, headline=headline,
    )
    progress(f"Enviando ao Drive: {target.split(':', 1)[1]}")
    upload_file(config, media, target, should_cancel)
    _mark_saved(domain, item, batch.workflow_run_id)
    return {"drive_path": target, "export_item_id": item.id, "render_artifact_id": artifact.id}


def run_drive_upload_job(job: Job, config: CortexConfig, jobs: JobStore, domain: DomainStore) -> None:
    def cancelled() -> bool:
        try:
            return jobs.get(job.id).status == JobStatus.CANCELLED
        except Exception:  # noqa: BLE001 - a read failure must not abort the upload
            return False

    def progress(message: str) -> None:
        jobs.update(job.id, JobUpdate(progress=10, stage=PipelineStage.RENDER, message=message))

    try:
        result = upload_export_item(
            config, domain, job.payload["drive_export_item_id"], should_cancel=cancelled, progress=progress,
        )
    except DriveCancelled:
        return
    path = result["drive_path"].split(":", 1)[1]
    jobs.update(job.id, JobUpdate(
        status=JobStatus.SUCCEEDED, progress=100, stage=PipelineStage.COMPLETE,
        message=f"No Drive: {path}", result=result,
    ))


def queue_drive_upload(jobs: JobStore, item: ExportItem, *, attempt: int = 1) -> Job:
    """One upload per render artifact; a retry passes a higher attempt."""
    suffix = "" if attempt == 1 else f":retry:{attempt}"
    return jobs.create(JobCreate(
        type=JobType.DRIVE_UPLOAD, project_id=item.project_id,
        payload={"drive_export_item_id": item.id},
        idempotency_key=f"drive:{item.render_artifact_id}{suffix}",
    ))


def queue_after_export_render(job: Job, config: CortexConfig, jobs: JobStore, domain: DomainStore) -> None:
    """After an export render succeeds, queue its Drive upload (when drive.enabled)."""
    item_id = job.payload.get("export_item_id")
    if not config.drive.enabled or job.type != JobType.RENDER or not item_id:
        return
    if jobs.get(job.id).status != JobStatus.SUCCEEDED:
        return
    item = domain.get_export_item(item_id)
    if item.status == ExportItemStatus.SUCCEEDED and item.render_artifact_id:
        queue_drive_upload(jobs, item)


def _exported_since(config: CortexConfig, since: date) -> list[str]:
    with sqlite3.connect(config.paths.database, timeout=30) as connection:
        rows = connection.execute(
            "SELECT id, updated_at FROM export_items WHERE status = ? ORDER BY created_at",
            (ExportItemStatus.SUCCEEDED.value,),
        ).fetchall()
    return [row[0] for row in rows if datetime.fromisoformat(row[1]).astimezone().date() >= since]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m cortex.drive",
        description="Enfileira (ou reenfileira) o envio ao Google Drive de cortes já exportados.",
    )
    parser.add_argument("items", nargs="*", help="ids de export_items")
    parser.add_argument("--desde", type=date.fromisoformat, help="todos os cortes exportados desde AAAA-MM-DD")
    parser.add_argument("--testar", action="store_true", help="só confere o rclone e o login do Drive")
    args = parser.parse_args(argv)
    config = load_config()
    if args.testar:
        completed = subprocess.run(
            [rclone_binary(config), "lsd", f"{config.drive.remote}:"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        print("Drive conectado." if completed.returncode == 0 else _explain(completed.stderr, config.drive.remote))
        return
    ids = list(args.items) + (_exported_since(config, args.desde) if args.desde else [])
    if not ids:
        parser.error("informe ids ou --desde")
    jobs, domain = JobStore(config.paths.database), DomainStore(config.paths.database)
    for item_id in ids:
        item = domain.get_export_item(item_id)
        attempt = 1
        # An earlier failed/cancelled upload keeps its key: retry under the next attempt.
        while (job := queue_drive_upload(jobs, item, attempt=attempt)).status in {
            JobStatus.FAILED, JobStatus.CANCELLED,
        }:
            attempt += 1
        print(f"{item.title[:60]}: {job.status.value} ({job.id})")


if __name__ == "__main__":
    main()
