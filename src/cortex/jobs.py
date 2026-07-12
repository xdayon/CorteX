from __future__ import annotations

import json
import sqlite3
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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at)"
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

    def create(self, request: JobCreate) -> Job:
        job = Job(type=request.type, project_id=request.project_id, payload=request.payload)
        self._save(job)
        return job

    def get(self, job_id: str) -> Job:
        with self._connect() as connection:
            row = connection.execute("SELECT data FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return Job.model_validate_json(row["data"])

    def list(self, limit: int = 50) -> list[Job]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Job.model_validate_json(row["data"]) for row in rows]

    def update(self, job_id: str, update: JobUpdate) -> Job:
        job = self.get(job_id)
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
        changes["updated_at"] = utc_now()
        job = job.model_copy(update=changes)
        self._save(job)
        return job

    def cancel(self, job_id: str) -> Job:
        return self.update(job_id, JobUpdate(
            status=JobStatus.CANCELLED,
            message="Cancelamento solicitado",
        ))

    def claim_next(self) -> Job | None:
        """Atomically claim the oldest queued job, marking it running.

        Uses a guarded UPDATE (WHERE status='queued') so concurrent workers
        racing on the same row never both succeed: SQLite serializes writers,
        and the second UPDATE's WHERE clause fails once the first commits.
        """
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT id, data FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            job = Job.model_validate_json(row["data"])
            claimed = job.model_copy(update={
                "status": JobStatus.RUNNING,
                "message": "Em execução",
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

    def _save(self, job: Job) -> None:
        payload = json.dumps(job.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO jobs (id, data, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    data=excluded.data, status=excluded.status, updated_at=excluded.updated_at""",
                (
                    job.id,
                    payload,
                    job.status.value,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                ),
            )

