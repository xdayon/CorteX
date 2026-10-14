from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from pathlib import Path

from cortex.analyze.schemas import AnalysisDocument
from cortex.analyze.scene_schemas import SceneIndexDocument
from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.diarize.attribution import ConfirmedVoice, subject_share_for_ranges
from cortex.edit.boundary import energy_track_from_analysis, snap_segments_to_scene_cuts
from cortex.edit.planner import (
    build_transition,
    limit_timeline_duration,
    absorb_short_segments,
    merge_touching_segments,
    plan_safe_segments,
    protect_segment_boundaries,
    remove_fillers,
    resolve_jl_cuts,
    timeline_duration,
)
from cortex.edit.profiles import resolve_profile
from cortex.edit.quality import validate_edit_plan
from cortex.edit.schemas import (
    EDIT_PLAN_SCHEMA_VERSION,
    EditPlanDocument,
    EditPlanDiagnostics,
    EditQualityReport,
    EditSegment,
    EditTransition,
    JlSettings,
)
from cortex.ingest.ffprobe import usable_av_duration_seconds
from cortex.paths import edit_plans_dir
from cortex.transcribe.schemas import TranscriptDocument, TranscriptWord


class EditPlanJobCancelled(RuntimeError):
    pass


class EditPlanPreconditionError(ValueError):
    pass


def resolve_semantic_ranges(
    approximate_edl: list[dict] | None, start: float, end: float,
) -> list[tuple[float, float]]:
    return [(a, b) for a, b, _piece in resolve_semantic_pieces(approximate_edl, start, end)]


