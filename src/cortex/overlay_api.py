"""Overlay preview API: the exact ASS/pagination the renderer burns, without FFmpeg."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from cortex.config import CortexConfig
from cortex.domain.store import (
    DomainStore,
    ProjectNotFoundError,
    StageArtifactNotFoundError,
    TranscriptArtifactNotFoundError,
)
from cortex.edit.schemas import EditPlanDocument
from cortex.render.ass_overlay import (
    AssOverlayError,
    ResolvedFont,
    allowed_font_files,
    build_ass_overlay,
)
from cortex.render.safe_zones import PLATFORM_OCCLUSIONS, SAFE_ZONES_VERSION
from cortex.render.schemas import RenderSettings
from cortex.render.service import (
    build_plan_overlay,
    effective_headline_settings,
    normalize_headline_text,
)
from cortex.transcribe.schemas import TranscriptDocument


class OverlayPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edit_plan_artifact_id: str
    # Validated by hand so the 422 detail is readable (PT-BR) for the editor.
    settings: dict[str, Any]


def _fonts_dir(config: CortexConfig) -> Path:
    fonts_dir = Path(config.render.fonts_dir)
    if not fonts_dir.is_absolute():
        fonts_dir = Path(__file__).resolve().parents[2] / fonts_dir
    return fonts_dir


def create_overlay_router(prefix: str, config: CortexConfig, domain: DomainStore) -> APIRouter:
    router = APIRouter(prefix=prefix)
    fonts_dir = _fonts_dir(config)

    def font_entry(font: ResolvedFont | None) -> dict[str, str] | None:
        if font is None:
            return None
        return {"ass_name": font.ass_name, "url": f"{prefix}/overlay-fonts/{font.filename}"}

    @router.post("/projects/{project_id}/overlay-preview")
    def overlay_preview(project_id: str, request: OverlayPreviewRequest):
        try:
            domain.get_project(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc
        try:
            artifact = domain.get_stage_artifact(request.edit_plan_artifact_id)
        except StageArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Plano de edição não encontrado") from exc
        if artifact.project_id != project_id or artifact.stage != "edit_plan":
            raise HTTPException(status_code=422, detail="Plano de edição não pertence ao projeto")
        try:
            plan = EditPlanDocument.model_validate_json(
                Path(artifact.path).read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail="Plano de edição indisponível") from exc
        try:
            transcript_artifact = domain.get_transcript_artifact(plan.transcript_artifact_id)
            transcript = TranscriptDocument.model_validate_json(
                Path(transcript_artifact.path).read_text(encoding="utf-8")
            )
        except TranscriptArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Transcrição do plano não encontrada") from exc
        except (OSError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail="Transcrição do plano indisponível") from exc
        if transcript_artifact.project_id != project_id:
            raise HTTPException(status_code=422, detail="Transcrição não pertence ao projeto")

        try:
            requested = RenderSettings.model_validate(request.settings)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            raise HTTPException(
                status_code=422, detail=f"Configurações de render inválidas: {problems}"
            ) from exc
        headline_text = normalize_headline_text(requested.headline.text)
        settings = requested.model_copy(update={
            "headline": effective_headline_settings(
                requested.headline, headline_text, plan.timeline_duration_seconds
            ),
        })
        try:
            overlay = build_plan_overlay(
                plan=plan, transcript=transcript, settings=settings, fonts_dir=fonts_dir,
                headline_text=headline_text, needs_cues=settings.captions.enabled,
                build_ass=True,
            )
            ass = overlay.ass or build_ass_overlay(
                settings, [], fonts_dir=fonts_dir, headline_text=None,
            )
        except AssOverlayError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Configurações inválidas: {exc}") from exc

        emphasis = set(settings.captions.emphasis_word_indices)
        uppercase = settings.captions.uppercase
        # Original (pre-correction) text and recognition confidence let the
        # editor show what was corrected and flag words worth reviewing.
        raw_words = [word for segment in transcript.segments for word in segment.words]
        pages = [
            {
                "start": page.start,
                "end": page.end,
                "lines": [
                    [
                        {
                            "index": word.index,
                            "text": word.text.upper() if uppercase else word.text,
                            "start": word.start,
                            "end": word.end,
                            "emphasis": word.index in emphasis,
                            "original": (
                                raw_words[word.index].word
                                if 0 <= word.index < len(raw_words) else word.text
                            ),
                            "probability": (
                                raw_words[word.index].probability
                                if 0 <= word.index < len(raw_words) else None
                            ),
                        }
                        for word in line
                    ]
                    for line in page.lines
                ],
            }
            for page in ass.pages
        ]
        fonts = [
            entry for entry in (font_entry(ass.caption_font), font_entry(ass.headline_font))
            if entry is not None
        ]
        return {
            "ass_text": ass.ass_text,
            "generator_version": ass.generator_version,
            "content_hash": ass.content_hash,
            "timeline_duration_seconds": plan.timeline_duration_seconds,
            "canvas": {"width": settings.canvas.width, "height": settings.canvas.height},
            "pages": pages,
            "junctions": list(overlay.junctions),
            "fonts": fonts,
        }

    @router.get("/safe-zones")
    def safe_zones():
        return {
            "version": SAFE_ZONES_VERSION,
            "platforms": {
                platform: {name: list(rect) for name, rect in rects.items()}
                for platform, rects in PLATFORM_OCCLUSIONS.items()
            },
        }

    @router.get("/overlay-fonts/{filename}")
    def overlay_font(filename: str):
        # Whitelist of bundled files only; the request never selects a path.
        if filename not in allowed_font_files():
            raise HTTPException(status_code=404, detail="Fonte indisponível")
        path = fonts_dir / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Fonte indisponível")
        return FileResponse(path, media_type="font/otf")

    return router
