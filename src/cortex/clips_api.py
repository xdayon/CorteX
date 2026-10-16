"""HTTP contracts for the integrated clip view and the manual clip state."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from cortex.clips import CLIP_STATUSES, clip_key, collect_clips, suggested_clips
from cortex.domain.models import ClipDecision, ClipState
from cortex.config import CortexConfig
from cortex.domain.store import DomainStore, SourceAssetNotFoundError, WorkflowRunNotFoundError
from cortex.library import EpisodeLibrary
from cortex.paths import cache_dir, source_dir
from cortex.render.quick_preview import QuickPreviewError, build_quick_preview
from cortex.schemas import utc_now


class ClipStatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    run_id: str
    clip_key: str = Field(min_length=1, max_length=200)
    decision: ClipDecision | None = None
    discard_reason: Literal[
        "weak_hook", "off_topic", "repeated", "bad_edit", "technical", "other"
    ] | None = None
    saved: bool | None = None
    notes: str | None = Field(default=None, max_length=300)


class ClipStatesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ClipStatePatch] = Field(min_length=1, max_length=100)


def _last_activity(clip: dict[str, Any]) -> str:
    stamps = [clip["run_created_at"]]
    if clip["state"]:
        stamps.append(clip["state"]["updated_at"])
    if clip["publication"]:
        stamps.append(clip["publication"]["updated_at"])
    stamps.extend(export["exported_at"] for export in clip["exports"])
    return max(stamps)


def _apply_patch(current: ClipState, patch: ClipStatePatch) -> ClipState:
    fields = patch.model_fields_set
    changes: dict[str, Any] = {}
    if "decision" in fields:
        changes["decision"] = patch.decision
    if "discard_reason" in fields:
        changes["discard_reason"] = patch.discard_reason
    decision = changes.get("decision", current.decision)
    if decision != ClipDecision.DISCARDED:
        if changes.get("discard_reason") is not None:
            raise HTTPException(
                status_code=422, detail="discard_reason só vale para cortes descartados",
            )
        changes["discard_reason"] = None
    if "saved" in fields and patch.saved is not None and patch.saved != current.saved:
        changes["saved"] = patch.saved
        changes["saved_at"] = utc_now() if patch.saved else None
    if "notes" in fields and patch.notes is not None:
        changes["notes"] = patch.notes
    return current.model_copy(update=changes)


def create_clips_router(
    prefix: str, domain: DomainStore, library: EpisodeLibrary,
    config: CortexConfig | None = None,
) -> APIRouter:
    router = APIRouter(prefix=prefix)

    def all_clips(include_archived: bool) -> list[dict[str, Any]]:
        rows = []
        for entry in library.entries(include_archived=include_archived):
            for clip in collect_clips(domain, entry.project_ids, prefix)["clips"]:
                rows.append({**clip, "episode_id": entry.id, "episode_title": entry.title})
        return rows

    @router.get("/episodes/{episode_id}/clips")
    def episode_clips(episode_id: str):
        try:
            entry = library.get(episode_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return collect_clips(domain, entry.project_ids, prefix)

    @router.get("/clips")
    def list_clips(
        status: str | None = None,
        include_archived: bool = False,
        limit: int = Query(default=200, ge=1, le=1000),
    ):
        wanted = {part.strip() for part in status.split(",") if part.strip()} if status else set()
        if wanted - set(CLIP_STATUSES):
            raise HTTPException(status_code=422, detail="Status de corte inválido")
        rows = [row for row in all_clips(include_archived)
                if not wanted or row["status"] in wanted]
        rows.sort(key=_last_activity, reverse=True)
        return {"clips": rows[:limit], "total": len(rows)}

    @router.get("/clips/summary")
    def clips_summary():
        return {"episodes": {
            entry.id: collect_clips(domain, entry.project_ids, prefix)["counts"]
            for entry in library.entries()
        }}

    @router.patch("/clip-states")
    def patch_clip_states(request: ClipStatesPatch):
        keys_by_run: dict[str, set[str]] = {}
        existing = {}
        for patch in request.items:
            if patch.run_id not in keys_by_run:
                keys_by_run[patch.run_id] = _run_keys(patch)
                for state in domain.list_clip_states([patch.project_id]):
                    existing[(state.workflow_run_id, state.clip_key)] = state
            if patch.clip_key not in keys_by_run[patch.run_id]:
                raise HTTPException(status_code=404, detail="Corte não existe nesta execução")
        updated = {}
        for patch in request.items:
            slot = (patch.run_id, patch.clip_key)
            base = updated.get(slot) or existing.get(slot) or ClipState(
                project_id=patch.project_id, workflow_run_id=patch.run_id,
                clip_key=patch.clip_key,
            )
            updated[slot] = _apply_patch(base, patch)
        return {"items": [domain.upsert_clip_state(state) for state in updated.values()]}

    def _run_keys(patch: ClipStatePatch) -> set[str]:
        try:
            run = domain.get_workflow_run(patch.run_id)
        except WorkflowRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Execução não encontrada") from exc
        if run.project_id != patch.project_id:
            raise HTTPException(status_code=404, detail="Execução não encontrada")
        return {clip_key(clip) for clip in suggested_clips(domain, run, [])}

    def _clip_pieces(clip: dict[str, Any]) -> list[tuple[float, float]]:
        pieces = clip.get("pieces") or []
        try:
            if pieces:
                return [(float(piece["start"]), float(piece["end"])) for piece in pieces]
            return [(float(clip["start_second"]), float(clip["end_second"]))]
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Trechos do corte inválidos") from exc

    @router.get("/projects/{project_id}/runs/{run_id}/quick-preview")
    def quick_preview(project_id: str, run_id: str, clip_key_param: str = Query(alias="clip_key", min_length=1, max_length=200)):
        if config is None:
            raise HTTPException(status_code=503, detail="Prévia rápida indisponível")
        try:
            run = domain.get_workflow_run(run_id)
        except WorkflowRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Execução não encontrada") from exc
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Execução não encontrada")
        clip = next((c for c in suggested_clips(domain, run, []) if clip_key(c) == clip_key_param), None)
        if clip is None:
            raise HTTPException(status_code=404, detail="Corte não existe nesta execução")
        try:
            asset = domain.get_source_asset(run.source_asset_id)
        except SourceAssetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Fonte não encontrada") from exc
        root = source_dir(config, project_id).resolve()
        source_path = (config.paths.data_dir / asset.stored_path).resolve()
        if asset.project_id != project_id or not source_path.is_relative_to(root) \
                or not source_path.is_file():
            raise HTTPException(status_code=410, detail="Arquivo da fonte não está disponível")
        try:
            path = build_quick_preview(
                config, source_path, asset.sha256, _clip_pieces(clip),
                cache_dir(config, project_id) / "quick-previews",
            )
        except QuickPreviewError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return FileResponse(path, media_type="video/mp4")

    return router
