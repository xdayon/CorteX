"""Automatic triangulation: the scene suggestions of a clip become its inserts unless the
person chose them by hand.

``RenderSettings.inserts`` is ``None`` for a clip whose scenes were never edited. The
render worker resolves it here, before the cache key, so the draft preview and the
export get the same inserts and the render artifact records them. Roles confirmed on the
"Câmeras" screen win; without them the roles suggested from the voice share and face
counts are used, and an undecided group is ignored (never used as footage or as base).
Deterministic and cheap: no media is decoded.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cortex.analyze.camera_map import CameraMapDocument, read_document, suggest_role
from cortex.analyze.scene_suggestions import SCENE_SUGGESTIONS_VERSION, SceneSuggestions, suggest_scenes
from cortex.config import CortexConfig
from cortex.domain.store import DomainStore
from cortex.edit.schemas import EditPlanDocument
from cortex.render.schemas import RenderInsert

Status = Literal["ok", "needs_camera_map", "needs_voice", "no_episode"]
RolesSource = Literal["confirmed", "suggested"]


class TriangulationError(RuntimeError):
    """The episode data exists but cannot be read."""


@dataclass(frozen=True)
class SceneContext:
    status: Status
    camera_map_artifact_id: str | None = None
    roles_source: RolesSource | None = None
    suggestions: SceneSuggestions | None = None

    def summary(self, inserts: int | None = None) -> dict:
        """What the render job and the artifact record about the automatic pick."""
        return {
            "mode": "auto", "status": self.status, "algorithm_version": SCENE_SUGGESTIONS_VERSION,
            "camera_map_artifact_id": self.camera_map_artifact_id, "roles_source": self.roles_source,
            **({"inserts": inserts} if inserts is not None else {}),
        }


def effective_roles(camera_roles: dict, artifact_id: str, document: CameraMapDocument) -> tuple[dict[int, str], RolesSource]:
    confirmed = camera_roles.get("roles") if camera_roles.get("artifact_id") == artifact_id else None
    if confirmed:
        return {int(key): value for key, value in confirmed.items()}, "confirmed"
    return {
        group.id: suggest_role(group.faces, group.subject_voice_share, group.airtime_share) or "ignore"
        for group in document.groups
    }, "suggested"


def camera_map_for(domain: DomainStore, project_ids: list[str], source_sha256: str | None):
    """Newest camera map of the episode built on the same media as the plan."""
    found = sorted(
        (a for p in project_ids for a in domain.list_stage_artifacts(p, stage="camera_map") if Path(a.path).is_file()),
        key=lambda a: a.created_at, reverse=True)
    for artifact in found:
        document = read_document(artifact)
        if source_sha256 is None or document.source_sha256 == source_sha256:
            return artifact, document
    return None, None


def scenes_for_plan(
    domain: DomainStore, episode, plan: EditPlanDocument, config: CortexConfig | None = None,
) -> SceneContext:
    """With ``config`` the borrowed footage is decoded and checked (``FootageChecker``);
    the API and the render worker always pass it, so both see the same suggestions."""
    from cortex.analyze.footage_check import FootageChecker
    from cortex.diarize.service import DiarizationDocument
    from cortex.diarize.voice_match import SUBJECT_LABEL
    from cortex.transcribe.schemas import TranscriptDocument

    if episode is None:
        return SceneContext("no_episode")
    try:
        source = domain.get_source_asset(plan.source_asset_id)
        artifact, document = camera_map_for(domain, episode.project_ids, source.sha256)
        if artifact is None:
            return SceneContext("needs_camera_map")
        if not document.voice_artifact_id:
            return SceneContext("needs_voice", artifact.id)
        roles, roles_source = effective_roles(episode.camera_roles or {}, artifact.id, document)
        voice = domain.get_stage_artifact(document.voice_artifact_id)
        turns = DiarizationDocument.model_validate_json(Path(voice.path).read_text()).turns
        transcript = TranscriptDocument.model_validate_json(
            Path(domain.get_transcript_artifact(plan.transcript_artifact_id).path).read_text())
    except (ValueError, LookupError, OSError) as exc:
        raise TriangulationError(f"dados do episódio ilegíveis: {exc}"[:300]) from exc
    words = [word for segment in transcript.segments for word in segment.words]
    verify = None
    if config is not None:
        media = config.paths.data_dir / source.stored_path
        verify = FootageChecker(config, document, artifact.id, media).verifier(roles)
    result = suggest_scenes(plan, document, roles, turns, words, SUBJECT_LABEL, verify=verify)
    return SceneContext("ok", artifact.id, roles_source, result)


def auto_inserts(context: SceneContext) -> list[RenderInsert]:
    """Every suggestion with its first pick: the suggestions already are the composition."""
    if context.suggestions is None:
        return []
    return [
        RenderInsert(kind=s.kind, anchor=s.anchor, duration=s.duration, source_start=s.source_start,
                     group=s.group, suggestion_id=s.id)
        for s in context.suggestions.suggestions
    ]
