from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from cortex.analyze.schemas import AnalysisDocument
from cortex.analyze.scene_schemas import SceneIndexDocument
from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.edit.boundary import energy_track_from_analysis, snap_segments_to_scene_cuts
from cortex.edit.planner import plan_safe_segments, protect_segment_boundaries, timeline_duration
from cortex.edit.profiles import resolve_profile
from cortex.edit.quality import validate_edit_plan
from cortex.edit.schemas import (
    EDIT_PLAN_SCHEMA_VERSION,
    EditPlanDocument,
    EditPlanDiagnostics,
    EditQualityReport,
    EditSegment,
    EditTransition,
)
from cortex.paths import edit_plans_dir
from cortex.transcribe.schemas import TranscriptDocument, TranscriptWord


class EditPlanJobCancelled(RuntimeError):
    pass


class EditPlanPreconditionError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _flatten_words(transcript: TranscriptDocument) -> list[TranscriptWord]:
    words: list[TranscriptWord] = []
    for segment in transcript.segments:
        words.extend(segment.words)
    return sorted(words, key=lambda w: w.start)


def _segment_to_schema(segment: dict) -> EditSegment:
    transition = segment.get("transition")
    return EditSegment(
        start=segment["start"],
        end=segment["end"],
        timeline_order=segment.get("timeline_order", 0),
        transition=EditTransition(**transition) if transition else None,
    )


