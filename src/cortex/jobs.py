from __future__ import annotations

import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from cortex.schemas import Job, JobCreate, JobStatus, JobUpdate, utc_now


_TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
_ALLOWED = {
    JobStatus.QUEUED: {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.RUNNING: {JobStatus.RUNNING, JobStatus.SUCCEEDED, JobStatus.CANCELLED, JobStatus.FAILED},
}


class JobNotFoundError(LookupError):
    pass


class InvalidJobTransitionError(ValueError):
    pass


class JobStore:
    def __init__(self, database: str | Path):
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "status" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN status TEXT NOT NULL DEFAULT 'queued'"
                )
            if "idempotency_key" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN idempotency_key TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency_key "
                "ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL"
            )
        self._backfill_status()

    def _backfill_status(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, data FROM jobs WHERE status = 'queued'"
            ).fetchall()
            for row in rows:
                job = Job.model_validate_json(row["data"])
                if job.status != JobStatus.QUEUED:
                    connection.execute(
                        "UPDATE jobs SET status = ? WHERE id = ?", (job.status.value, row["id"])
                    )

    def create(
        self, request: JobCreate, *, connection: sqlite3.Connection | None = None,
    ) -> Job:
        if request.idempotency_key is not None:
            existing = self.get_by_idempotency_key(
                request.idempotency_key, connection=connection,
            )
            if existing is not None:
                return existing
        job = Job(
            type=request.type,
            project_id=request.project_id,
            payload=request.payload,
            idempotency_key=request.idempotency_key,
        )
        self._save(job, connection=connection)
        return job

    def get_by_idempotency_key(
        self, key: str, *, connection: sqlite3.Connection | None = None,
    ) -> Job | None:
        owns_connection = connection is None
        connection = connection or self._connect()
        try:
            row = connection.execute(
                "SELECT data FROM jobs WHERE idempotency_key = ?", (key,),
            ).fetchone()
            return Job.model_validate_json(row["data"]) if row is not None else None
        finally:
            if owns_connection:
                connection.close()

    def get(
        self, job_id: str, *, connection: sqlite3.Connection | None = None,
    ) -> Job:
        owns_connection = connection is None
        connection = connection or self._connect()
        try:
            row = connection.execute("SELECT data FROM jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            if owns_connection:
                connection.close()
        if row is None:
            raise JobNotFoundError(job_id)
        return Job.model_validate_json(row["data"])

    def list(self, limit: int = 50) -> list[Job]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Job.model_validate_json(row["data"]) for row in rows]

    def update(
        self, job_id: str, update: JobUpdate, *, connection: sqlite3.Connection | None = None,
    ) -> Job:
        job = self.get(job_id, connection=connection)
        changes = update.model_dump(exclude_unset=True)
        next_status = changes.get("status", job.status)
        if job.status in _TERMINAL:
            raise InvalidJobTransitionError(f"job {job.id} is already {job.status}")
        if next_status not in _ALLOWED[job.status]:
            raise InvalidJobTransitionError(f"cannot transition {job.status} to {next_status}")
        if changes.get("progress", job.progress) < job.progress:
            raise InvalidJobTransitionError("job progress cannot move backwards")
        if next_status == JobStatus.SUCCEEDED:
            changes["progress"] = 100.0
        now = utc_now()
        changes["updated_at"] = now
        if next_status in _TERMINAL:
            changes["finished_at"] = now
            if job.started_at is not None:
                changes["elapsed_seconds"] = max(0.0, (now - job.started_at).total_seconds())
        job = job.model_copy(update=changes)
        self._save(job, connection=connection)
        return job

    def cancel(
        self, job_id: str, *, connection: sqlite3.Connection | None = None,
    ) -> Job:
        return self.update(job_id, JobUpdate(
            status=JobStatus.CANCELLED,
            message="Cancelamento solicitado",
        ), connection=connection)

    def claim_next(self, *, worker_pid: int | None = None) -> Job | None:
        """Atomically claim the oldest queued job, marking it running.

        Uses a guarded UPDATE (WHERE status='queued') so concurrent workers
        racing on the same row never both succeed: SQLite serializes writers,
        and the second UPDATE's WHERE clause fails once the first commits.
        """
        connection = self._connect()
        try:
            # Hold the queue selection and status transition in one write
            # transaction so recovery and concurrent workers cannot interleave.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id, data FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            job = Job.model_validate_json(row["data"])
            claimed = job.model_copy(update={
                "status": JobStatus.RUNNING,
                "message": "Em execução",
                "worker_pid": worker_pid if worker_pid is not None else os.getpid(),
                "started_at": utc_now(),
                "finished_at": None,
                "elapsed_seconds": None,
                "updated_at": utc_now(),
            })
            payload = json.dumps(claimed.model_dump(mode="json"), ensure_ascii=False)
            cursor = connection.execute(
                "UPDATE jobs SET data = ?, status = ?, updated_at = ? "
                "WHERE id = ? AND status = 'queued'",
                (payload, claimed.status.value, claimed.updated_at.isoformat(), job.id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                return None
            return claimed
        finally:
            connection.close()

    def recover_abandoned_jobs(self) -> list[Job]:
        """Requeue jobs left running by a stopped worker process.

        A live local worker is never recovered, preventing a second worker
        startup from duplicating work. Recovery changes only operational fields. The original job identity,
        payload, progress, and timestamps created before the interruption are
        retained so a restarted worker can safely resume FIFO claims.
        """
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id, data FROM jobs WHERE status = 'running' ORDER BY created_at ASC"
            ).fetchall()
            recovered: list[Job] = []
            for row in rows:
                job = Job.model_validate_json(row["data"])
                if job.worker_pid is not None and _pid_is_alive(job.worker_pid):
                    continue
                requeued = job.model_copy(update={
                    "status": JobStatus.QUEUED,
                    "message": "Recuperado apos interrupcao; reenfileirado",
                    "worker_pid": None,
                    "started_at": None,
                    "updated_at": utc_now(),
                })
                payload = json.dumps(requeued.model_dump(mode="json"), ensure_ascii=False)
                connection.execute(
                    "UPDATE jobs SET data = ?, status = ?, updated_at = ? "
                    "WHERE id = ? AND status = 'running'",
                    (payload, requeued.status.value, requeued.updated_at.isoformat(), job.id),
                )
                recovered.append(requeued)
            connection.commit()
            return recovered
        finally:
            connection.close()

    def fail_stalled_jobs(self, *, stale_after_seconds: float) -> list[Job]:
        """Fail jobs that claim to be running but stopped reporting progress.

        This is deliberately invoked by the single local worker only while it
        is between jobs.  It catches the failure mode where a handler returns
        or a child disappears without committing a terminal job status.
        """
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        now = utc_now()
        cutoff = now - timedelta(seconds=stale_after_seconds)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id, data FROM jobs WHERE status = 'running' AND updated_at < ? "
                "ORDER BY created_at ASC", (cutoff.isoformat(),),
            ).fetchall()
            failed: list[Job] = []
            for row in rows:
                job = Job.model_validate_json(row["data"])
                inactive_seconds = max(0, round((now - job.updated_at).total_seconds()))
                message = f"Interrompida: sem atualização há {inactive_seconds}s"
                updated = job.model_copy(update={
                    "status": JobStatus.FAILED,
                    "message": message,
                    "error": (
                        "O worker não registrou atividade dentro do prazo de segurança. "
                        "Repita o processamento para tentar novamente."
                    ),
                    "finished_at": now,
                    "elapsed_seconds": (
                        max(0.0, (now - job.started_at).total_seconds())
                        if job.started_at is not None else None
                    ),
                    "updated_at": now,
                })
                self._save(updated, connection=connection)
                failed.append(updated)
            connection.commit()
            return failed
        finally:
            connection.close()

    def _save(
        self, job: Job, *, connection: sqlite3.Connection | None = None,
    ) -> None:
        payload = json.dumps(job.model_dump(mode="json"), ensure_ascii=False)
        owns_connection = connection is None
        connection = connection or self._connect()
        try:
            connection.execute(
                """INSERT INTO jobs
                   (id, data, status, idempotency_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    data=excluded.data, status=excluded.status,
                    idempotency_key=excluded.idempotency_key,
                    updated_at=excluded.updated_at""",
                (
                    job.id,
                    payload,
                    job.status.value,
                    job.idempotency_key,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                ),
            )
            if owns_connection:
                connection.commit()
        finally:
            if owns_connection:
                connection.close()


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
