from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from cortex.analyze.schemas import AnalysisDocument
from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.edit.boundary import energy_track_from_analysis
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
    ) -> dict:
        if should_cancel():
            raise EditPlanJobCancelled()
        if end <= start:
            raise EditPlanPreconditionError("end deve ser maior que start")
        if analysis_artifact.stage != "analysis":
            raise EditPlanPreconditionError("artifact informado não é um AnalysisArtifact")
        if transcript_artifact.project_id != analysis_artifact.project_id:
            raise EditPlanPreconditionError("transcript e analysis não pertencem ao mesmo projeto")

        analysis_path = Path(analysis_artifact.path)
        if not analysis_path.exists():
            raise EditPlanPreconditionError("arquivo do AnalysisArtifact não está disponível")
        transcript_path = Path(transcript_artifact.path)
        if not transcript_path.exists():
            raise EditPlanPreconditionError("arquivo do transcript não está disponível")

        progress_cb(0.0, "Lendo transcript e análise")
        analysis = AnalysisDocument.model_validate_json(analysis_path.read_text(encoding="utf-8"))
        if analysis.transcript_artifact_id != transcript_artifact.id:
            raise EditPlanPreconditionError("AnalysisArtifact não corresponde a este transcript")
        transcript = TranscriptDocument.model_validate_json(transcript_path.read_text(encoding="utf-8"))

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

        document = EditPlanDocument(
            project_id=transcript_artifact.project_id, source_asset_id=transcript_artifact.source_asset_id,
            transcript_artifact_id=transcript_artifact.id, analysis_artifact_id=analysis_artifact.id,
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
            },
        ))
        progress_cb(100.0, "Plano de edição concluído")
        return {"cached": False, "edit_plan_artifact_id": artifact.id, "edit_plan_path": artifact.path, "schema_version": artifact.schema_version}
