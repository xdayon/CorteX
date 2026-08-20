from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from cortex.domain.models import (
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


class WorkflowRunNotFoundError(LookupError):
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

    def _initialize(self) -> None:
        with self._connect() as connection:
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
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
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
                """CREATE INDEX IF NOT EXISTS idx_workflow_runs_project_created
                   ON workflow_runs (project_id, created_at DESC)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_render_presets_project_created
                   ON render_presets (project_id, created_at DESC)"""
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
                "INSERT INTO source_assets (id, project_id, data, created_at) VALUES (?, ?, ?, ?)",
                (asset.id, asset.project_id, payload, asset.created_at.isoformat()),
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
        for asset in self.list_source_assets(project_id):
            if asset.sha256 == sha256:
                return asset
        return None

    # -- Workflow runs -------------------------------------------------

    def create_workflow_run(self, run: WorkflowRun) -> WorkflowRun:
        payload = json.dumps(run.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as connection:
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

    def update_workflow_run(self, run_id: str, **changes: object) -> WorkflowRun:
        current = self.get_workflow_run(run_id)
        changes["updated_at"] = utc_now()
        updated = WorkflowRun.model_validate({**current.model_dump(), **changes})
        payload = json.dumps(updated.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE workflow_runs SET status = ?, data = ?, updated_at = ?
                   WHERE id = ?""",
                (updated.status.value, payload, updated.updated_at.isoformat(), run_id),
            )
        if cursor.rowcount != 1:
            raise WorkflowRunNotFoundError(run_id)
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
                "INSERT INTO stage_artifacts (id, project_id, stage, data, created_at) VALUES (?, ?, ?, ?, ?)",
                (artifact.id, artifact.project_id, artifact.stage, payload, artifact.created_at.isoformat()),
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
        for artifact in self.list_stage_artifacts(project_id, stage):
            if (
                artifact.input_hash == input_hash
                and artifact.schema_version == schema_version
                and Path(artifact.path).exists()
            ):
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
