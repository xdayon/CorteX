"""HTTP contracts for clip publication records (the editorial learning loop)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from cortex.domain.models import ClipPublication
from cortex.domain.store import ClipPublicationNotFoundError, DomainStore, StageArtifactNotFoundError


class PublicationUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    headline: str | None = Field(default=None, max_length=300)
    hook_text: str = Field(default="", max_length=200)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    published: bool = False
    platforms: list[Literal["instagram", "tiktok"]] = Field(default_factory=list)
    published_on: date | None = None
    url: str | None = Field(default=None, max_length=500, pattern=r"^https?://\S+$")
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    retention_3s_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    avg_watch_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    rating: Literal[-1, 0, 1] | None = None
    notes: str = Field(default="", max_length=300)


def _manifest_defaults(manifest_path: str) -> dict[str, Any]:
    try:
        document = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    settings = document.get("requested_settings") or {}
    headline = ((settings.get("headline") or {}).get("text") or "").strip()
    return {
        "headline": headline,
        "duration_seconds": float(document.get("timeline_duration_seconds") or 0),
    }


def create_publication_router(prefix: str, domain: DomainStore) -> APIRouter:
    router = APIRouter(prefix=prefix)

    def render_artifact(project_id: str, artifact_id: str):
        try:
            artifact = domain.get_stage_artifact(artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Render não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "render":
            raise HTTPException(status_code=404, detail="Render não encontrado")
        return artifact

    @router.put("/projects/{project_id}/renders/{artifact_id}/publication")
    def put_publication(project_id: str, artifact_id: str, request: PublicationUpsert):
        artifact = render_artifact(project_id, artifact_id)
        defaults = _manifest_defaults(artifact.path)
        headline = request.headline if request.headline is not None else defaults.get("headline", "")
        duration = (
            request.duration_seconds if request.duration_seconds is not None
            else defaults.get("duration_seconds", 0.0)
        )
        title = request.title or headline or f"Corte {artifact.id[:8]}"
        publication = ClipPublication(
            project_id=project_id, render_artifact_id=artifact_id, title=title,
            headline=headline, duration_seconds=duration,
            **request.model_dump(exclude={"title", "headline", "duration_seconds"}),
        )
        return domain.upsert_clip_publication(publication)

    @router.get("/projects/{project_id}/renders/{artifact_id}/publication")
    def get_publication(project_id: str, artifact_id: str):
        try:
            return domain.get_clip_publication(project_id, artifact_id)
        except ClipPublicationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Publicação não registrada") from exc

    @router.delete("/projects/{project_id}/renders/{artifact_id}/publication", status_code=204)
    def delete_publication(project_id: str, artifact_id: str):
        try:
            domain.delete_clip_publication(project_id, artifact_id)
        except ClipPublicationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Publicação não registrada") from exc

    @router.get("/publications")
    def list_publications(limit: int = 100):
        return domain.list_clip_publications(limit=max(1, min(limit, 500)))

    return router