class EditPlanService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    def run(
        self,
        *,
        transcript_artifact: TranscriptArtifact,
        analysis_artifact: StageArtifact,
        start: float,
        end: float,
        profile: str | None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
        scene_index_artifact: StageArtifact | None = None,
    ) -> dict:
        if should_cancel():
            raise EditPlanJobCancelled()
        if end <= start:
            raise EditPlanPreconditionError("end deve ser maior que start")
        if analysis_artifact.stage != "analysis":
            raise EditPlanPreconditionError("artifact informado não é um AnalysisArtifact")
        if transcript_artifact.project_id != analysis_artifact.project_id:
            raise EditPlanPreconditionError("transcript e analysis não pertencem ao mesmo projeto")
        if scene_index_artifact is not None:
            if scene_index_artifact.stage != "scene_index":
                raise EditPlanPreconditionError("artifact informado não é um SceneIndexArtifact")
            if scene_index_artifact.project_id != transcript_artifact.project_id:
                raise EditPlanPreconditionError("scene_index não pertence ao mesmo projeto")

        analysis_path = Path(analysis_artifact.path)
        if not analysis_path.exists():
            raise EditPlanPreconditionError("arquivo do AnalysisArtifact não está disponível")
        transcript_path = Path(transcript_artifact.path)
        if not transcript_path.exists():
            raise EditPlanPreconditionError("arquivo do transcript não está disponível")
        scene_index_path: Path | None = None
        if scene_index_artifact is not None:
            scene_index_path = Path(scene_index_artifact.path)
            if not scene_index_path.exists():
                raise EditPlanPreconditionError("arquivo do SceneIndexArtifact não está disponível")

        progress_cb(0.0, "Lendo transcript e análise")
        analysis = AnalysisDocument.model_validate_json(analysis_path.read_text(encoding="utf-8"))
        if analysis.transcript_artifact_id != transcript_artifact.id:
            raise EditPlanPreconditionError("AnalysisArtifact não corresponde a este transcript")
        transcript = TranscriptDocument.model_validate_json(transcript_path.read_text(encoding="utf-8"))
        scene_index: SceneIndexDocument | None = None
        if scene_index_path is not None:
            scene_index = SceneIndexDocument.model_validate_json(scene_index_path.read_text(encoding="utf-8"))
            if scene_index.source_asset_id != transcript_artifact.source_asset_id:
                raise EditPlanPreconditionError("SceneIndexArtifact não corresponde à fonte deste transcript")

        requested_profile = (profile or "auto").strip().lower()
        transcript_hash = _sha256(transcript_path)
        analysis_hash = _sha256(analysis_path)
        hash_payload = {
            "schema_version": EDIT_PLAN_SCHEMA_VERSION,
            "transcript_artifact_id": transcript_artifact.id,
            "analysis_artifact_id": analysis_artifact.id,
            "transcript_sha256": transcript_hash,
            "analysis_sha256": analysis_hash,
            "clip_start": round(start, 4),
            "clip_end": round(end, 4),
            "profile": requested_profile,
        }
        # Absent a scene_index, the hash payload — and thus the cache key —
        # is identical to before this gate: EDLs stay byte-for-byte the same
        # when scene snapping does not participate.
        if scene_index_artifact is not None and scene_index_path is not None:
            hash_payload["scene_index_artifact_id"] = scene_index_artifact.id
            hash_payload["scene_index_sha256"] = _sha256(scene_index_path)
            hash_payload["scene_snap_tolerance_seconds"] = round(
                self._config.edit.scene_snap_tolerance_seconds, 4
            )
        input_hash = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        cached = self._domain.find_cached_stage_artifact(
            project_id=transcript_artifact.project_id, stage="edit_plan", input_hash=input_hash,
            schema_version=EDIT_PLAN_SCHEMA_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Plano de edição em cache reutilizado")
            return {"cached": True, "edit_plan_artifact_id": cached.id, "edit_plan_path": cached.path, "schema_version": cached.schema_version}

        if should_cancel():
            raise EditPlanJobCancelled()
        progress_cb(20.0, "Resolvendo perfil de ritmo")
        words = _flatten_words(transcript)
        resolved_profile = resolve_profile(requested_profile, words, start, end)
        energy = energy_track_from_analysis(analysis)

        if should_cancel():
            raise EditPlanJobCancelled()
        progress_cb(45.0, "Planejando cortes seguros")
        segments, diagnostics = plan_safe_segments(
            words, start, end, resolved_profile, energy, vad_intervals=analysis.vad_intervals,
        )
        segments = protect_segment_boundaries(segments, words, resolved_profile)

        if should_cancel():
            raise EditPlanJobCancelled()
        progress_cb(70.0, "Validando fronteiras do plano")
        segments, quality = validate_edit_plan(
            segments, words, resolved_profile, start, end, energy,
            vad_intervals=analysis.vad_intervals,
        )
        if quality["degraded"]:
            diagnostics = {
                "profile": resolved_profile.name, "waveform_used": diagnostics["waveform_used"],
                "candidate_pauses": diagnostics["candidate_pauses"], "cuts": 0,
                "saved_seconds": 0.0, "crossfade": resolved_profile.crossfade,
                "vad_used": diagnostics["vad_used"],
            }

        scene_snap_count = 0
        if scene_index is not None:
            if should_cancel():
                raise EditPlanJobCancelled()
            progress_cb(85.0, "Ajustando fronteiras aos cortes de cena")
            cuts = [cut.time for cut in scene_index.cuts]
            segments, scene_issues = snap_segments_to_scene_cuts(
                segments, words, [(v.start, v.end) for v in analysis.vad_intervals],
                cuts, self._config.edit.scene_snap_tolerance_seconds,
            )
            scene_snap_count = len(scene_issues)
            quality = {**quality, "issues": [*quality["issues"], *scene_issues]}
        diagnostics = {**diagnostics, "scene_snap_count": scene_snap_count}

        document = EditPlanDocument(
            project_id=transcript_artifact.project_id, source_asset_id=transcript_artifact.source_asset_id,
            transcript_artifact_id=transcript_artifact.id, analysis_artifact_id=analysis_artifact.id,
            scene_index_artifact_id=scene_index_artifact.id if scene_index_artifact is not None else None,
            input_hash=input_hash, clip_start=round(start, 4), clip_end=round(end, 4),
            profile=resolved_profile.name,
            segments=[_segment_to_schema(segment) for segment in segments],
            timeline_duration_seconds=round(
                timeline_duration(segments, resolved_profile.crossfade), 4
            ),
            diagnostics=EditPlanDiagnostics(**diagnostics),
            quality=EditQualityReport(**quality),
        )

        if should_cancel():
            raise EditPlanJobCancelled()
        progress_cb(95.0, "Persistindo EditPlanArtifact")
        out_dir = edit_plans_dir(self._config, transcript_artifact.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"edit-plan-{input_hash[:16]}.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        if should_cancel():
            temporary_path.unlink(missing_ok=True)
            raise EditPlanJobCancelled()
        temporary_path.replace(output_path)
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=transcript_artifact.project_id, stage="edit_plan",
            schema_version=EDIT_PLAN_SCHEMA_VERSION, path=str(output_path), input_hash=input_hash,
            metadata={
                "transcript_artifact_id": transcript_artifact.id,
                "analysis_artifact_id": analysis_artifact.id,
                "requested_profile": requested_profile,
                "effective_profile": resolved_profile.name,
                "quality_passed": quality["passed"],
                "degraded": quality["degraded"],
                "scene_index_artifact_id": scene_index_artifact.id if scene_index_artifact is not None else None,
                "scene_snap_count": scene_snap_count,
            },
        ))
        progress_cb(100.0, "Plano de edição concluído")
        return {"cached": False, "edit_plan_artifact_id": artifact.id, "edit_plan_path": artifact.path, "schema_version": artifact.schema_version}
