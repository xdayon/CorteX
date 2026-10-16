from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path

from cortex.domain.models import (
    ClipPublication,
    ClipRevision,
    ClipState,
    ExportBatch,
    ExportItem,
    Project,
    RenderPreset,
    SourceAsset,
    StageArtifact,
    TranscriptArtifact,
    WorkflowRun,
)
from cortex.schemas import utc_now


class ProjectNotFoundError(LookupError):
    pass


class SourceAssetNotFoundError(LookupError):
    pass


class TranscriptArtifactNotFoundError(LookupError):
    pass


class StageArtifactNotFoundError(LookupError):
    pass


class RenderPresetNotFoundError(LookupError):
    pass


class RenderPresetNameConflictError(ValueError):
    pass


class ClipPublicationNotFoundError(LookupError):
    pass


class WorkflowRunNotFoundError(LookupError):
    pass


class ClipRevisionNotFoundError(LookupError):
    pass


class ExportBatchNotFoundError(LookupError):
    pass


class ExportItemNotFoundError(LookupError):
    pass


class DomainStore:
    """Persists Project/SourceAsset/TranscriptArtifact/StageArtifact.

    Mirrors the JobStore pattern from cortex.jobs: one table per entity,
    payload serialized as JSON in a `data` column, plus a few indexed
    columns used for lookups (project_id, source_asset_id).
    """

    def __init__(self, database: str | Path):
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @contextmanager
    def transaction(self):
        """One write transaction shared by domain and queue operations."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                    id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS source_assets (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    sha256 TEXT,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS transcript_artifacts (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    source_asset_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS stage_artifacts (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    input_hash TEXT,
                    schema_version INTEGER,
                    path TEXT,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            source_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(source_assets)")
            }
            if "sha256" not in source_columns:
                connection.execute("ALTER TABLE source_assets ADD COLUMN sha256 TEXT")
            stage_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(stage_artifacts)")
            }
            for name, kind in (
                ("input_hash", "TEXT"), ("schema_version", "INTEGER"), ("path", "TEXT"),
            ):
                if name not in stage_columns:
                    connection.execute(f"ALTER TABLE stage_artifacts ADD COLUMN {name} {kind}")
            for row in connection.execute(
                "SELECT id, data FROM source_assets WHERE sha256 IS NULL"
            ).fetchall():
                asset = SourceAsset.model_validate_json(row["data"])
                connection.execute(
                    "UPDATE source_assets SET sha256 = ? WHERE id = ?", (asset.sha256, asset.id),
                )
            for row in connection.execute(
                "SELECT id, data FROM stage_artifacts "
                "WHERE input_hash IS NULL OR schema_version IS NULL OR path IS NULL"
            ).fetchall():
                artifact = StageArtifact.model_validate_json(row["data"])
                connection.execute(
                    "UPDATE stage_artifacts SET input_hash = ?, schema_version = ?, path = ? "
                    "WHERE id = ?",
                    (artifact.input_hash, artifact.schema_version, artifact.path, artifact.id),
                )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS render_presets (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, name)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS workflow_runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    source_asset_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS clip_revisions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    workflow_run_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workflow_run_id, version)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS export_batches (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    workflow_run_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS export_items (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(batch_id, position)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS clip_publications (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    render_artifact_id TEXT NOT NULL UNIQUE,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS clip_states (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    workflow_run_id TEXT NOT NULL,
                    clip_key TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workflow_run_id, clip_key)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_clip_states_project ON clip_states(project_id)"
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_workflow_runs_project_created
                   ON workflow_runs (project_id, created_at DESC)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_render_presets_project_created
                   ON render_presets (project_id, created_at DESC)"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_revisions_run_version "
                "ON clip_revisions(workflow_run_id, version DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_batches_run_created "
                "ON export_batches(workflow_run_id, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_export_items_batch_position "
                "ON export_items(batch_id, position)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_source_assets_project_sha "
                "ON source_assets(project_id, sha256)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_stage_artifact_cache "
                "ON stage_artifacts(project_id, stage, input_hash, schema_version, created_at DESC)"
            )
            for migration_id in (
                "2026-09-13-export-batches-v1",
                "2026-09-13-indexed-artifact-cache-v1",
                "2026-10-14-clip-publications-v1",
                "2026-10-16-clip-states-v1",
            ):
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(id, applied_at) VALUES (?, ?)",
                    (migration_id, utc_now().isoformat()),
                )

    # -- Projects ---------------------------------------------------

    def create_project(self, name: str) -> Project:
        project = Project(name=name)
        payload = json.dumps(project.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO projects (id, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (project.id, payload, project.created_at.isoformat(), project.updated_at.isoformat()),
            )
        return project

    def get_project(self, project_id: str) -> Project:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ProjectNotFoundError(project_id)
        return Project.model_validate_json(row["data"])

    def list_projects(self, limit: int = 100) -> list[Project]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM projects ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Project.model_validate_json(row["data"]) for row in rows]

    # -- Source assets ------------------------------------------------

    def create_source_asset(self, asset: SourceAsset) -> SourceAsset:
        payload = json.dumps(asset.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO source_assets (id, project_id, sha256, data, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (asset.id, asset.project_id, asset.sha256, payload, asset.created_at.isoformat()),
            )
        return asset

    def get_source_asset(self, source_asset_id: str) -> SourceAsset:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM source_assets WHERE id = ?", (source_asset_id,)
            ).fetchone()
        if row is None:
            raise SourceAssetNotFoundError(source_asset_id)
        return SourceAsset.model_validate_json(row["data"])

    def list_source_assets(self, project_id: str) -> list[SourceAsset]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM source_assets WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [SourceAsset.model_validate_json(row["data"]) for row in rows]

    def find_source_asset_by_sha256(self, project_id: str, sha256: str) -> SourceAsset | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM source_assets WHERE project_id = ? AND sha256 = ? "
                "ORDER BY created_at DESC LIMIT 1", (project_id, sha256),
            ).fetchone()
        return SourceAsset.model_validate_json(row["data"]) if row is not None else None

    # -- Workflow runs -------------------------------------------------

    def create_workflow_run(
        self, run: WorkflowRun, *, connection: sqlite3.Connection | None = None,
    ) -> WorkflowRun:
        payload = json.dumps(run.model_dump(mode="json"), ensure_ascii=False)
        owns_connection = connection is None
        connection = connection or self._connect()
        try:
            connection.execute(
                """INSERT INTO workflow_runs
                   (id, project_id, source_asset_id, status, data, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    run.id,
                    run.project_id,
                    run.source_asset_id,
                    run.status.value,
                    payload,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )
            if owns_connection:
                connection.commit()
        finally:
            if owns_connection:
                connection.close()
        return run

    def get_workflow_run(self, run_id: str) -> WorkflowRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM workflow_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise WorkflowRunNotFoundError(run_id)
        return WorkflowRun.model_validate_json(row["data"])

    def list_workflow_runs(self, project_id: str) -> list[WorkflowRun]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT data FROM workflow_runs WHERE project_id = ?
                   ORDER BY created_at DESC""",
                (project_id,),
            ).fetchall()
        return [WorkflowRun.model_validate_json(row["data"]) for row in rows]

    def update_workflow_run(
        self, run_id: str, *, connection: sqlite3.Connection | None = None, **changes: object,
    ) -> WorkflowRun:
        if connection is None:
            current = self.get_workflow_run(run_id)
        else:
            row = connection.execute(
                "SELECT data FROM workflow_runs WHERE id = ?", (run_id,),
            ).fetchone()
            if row is None:
                raise WorkflowRunNotFoundError(run_id)
            current = WorkflowRun.model_validate_json(row["data"])
        changes["updated_at"] = utc_now()
        updated = WorkflowRun.model_validate({**current.model_dump(), **changes})
        payload = json.dumps(updated.model_dump(mode="json"), ensure_ascii=False)
        owns_connection = connection is None
        connection = connection or self._connect()
        try:
            cursor = connection.execute(
                """UPDATE workflow_runs SET status = ?, data = ?, updated_at = ?
                   WHERE id = ?""",
                (updated.status.value, payload, updated.updated_at.isoformat(), run_id),
            )
            if owns_connection:
                connection.commit()
        finally:
            if owns_connection:
                connection.close()
        if cursor.rowcount != 1:
            raise WorkflowRunNotFoundError(run_id)
        return updated

    # -- Review revisions and durable export batches ------------------

    def create_clip_revision(self, revision: ClipRevision) -> ClipRevision:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM clip_revisions "
                "WHERE workflow_run_id = ?", (revision.workflow_run_id,),
            ).fetchone()
            revision = revision.model_copy(update={
                "version": int(row["version"]) + 1,
                "updated_at": utc_now(),
            })
            payload = json.dumps(revision.model_dump(mode="json"), ensure_ascii=False)
            connection.execute(
                """INSERT INTO clip_revisions
                   (id, project_id, workflow_run_id, version, data, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (revision.id, revision.project_id, revision.workflow_run_id, revision.version,
                 payload, revision.created_at.isoformat(), revision.updated_at.isoformat()),
            )
        return revision

    def get_clip_revision(self, revision_id: str) -> ClipRevision:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM clip_revisions WHERE id = ?", (revision_id,),
            ).fetchone()
        if row is None:
            raise ClipRevisionNotFoundError(revision_id)
        return ClipRevision.model_validate_json(row["data"])

    def latest_clip_revision(self, workflow_run_id: str) -> ClipRevision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM clip_revisions WHERE workflow_run_id = ? "
                "ORDER BY version DESC LIMIT 1", (workflow_run_id,),
            ).fetchone()
        return ClipRevision.model_validate_json(row["data"]) if row is not None else None

    @staticmethod
    def _save_export_batch(connection: sqlite3.Connection, batch: ExportBatch) -> None:
        payload = json.dumps(batch.model_copy(update={"items": []}).model_dump(mode="json"),
                             ensure_ascii=False)
        connection.execute(
            """INSERT INTO export_batches
               (id, project_id, workflow_run_id, revision_id, idempotency_key, status,
                data, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data,
                   updated_at=excluded.updated_at""",
            (batch.id, batch.project_id, batch.workflow_run_id, batch.revision_id,
             batch.idempotency_key, batch.status.value, payload,
             batch.created_at.isoformat(), batch.updated_at.isoformat()),
        )

    @staticmethod
    def _save_export_item(connection: sqlite3.Connection, item: ExportItem) -> None:
        payload = json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
        connection.execute(
            """INSERT INTO export_items
               (id, batch_id, project_id, position, status, data, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data,
                   updated_at=excluded.updated_at""",
            (item.id, item.batch_id, item.project_id, item.position, item.status.value,
             payload, item.created_at.isoformat(), item.updated_at.isoformat()),
        )

    def create_export_batch(
        self, batch: ExportBatch, *, connection: sqlite3.Connection,
    ) -> ExportBatch:
        row = connection.execute(
            "SELECT id FROM export_batches WHERE idempotency_key = ?",
            (batch.idempotency_key,),
        ).fetchone()
        if row is not None:
            return self.get_export_batch(row["id"], connection=connection)
        self._save_export_batch(connection, batch)
        for item in batch.items:
            self._save_export_item(connection, item)
        return batch

    def get_export_item(
        self, item_id: str, *, connection: sqlite3.Connection | None = None,
    ) -> ExportItem:
        owns_connection = connection is None
        connection = connection or self._connect()
        try:
            row = connection.execute(
                "SELECT data FROM export_items WHERE id = ?", (item_id,),
            ).fetchone()
        finally:
            if owns_connection:
                connection.close()
        if row is None:
            raise ExportItemNotFoundError(item_id)
        return ExportItem.model_validate_json(row["data"])

    def get_export_batch(
        self, batch_id: str, *, connection: sqlite3.Connection | None = None,
    ) -> ExportBatch:
        owns_connection = connection is None
        connection = connection or self._connect()
        try:
            row = connection.execute(
                "SELECT data FROM export_batches WHERE id = ?", (batch_id,),
            ).fetchone()
            if row is None:
                raise ExportBatchNotFoundError(batch_id)
            item_rows = connection.execute(
                "SELECT data FROM export_items WHERE batch_id = ? ORDER BY position",
                (batch_id,),
            ).fetchall()
        finally:
            if owns_connection:
                connection.close()
        batch = ExportBatch.model_validate_json(row["data"])
        return batch.model_copy(update={
            "items": [ExportItem.model_validate_json(item["data"]) for item in item_rows],
        })

    def list_export_batches(self, workflow_run_id: str) -> list[ExportBatch]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM export_batches WHERE workflow_run_id = ? "
                "ORDER BY created_at DESC", (workflow_run_id,),
            ).fetchall()
        return [self.get_export_batch(row["id"]) for row in rows]

    def list_incomplete_export_batches(self) -> list[ExportBatch]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM export_batches WHERE status IN ('queued', 'running') "
                "ORDER BY created_at"
            ).fetchall()
        return [self.get_export_batch(row["id"]) for row in rows]

    def update_export_batch(
        self, batch_id: str, *, connection: sqlite3.Connection, **changes: object,
    ) -> ExportBatch:
        current = self.get_export_batch(batch_id, connection=connection)
        updated = current.model_copy(update={**changes, "updated_at": utc_now()})
        self._save_export_batch(connection, updated)
        return updated

    def update_export_item(
        self, item_id: str, *, connection: sqlite3.Connection, **changes: object,
    ) -> ExportItem:
        current = self.get_export_item(item_id, connection=connection)
        updated = current.model_copy(update={**changes, "updated_at": utc_now()})
        self._save_export_item(connection, updated)
        return updated

    # -- Transcript artifacts -----------------------------------------

    def create_transcript_artifact(self, artifact: TranscriptArtifact) -> TranscriptArtifact:
        payload = json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO transcript_artifacts
                   (id, project_id, source_asset_id, data, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (artifact.id, artifact.project_id, artifact.source_asset_id, payload,
                 artifact.created_at.isoformat()),
            )
        return artifact

    def list_transcript_artifacts(self, source_asset_id: str) -> list[TranscriptArtifact]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT data FROM transcript_artifacts
                   WHERE source_asset_id = ? ORDER BY created_at DESC""",
                (source_asset_id,),
            ).fetchall()
        return [TranscriptArtifact.model_validate_json(row["data"]) for row in rows]

    def get_transcript_artifact(self, artifact_id: str) -> TranscriptArtifact:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM transcript_artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            raise TranscriptArtifactNotFoundError(artifact_id)
        return TranscriptArtifact.model_validate_json(row["data"])

    def find_cached_transcript_artifact(
        self,
        *,
        source_asset_id: str,
        audio_sha256: str,
        model: str,
        device: str,
        compute_type: str,
        language: str | None,
        vad: bool,
        schema_version: int,
    ) -> TranscriptArtifact | None:
        for artifact in self.list_transcript_artifacts(source_asset_id):
            if (
                artifact.audio_sha256 == audio_sha256
                and artifact.model == model
                and artifact.device == device
                and artifact.compute_type == compute_type
                and artifact.language == language
                and artifact.vad == vad
                and artifact.schema_version == schema_version
                and Path(artifact.path).exists()
            ):
                return artifact
        return None

    # -- Stage artifacts (generic) -------------------------------------

    def create_stage_artifact(self, artifact: StageArtifact) -> StageArtifact:
        payload = json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO stage_artifacts
                   (id, project_id, stage, input_hash, schema_version, path, data, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (artifact.id, artifact.project_id, artifact.stage, artifact.input_hash,
                 artifact.schema_version, artifact.path, payload, artifact.created_at.isoformat()),
            )
        return artifact

    def list_stage_artifacts(self, project_id: str, stage: str | None = None) -> list[StageArtifact]:
        with self._connect() as connection:
            if stage is None:
                rows = connection.execute(
                    "SELECT data FROM stage_artifacts WHERE project_id = ? ORDER BY created_at DESC",
                    (project_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT data FROM stage_artifacts WHERE project_id = ? AND stage = ?
                       ORDER BY created_at DESC""",
                    (project_id, stage),
                ).fetchall()
        return [StageArtifact.model_validate_json(row["data"]) for row in rows]

    def get_stage_artifact(self, artifact_id: str) -> StageArtifact:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM stage_artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            raise StageArtifactNotFoundError(artifact_id)
        return StageArtifact.model_validate_json(row["data"])

    def find_cached_stage_artifact(
        self, *, project_id: str, stage: str, input_hash: str, schema_version: int
    ) -> StageArtifact | None:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT data FROM stage_artifacts
                   WHERE project_id = ? AND stage = ? AND input_hash = ?
                     AND schema_version = ? ORDER BY created_at DESC""",
                (project_id, stage, input_hash, schema_version),
            ).fetchall()
        for row in rows:
            artifact = StageArtifact.model_validate_json(row["data"])
            if Path(artifact.path).exists():
                return artifact
        return None

    # -- Render presets -------------------------------------------------

    def create_render_preset(self, preset: RenderPreset) -> RenderPreset:
        payload = json.dumps(preset.model_dump(mode="json"), ensure_ascii=False)
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO render_presets
                       (id, project_id, name, data, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        preset.id,
                        preset.project_id,
                        preset.name,
                        payload,
                        preset.created_at.isoformat(),
                        preset.updated_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            if "render_presets.project_id, render_presets.name" in str(error):
                raise RenderPresetNameConflictError(
                    f"render preset named {preset.name!r} already exists for project {preset.project_id}"
                ) from error
            raise
        return preset

    def list_render_presets(self, project_id: str) -> list[RenderPreset]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT data FROM render_presets WHERE project_id = ?
                   ORDER BY created_at DESC""",
                (project_id,),
            ).fetchall()
        return [RenderPreset.model_validate_json(row["data"]) for row in rows]

    def get_render_preset(self, project_id: str, preset_id: str) -> RenderPreset:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM render_presets WHERE id = ? AND project_id = ?",
                (preset_id, project_id),
            ).fetchone()
        if row is None:
            raise RenderPresetNotFoundError(preset_id)
        return RenderPreset.model_validate_json(row["data"])

    def update_render_preset(
        self,
        project_id: str,
        preset_id: str,
        *,
        name: str | None = None,
        settings: dict | None = None,
    ) -> RenderPreset:
        current = self.get_render_preset(project_id, preset_id)
        changes: dict[str, object] = {"updated_at": utc_now()}
        if name is not None:
            changes["name"] = name
        if settings is not None:
            changes["settings"] = settings
        updated = RenderPreset.model_validate({
            **current.model_dump(),
            **changes,
        })
        payload = json.dumps(updated.model_dump(mode="json"), ensure_ascii=False)
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """UPDATE render_presets
                       SET name = ?, data = ?, updated_at = ?
                       WHERE id = ? AND project_id = ?""",
                    (
                        updated.name,
                        payload,
                        updated.updated_at.isoformat(),
                        preset_id,
                        project_id,
                    ),
                )
        except sqlite3.IntegrityError as error:
            if "render_presets.project_id, render_presets.name" in str(error):
                raise RenderPresetNameConflictError(
                    f"render preset named {updated.name!r} already exists for project {project_id}"
                ) from error
            raise
        if cursor.rowcount != 1:
            raise RenderPresetNotFoundError(preset_id)
        return updated

    # -- Clip publications ----------------------------------------------

    def upsert_clip_publication(self, publication: ClipPublication) -> ClipPublication:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, created_at FROM clip_publications WHERE render_artifact_id = ?",
                (publication.render_artifact_id,),
            ).fetchone()
            if row is None:
                saved = publication
                connection.execute(
                    """INSERT INTO clip_publications
                       (id, project_id, render_artifact_id, data, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        saved.id, saved.project_id, saved.render_artifact_id,
                        json.dumps(saved.model_dump(mode="json"), ensure_ascii=False),
                        saved.created_at.isoformat(), saved.updated_at.isoformat(),
                    ),
                )
                return saved
            saved = publication.model_copy(update={
                "id": row["id"],
                "created_at": datetime.fromisoformat(row["created_at"]),
                "updated_at": utc_now(),
            })
            connection.execute(
                "UPDATE clip_publications SET data = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(saved.model_dump(mode="json"), ensure_ascii=False),
                    saved.updated_at.isoformat(), saved.id,
                ),
            )
            return saved

    def get_clip_publication(self, project_id: str, render_artifact_id: str) -> ClipPublication:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM clip_publications WHERE project_id = ? AND render_artifact_id = ?",
                (project_id, render_artifact_id),
            ).fetchone()
        if row is None:
            raise ClipPublicationNotFoundError(render_artifact_id)
        return ClipPublication.model_validate_json(row["data"])

    def delete_clip_publication(self, project_id: str, render_artifact_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM clip_publications WHERE project_id = ? AND render_artifact_id = ?",
                (project_id, render_artifact_id),
            )
        if cursor.rowcount != 1:
            raise ClipPublicationNotFoundError(render_artifact_id)

    def list_clip_publications(
        self, *, project_id: str | None = None, limit: int = 200,
    ) -> list[ClipPublication]:
        query = "SELECT data FROM clip_publications"
        params: list[object] = []
        if project_id is not None:
            query += " WHERE project_id = ?"
            params.append(project_id)
        query += " ORDER BY updated_at DESC, id LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [ClipPublication.model_validate_json(row["data"]) for row in rows]

    # -- Clip states ----------------------------------------------------

    def upsert_clip_state(self, state: ClipState) -> ClipState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, created_at FROM clip_states WHERE workflow_run_id = ? AND clip_key = ?",
                (state.workflow_run_id, state.clip_key),
            ).fetchone()
            if row is None:
                saved = state
                connection.execute(
                    """INSERT INTO clip_states
                       (id, project_id, workflow_run_id, clip_key, data, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        saved.id, saved.project_id, saved.workflow_run_id, saved.clip_key,
                        json.dumps(saved.model_dump(mode="json"), ensure_ascii=False),
                        saved.created_at.isoformat(), saved.updated_at.isoformat(),
                    ),
                )
                return saved
            saved = state.model_copy(update={
                "id": row["id"],
                "created_at": datetime.fromisoformat(row["created_at"]),
                "updated_at": utc_now(),
            })
            connection.execute(
                "UPDATE clip_states SET data = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(saved.model_dump(mode="json"), ensure_ascii=False),
                    saved.updated_at.isoformat(), saved.id,
                ),
            )
            return saved

    def list_clip_states(self, project_ids: list[str]) -> list[ClipState]:
        if not project_ids:
            return []
        placeholders = ",".join("?" for _ in project_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT data FROM clip_states WHERE project_id IN ({placeholders}) "
                "ORDER BY updated_at DESC, id",
                project_ids,
            ).fetchall()
        return [ClipState.model_validate_json(row["data"]) for row in rows]