def _entry_piece(entry: dict) -> int | None:
    value = entry.get("piece", entry.get("order"))
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def resolve_semantic_pieces(
    approximate_edl: list[dict] | None, start: float, end: float,
) -> list[tuple[float, float, int | None]]:
    """Turn ``approximate_edl`` entries into clamped, unioned keep-ranges.

    Requirement R5: a range partly outside the ``[start, end)`` envelope is
    clamped instead of rejected, and dropped if clamping leaves it empty.
    Overlapping (or exactly touching) ranges are unioned instead of raising
    "approximate_edl possui intervalos sobrepostos" — ``approximate_edl`` is
    model output (an AI "tighten" pass proposing several keep-ranges per
    clip), not a hand-authored EDL, so overlap is expected input, not an
    error. Raises only when ``approximate_edl`` was given but nothing usable
    remains.
    """
    raw_ranges: list[tuple[float, float, int | None]] = []
    for entry in approximate_edl or []:
        if entry.get("audio_mode") == "room_tone":
            continue
        try:
            range_start = float(entry["source_start"])
            range_end = float(entry["source_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise EditPlanPreconditionError("approximate_edl possui intervalo inválido") from exc
        if not math.isfinite(range_start) or not math.isfinite(range_end) or range_end <= range_start:
            raise EditPlanPreconditionError("approximate_edl possui intervalo inválido")
        clamped_start = max(range_start, start)
        clamped_end = min(range_end, end)
        if clamped_end <= clamped_start:
            continue
        raw_ranges.append((clamped_start, clamped_end, _entry_piece(entry)))
    raw_ranges.sort(key=lambda item: (item[0], item[1]))
    semantic_ranges: list[tuple[float, float, int | None]] = []
    for range_start, range_end, piece in raw_ranges:
        if semantic_ranges and range_start <= semantic_ranges[-1][1]:
            prev_start, prev_end, prev_piece = semantic_ranges[-1]
            semantic_ranges[-1] = (prev_start, max(prev_end, range_end), prev_piece)
        else:
            semantic_ranges.append((range_start, range_end, piece))
    if approximate_edl is not None and not semantic_ranges:
        raise EditPlanPreconditionError("approximate_edl não possui áudio editorial")
    return semantic_ranges


def _below_minimum(duration: float | None, minimum_seconds: float | None) -> bool:
    return (
        duration is not None and minimum_seconds is not None
        and float(duration) < float(minimum_seconds) - 0.5
    )


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
        audio_start=segment.get("audio_start"),
        audio_end=segment.get("audio_end"),
        timeline_order=segment.get("timeline_order", 0),
        transition=EditTransition(**transition) if transition else None,
        piece_index=segment.get("piece_index"),
        teaser=bool(segment.get("teaser", False)),
    )


class EditPlanService:
    def __init__(self, config: CortexConfig, domain: DomainStore) -> None:
        self._config = config
        self._domain = domain

    @staticmethod
    def _plan_teaser(
        words: list[TranscriptWord], start: float, end: float, profile, energy,
        analysis: AnalysisDocument,
    ) -> list[dict]:
        """Word-safe keep ranges for the teaser phrase, planned like a body piece."""
        planned, _diag = plan_safe_segments(
            words, start, end, profile, energy, vad_intervals=analysis.vad_intervals,
        )
        segments = protect_segment_boundaries(planned, words, profile)
        if len(segments) > 1:
            segments = absorb_short_segments(merge_touching_segments(segments))
        validated: list[dict] = []
        for segment in segments:
            checked, _report = validate_edit_plan(
                [segment], words, profile, float(segment["start"]), float(segment["end"]),
                energy, vad_intervals=analysis.vad_intervals,
            )
            validated.extend(checked)
        for index, segment in enumerate(validated):
            if index < len(validated) - 1:
                segment.setdefault("transition", build_transition(profile))
        return validated

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
        jl_cut: bool | None = None,
        max_jl_offset_seconds: float | None = None,
        maximum_seconds: float | None = None,
        approximate_edl: list[dict] | None = None,
        confirmed_voice: dict | None = None,
        minimum_seconds: float | None = None,
        teaser_range: tuple[float, float] | None = None,
    ) -> dict:
        """``teaser_range`` prepends a repeated phrase (flash-forward) planned like
        a body piece; it is dropped, with the reason recorded in
        ``diagnostics.teaser_dropped``, when it would break the duration ceiling.
        ``minimum_seconds`` never changes the plan (cutting dead time wins);
        a final timeline shorter than it is reported as ``below_minimum``."""
        if should_cancel():
            raise EditPlanJobCancelled()
        if end <= start:
            raise EditPlanPreconditionError("end deve ser maior que start")
        start, end = float(start), float(end)
        if maximum_seconds is not None and (
            not math.isfinite(maximum_seconds) or maximum_seconds <= 0
        ):
            raise EditPlanPreconditionError("maximum_seconds deve ser positivo e finito")
        if maximum_seconds is not None:
            maximum_seconds = float(maximum_seconds)
        if teaser_range is not None:
            teaser_range = (float(teaser_range[0]), float(teaser_range[1]))
            if not all(math.isfinite(v) for v in teaser_range) or not (
                0 <= teaser_range[0] < teaser_range[1]
            ):
                raise EditPlanPreconditionError("teaser inválido: início deve ser menor que o fim")
        semantic_pieces = resolve_semantic_pieces(approximate_edl, start, end)
        semantic_ranges = [(a, b) for a, b, _piece in semantic_pieces]
        voice = ConfirmedVoice.model_validate(confirmed_voice) if confirmed_voice else None
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
        source = self._domain.get_source_asset(transcript_artifact.source_asset_id)
        if source.project_id != transcript_artifact.project_id:
            raise EditPlanPreconditionError("fonte do transcript não pertence ao projeto")
        requested_start, requested_end = start, end
        source_duration = usable_av_duration_seconds(source.probe, transcript.duration_seconds)
        if start >= source_duration:
            raise EditPlanPreconditionError("início do corte excede a duração física da fonte")
        end = min(end, source_duration)
        if end <= start:
            raise EditPlanPreconditionError("corte não possui mídia disponível após limitar à fonte")
        scene_index: SceneIndexDocument | None = None
        if scene_index_path is not None:
            scene_index = SceneIndexDocument.model_validate_json(scene_index_path.read_text(encoding="utf-8"))
            if scene_index.source_asset_id != transcript_artifact.source_asset_id:
                raise EditPlanPreconditionError("SceneIndexArtifact não corresponde à fonte deste transcript")

        requested_profile = (profile or "auto").strip().lower()
        transcript_hash = _sha256(transcript_path)
        analysis_hash = _sha256(analysis_path)

        # Resolved eagerly (before the hash/cache check) because the
        # effective J/L-cut settings below depend on the resolved profile's
        # max_jl_offset, and the cache key must reflect them.
        words = _flatten_words(transcript)
        resolved_profile = resolve_profile(requested_profile, words, start, end)
        energy = energy_track_from_analysis(analysis)

        effective_jl_enabled = (
            self._config.edit.jl_cut_enabled_default if jl_cut is None else bool(jl_cut)
        )
        if effective_jl_enabled:
            requested_by: str = "user" if jl_cut is not None else "profile"
            effective_max_jl_offset = resolved_profile.max_jl_offset
            if max_jl_offset_seconds is not None:
                effective_max_jl_offset = max(
                    0.0, min(float(max_jl_offset_seconds), resolved_profile.max_jl_offset)
                )
        else:
            requested_by = "disabled"
            effective_max_jl_offset = 0.0

        hash_payload = {
            "schema_version": EDIT_PLAN_SCHEMA_VERSION,
            "transcript_artifact_id": transcript_artifact.id,
            "analysis_artifact_id": analysis_artifact.id,
            "transcript_sha256": transcript_hash,
            "analysis_sha256": analysis_hash,
            "clip_start_requested": round(requested_start, 4),
            "clip_end_requested": round(requested_end, 4),
            "clip_start_effective": round(start, 4),
            "clip_end_effective": round(end, 4),
            "source_duration_seconds": round(source_duration, 6),
            "physical_boundary_version": 2,
            "profile": requested_profile,
            "jl_settings": {
                "enabled": effective_jl_enabled,
                "max_offset_seconds": round(effective_max_jl_offset, 4),
                "requested_by": requested_by,
            },
        }
        if maximum_seconds is not None:
            hash_payload["maximum_seconds"] = maximum_seconds
            hash_payload["duration_limit_version"] = 1
        if semantic_ranges:
            hash_payload["semantic_ranges"] = semantic_ranges
            hash_payload["semantic_pieces"] = [piece for _a, _b, piece in semantic_pieces]
            hash_payload["semantic_planner_version"] = 3
        if teaser_range is not None:
            hash_payload["teaser"] = [round(teaser_range[0], 4), round(teaser_range[1], 4)]
            hash_payload["teaser_planner_version"] = 1
        if voice is not None:
            hash_payload["confirmed_voice"] = {
                "speaker": voice.speaker,
                "diarization_artifact_id": voice.diarization_artifact_id,
                "turns": [turn.model_dump(mode="json") for turn in voice.turns],
                "validation_version": 1,
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
            cached_duration = cached.metadata.get("timeline_duration_seconds")
            return {"cached": True, "edit_plan_artifact_id": cached.id, "edit_plan_path": cached.path, "schema_version": cached.schema_version,
                    "duration_limit_applied": cached.metadata.get("duration_limit_applied", False),
                    "timeline_duration_seconds": cached_duration,
                    "below_minimum": _below_minimum(cached_duration, minimum_seconds),
                    "voice_validation": cached.metadata.get("voice_validation"),
                    "teaser_applied": cached.metadata.get("teaser_applied", False),
                    "teaser_dropped": cached.metadata.get("teaser_dropped")}

        if should_cancel():
            raise EditPlanJobCancelled()
        progress_cb(20.0, "Resolvendo perfil de ritmo")

        if should_cancel():
            raise EditPlanJobCancelled()
        progress_cb(45.0, "Planejando cortes seguros")
        planning_ranges = semantic_pieces or [(start, end, None)]
        segments = []
        range_diagnostics = []
        for range_start, range_end, piece in planning_ranges:
            planned, range_diag = plan_safe_segments(
                words, range_start, range_end, resolved_profile, energy,
                vad_intervals=analysis.vad_intervals,
            )
            protected = protect_segment_boundaries(planned, words, resolved_profile)
            if piece is not None:
                for segment in protected:
                    segment["piece_index"] = piece
            segments.extend(protected)
            range_diagnostics.append(range_diag)
        if len(planning_ranges) > 1:
            # Independently-planned semantic ranges may abut once padded;
            # merge those touches into one continuous kept range instead of a
            # spurious zero-content cut (Requirement R5).
            segments = merge_touching_segments(segments)
            segments = absorb_short_segments(segments)

        fillers_removed = 0
        if resolved_profile.name == "dynamic" and analysis.fillers:
            # Filler removal is the last split performed before boundary
            # validation, so nothing downstream merges short segments back
            # together and reinserts the removed audio (Requirement R4).
            segments, fillers_removed = remove_fillers(
                segments, words, analysis.fillers, resolved_profile, energy,
            )

        for index, segment in enumerate(segments):
            segment["timeline_order"] = index
            if index < len(segments) - 1 and "transition" not in segment:
                segment["transition"] = build_transition(resolved_profile)
        diagnostics = {
            "profile": resolved_profile.name,
            "waveform_used": any(item["waveform_used"] for item in range_diagnostics),
            "candidate_pauses": sum(item["candidate_pauses"] for item in range_diagnostics),
            "cuts": max(0, len(segments) - 1),
            "saved_seconds": max(0.0, round(
                (sum(b - a for a, b in semantic_ranges) if semantic_ranges else end - start)
                - sum(s["end"] - s["start"] for s in segments), 3,
            )),
            "crossfade": resolved_profile.crossfade,
            "vad_used": any(item["vad_used"] for item in range_diagnostics),
            "fillers_removed": fillers_removed,
        }

        if should_cancel():
            raise EditPlanJobCancelled()
        progress_cb(70.0, "Validando fronteiras do plano")
        if semantic_ranges:
            validated_segments = []
            validation_reports = []
            for segment in segments:
                validated, report = validate_edit_plan(
                    [segment], words, resolved_profile, float(segment["start"]),
                    float(segment["end"]), energy, vad_intervals=analysis.vad_intervals,
                )
                validated_segments.extend(validated)
                validation_reports.append(report)
            segments = validated_segments
            for index, segment in enumerate(segments):
                segment["timeline_order"] = index
                if index < len(segments) - 1:
                    segment.setdefault("transition", build_transition(resolved_profile))
            quality = {
                "passed": all(report["passed"] for report in validation_reports),
                "issues": [issue for report in validation_reports for issue in report["issues"]],
                "profile": resolved_profile.name,
                "degraded": any(report["degraded"] for report in validation_reports),
            }
        else:
            segments, quality = validate_edit_plan(
                segments, words, resolved_profile, start, end, energy,
                vad_intervals=analysis.vad_intervals,
            )
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

        jl_counts = {"j_cuts": 0, "l_cuts": 0, "jl_blocked": 0}
        duration_issues = []
        if maximum_seconds is not None:
            try:
                segments, duration_issues = limit_timeline_duration(
                    segments, words, maximum_seconds, resolved_profile, analysis.vad_intervals,
                )
            except ValueError as exc:
                raise EditPlanPreconditionError(str(exc)) from exc
            quality = {**quality, "issues": [*quality["issues"], *duration_issues]}
            if duration_issues:
                diagnostics = {**diagnostics, "cuts": max(0, len(segments) - 1)}
        if effective_jl_enabled and not quality["degraded"]:
            if should_cancel():
                raise EditPlanJobCancelled()
            progress_cb(90.0, "Resolvendo cortes J/L de áudio")
            segments, jl_issues, jl_counts = resolve_jl_cuts(
                segments, words, analysis.vad_intervals, energy, effective_max_jl_offset,
            )
            quality = {**quality, "issues": [*quality["issues"], *jl_issues]}

        # Boundary protection may add post-roll after the requested end. The
        # physical source ceiling remains absolute even when transcript words
        # or container metadata extend beyond the last decodable video frame.
        clamped_issues: list[dict] = []
        for index, segment in enumerate(segments):
            previous_end = float(segment["end"])
            for key in ("end", "video_end", "audio_end"):
                if key in segment:
                    segment[key] = min(float(segment[key]), source_duration)
            segment["end"] = min(float(segment["end"]), source_duration)
            if float(segment["start"]) >= float(segment["end"]):
                raise EditPlanPreconditionError("segmento ficou vazio no limite físico da fonte")
            if previous_end > source_duration:
                clamped_issues.append({
                    "severity": "warning",
                    "code": "source_end_clamped",
                    "segment": index,
                    "boundary": "end",
                    "snapped_from": round(previous_end, 6),
                    "snapped_to": round(source_duration, 6),
                    "delta_ms": round((source_duration - previous_end) * 1000, 3),
                })
        if clamped_issues:
            quality = {**quality, "issues": [*quality["issues"], *clamped_issues]}

        teaser_applied = False
        teaser_dropped: str | None = None
        if teaser_range is not None:
            teaser_dropped = "outside_source"
            teaser_end = min(teaser_range[1], source_duration)
            if teaser_range[0] < teaser_end:
                teaser_dropped = None
                teaser_segments = self._plan_teaser(
                    words, teaser_range[0], teaser_end, resolved_profile, energy, analysis,
                )
                if not teaser_segments:
                    teaser_dropped = "no_speech"
                else:
                    known = [s["piece_index"] for s in segments if s.get("piece_index") is not None]
                    if not known:
                        for segment in segments:
                            segment["piece_index"] = 0
                    teaser_piece = max(known, default=0) + 1
                    for segment in teaser_segments:
                        segment["teaser"] = True
                        segment["piece_index"] = teaser_piece
                    teaser_segments[-1]["transition"] = {
                        "video": "cut", "audio": "hard_concat", "duration": 0.0, "kind": "hard",
                    }
                    combined = [*teaser_segments, *segments]
                    if maximum_seconds is not None and timeline_duration(
                        combined, resolved_profile.crossfade,
                    ) > maximum_seconds + 1e-6:
                        teaser_dropped = "max_duration"
                    else:
                        segments = combined
                        teaser_applied = True
                        for index, segment in enumerate(segments):
                            segment["timeline_order"] = index

        # Validate the confirmed voice only after every duration, J/L-cut, and
        # physical-source adjustment.  This is the audio timeline that render
        # will actually consume, rather than the earlier semantic proposal.
        voice_validation = None
        if voice is not None:
            final_share = subject_share_for_ranges(
                [turn.model_dump() for turn in voice.turns], voice.speaker,
                ((float(segment.get("audio_start", segment["start"])),
                  float(segment.get("audio_end", segment["end"]))) for segment in segments),
            )
            voice_validation = {
                "version": 1, "speaker": voice.speaker,
                "subject_share": round(final_share, 6), "accepted": final_share >= 0.5,
            }
            if final_share < 0.5:
                raise EditPlanPreconditionError(
                    f"Plano final rejeitado: voz confirmada ocupa {final_share:.1%} do áudio falado"
                )

        # Diagnostics always reflect the real, final plan (Requirement R3):
        # boundary validation may have dropped individual cuts (or, for
        # semantic ranges, snapped/extended their edges), scene snapping and
        # duration limiting may have changed segment count further — cuts
        # and saved_seconds are recomputed here from the actual final
        # segments rather than trusted from the pre-validation estimate.
        diagnostics = {
            **diagnostics,
            "cuts": max(0, len(segments) - 1),
            "saved_seconds": max(0.0, round(
                (end - start) - sum(
                    float(s["end"]) - float(s["start"]) for s in segments if not s.get("teaser")
                ), 3,
            )),
            "teaser_dropped": teaser_dropped,
            "scene_snap_count": scene_snap_count,
            "j_cuts": jl_counts["j_cuts"],
            "l_cuts": jl_counts["l_cuts"],
            "jl_blocked": jl_counts["jl_blocked"],
        }

        document = EditPlanDocument(
            project_id=transcript_artifact.project_id, source_asset_id=transcript_artifact.source_asset_id,
            transcript_artifact_id=transcript_artifact.id, analysis_artifact_id=analysis_artifact.id,
            scene_index_artifact_id=scene_index_artifact.id if scene_index_artifact is not None else None,
            input_hash=input_hash, clip_start=round(start, 4), clip_end=round(end, 4),
            profile=resolved_profile.name,
            maximum_seconds=maximum_seconds,
            segments=[_segment_to_schema(segment) for segment in segments],
            timeline_duration_seconds=round(
                timeline_duration(segments, resolved_profile.crossfade), 4
            ),
            diagnostics=EditPlanDiagnostics(**diagnostics),
            quality=EditQualityReport(**quality),
            jl_settings=JlSettings(
                enabled=effective_jl_enabled,
                max_offset_seconds=round(effective_max_jl_offset, 4),
                requested_by=requested_by,
            ),
            voice_validation=voice_validation,
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
                "requested_jl": jl_cut,
                "effective_jl": effective_jl_enabled,
                "effective_max_jl_offset_seconds": round(effective_max_jl_offset, 4),
                "maximum_seconds": maximum_seconds,
                "duration_limit_applied": bool(duration_issues),
                "timeline_duration_seconds": document.timeline_duration_seconds,
                "semantic_range_count": len(semantic_ranges),
                "voice_validation": voice_validation,
                "teaser_applied": teaser_applied,
                "teaser_dropped": teaser_dropped,
            },
        ))
        progress_cb(100.0, "Plano de edição concluído")
        return {"cached": False, "edit_plan_artifact_id": artifact.id, "edit_plan_path": artifact.path, "schema_version": artifact.schema_version,
                "duration_limit_applied": bool(duration_issues), "timeline_duration_seconds": document.timeline_duration_seconds,
                "below_minimum": _below_minimum(document.timeline_duration_seconds, minimum_seconds),
                "voice_validation": voice_validation,
                "teaser_applied": teaser_applied, "teaser_dropped": teaser_dropped}
