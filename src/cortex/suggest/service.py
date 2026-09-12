from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from cortex.config import CortexConfig
from cortex.diarize.attribution import (
    VOICE_ATTRIBUTION_VERSION,
    ConfirmedVoice,
    subject_share,
    subject_share_for_ranges,
)
from cortex.domain.models import StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.paths import suggestions_dir
from cortex.suggest.provider import SuggestionProvider, SuggestionProviderCancelled

SUGGESTION_ARTIFACT_VERSION = 1
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class SuggestionJobCancelled(RuntimeError):
    pass


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _transcript_lines(document: dict[str, Any]) -> str:
    lines = []
    for segment in document.get("segments") or []:
        text = " ".join(str(segment.get("text") or "").split())
        if text:
            lines.append(
                f"[{float(segment.get('start', 0)):.3f}-{float(segment.get('end', 0)):.3f}] {text}"
            )
    return "\n".join(lines)


def _compact_analysis(document: dict[str, Any]) -> dict[str, Any]:
    pauses = document.get("pauses") or []
    return {
        "overall_speech_ratio": document.get("overall_speech_ratio"),
        "loudness": document.get("loudness"),
        "room_tone_samples": document.get("room_tone"),
        "notable_pauses": [
            {"start": pause.get("start"), "end": pause.get("end"), "duration": pause.get("duration")}
            for pause in pauses
            if float(pause.get("duration") or 0) >= 0.4
        ][:200],
    }


class SuggestionService:
    def __init__(
        self,
        config: CortexConfig,
        domain: DomainStore,
        provider: SuggestionProvider,
    ) -> None:
        self._config = config
        self._domain = domain
        self._provider = provider

    def run(
        self,
        *,
        transcript_artifact: TranscriptArtifact,
        analysis_artifact: StageArtifact | None = None,
        brief: dict[str, Any],
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict[str, Any]:
        if should_cancel():
            raise SuggestionJobCancelled()
        voice = None
        if "confirmed_voice" in brief:
            try:
                voice = ConfirmedVoice.model_validate(brief["confirmed_voice"])
            except ValidationError as exc:
                raise ValueError(
                    "Referência de voz confirmada inválida; revise a voz e os turnos da diarização."
                ) from exc
        transcript = json.loads(Path(transcript_artifact.path).read_text(encoding="utf-8"))
        analysis_context = None
        if analysis_artifact is not None:
            analysis_context = _compact_analysis(
                json.loads(Path(analysis_artifact.path).read_text(encoding="utf-8"))
            )
        prompt_path = _PROJECT_ROOT / "prompts/clip_selection.pt-BR.md"
        schema_path = _PROJECT_ROOT / "prompts/clip_selection.schema.json"
        prompt_template = prompt_path.read_text(encoding="utf-8")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        request_payload = {
            "brief": brief,
            "transcript": _transcript_lines(transcript),
            "transcript_metadata": {
                "duration_seconds": transcript.get("duration_seconds"),
                "language": transcript.get("language"),
            },
            "local_audio_analysis": analysis_context,
        }
        prompt_sha256 = hashlib.sha256(prompt_template.encode()).hexdigest()
        input_hash = _sha256_json({
            "artifact_version": SUGGESTION_ARTIFACT_VERSION,
            "duration_validation_version": 1,
            "transcript_sha256": transcript_artifact.audio_sha256,
            "prompt_sha256": prompt_sha256,
            "schema_sha256": hashlib.sha256(
                json.dumps(schema, sort_keys=True).encode()
            ).hexdigest(),
            "provider": self._config.ai.model_dump(mode="json"),
            "request": request_payload,
            **({"voice_attribution_version": VOICE_ATTRIBUTION_VERSION} if voice else {}),
        })
        cached = self._domain.find_cached_stage_artifact(
            project_id=transcript_artifact.project_id,
            stage="suggestion",
            input_hash=input_hash,
            schema_version=SUGGESTION_ARTIFACT_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Sugestões em cache reutilizadas")
            cached_document = json.loads(Path(cached.path).read_text(encoding="utf-8"))
            return {
                "cached": True,
                "suggestion_artifact_id": cached.id,
                "suggestion_path": cached.path,
                "selection": cached_document["selection"],
                "provenance": cached_document.get("provenance"),
            }

        prompt = (
            f"{prompt_template}\n\n"
            "## Pedido atual e transcrição\n"
            f"{json.dumps(request_payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        if len(prompt) > self._config.ai.max_input_chars:
            raise ValueError(
                "transcrição excede o limite do provider; análise em chunks ainda é necessária "
                f"({len(prompt)} > {self._config.ai.max_input_chars} caracteres)"
            )

        progress_cb(5.0, "Preparando seleção editorial")
        try:
            generated = self._provider.generate(prompt, schema, should_cancel=should_cancel)
        except SuggestionProviderCancelled as exc:
            raise SuggestionJobCancelled() from exc
        if should_cancel():
            raise SuggestionJobCancelled()
        progress_cb(90.0, "Validando resposta estruturada")

        errors = sorted(Draft202012Validator(schema).iter_errors(generated.document), key=str)
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path) or "root"
            raise ValueError(f"resposta da IA inválida em {location}: {first.message}")

        duration_validation = None
        maximum = brief.get("maximum_seconds")
        if maximum is not None:
            ceiling = float(maximum)
            if not 0 < ceiling < float("inf"):
                raise ValueError("maximum_seconds deve ser positivo e finito")
            clips = generated.document["clips"]
            # The current planner consumes the envelope, not approximate_edl.
            # Reject a long envelope even when the model estimates a short edit.
            accepted = [clip for clip in clips if
                        clip["end_second"] - clip["start_second"] <= ceiling
                        and clip["estimated_duration"] <= ceiling]
            duration_validation = {"version": 1, "maximum_seconds": ceiling,
                                   "rejected_count": len(clips) - len(accepted)}
            if not accepted:
                raise ValueError(f"Nenhum corte respeitou o máximo de {ceiling:g}s. "
                                 "Gere outra seleção; nenhum corte acima do limite foi salvo.")
            if len(accepted) != len(clips):
                generated.document["selection_notes"] += (
                    f" {len(clips)-len(accepted)} sugestões acima de {ceiling:g}s foram removidas."
                )
            generated.document["clips"] = accepted

        voice_validation = None
        if voice is not None:
            turns = [turn.model_dump() for turn in voice.turns]
            fresh = []
            metrics = []
            for clip in generated.document["clips"]:
                envelope_share = subject_share(
                    turns, voice.speaker, clip["start_second"], clip["end_second"],
                )
                editorial_share = subject_share_for_ranges(
                    turns, voice.speaker,
                    ((item["source_start"], item["source_end"])
                     for item in clip["approximate_edl"] if item["audio_mode"] != "room_tone"),
                )
                accepted = envelope_share >= 0.5 and editorial_share >= 0.5
                metrics.append({
                    "clip_rank": clip["rank"],
                    "envelope_subject_share": envelope_share,
                    "editorial_subject_share": editorial_share,
                    "accepted": accepted,
                })
                if accepted:
                    fresh.append(clip)
            if not fresh:
                envelope_failures = sum(item["envelope_subject_share"] < 0.5 for item in metrics)
                editorial_failures = sum(item["editorial_subject_share"] < 0.5 for item in metrics)
                raise ValueError(
                    "Nenhum corte tem fala predominante da voz confirmada do Dayon "
                    "no intervalo e na EDL editorial. "
                    f"Abaixo de 50%: intervalo={envelope_failures}; EDL editorial={editorial_failures}. "
                    "Revise a identificação e a direção editorial."
                )
            # These are suggestion checks. The deterministic final audio plan is separate.
            voice_validation = {
                "version": VOICE_ATTRIBUTION_VERSION,
                "scope": "suggestion_envelope_and_editorial_source_union",
                "speaker": voice.speaker,
                "minimum_subject_share": 0.5,
                "clips": metrics,
            }
            generated.document["clips"] = fresh
        excluded = brief.get("exclude_ranges") or []
        if excluded:
            clips = generated.document["clips"]
            fresh = [clip for clip in clips if not any(
                min(clip["end_second"], old["end"]) > max(clip["start_second"], old["start"])
                for old in excluded
            )]
            if not fresh:
                raise ValueError("A seleção repetiu trechos já sugeridos. Nenhum corte novo foi salvo; ajuste a direção editorial.")
            if len(fresh) != len(clips):
                generated.document["selection_notes"] += " Trechos já sugeridos foram removidos."
                generated.document["clips"] = fresh

        artifact_document = {
            "duration_validation": duration_validation,
            "artifact_schema_version": SUGGESTION_ARTIFACT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input_hash": input_hash,
            "transcript_artifact_id": transcript_artifact.id,
            "analysis_artifact_id": analysis_artifact.id if analysis_artifact else None,
            "provenance": {
                **generated.provenance,
                "prompt_sha256": prompt_sha256,
                "schema_id": schema.get("$id"),
            },
            "selection": generated.document,
            **({"voice_validation": voice_validation} if voice_validation is not None else {}),
        }
        out_dir = suggestions_dir(self._config, transcript_artifact.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"suggestion-{input_hash[:12]}.json"
        out_path.write_text(
            json.dumps(artifact_document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=transcript_artifact.project_id,
            stage="suggestion",
            schema_version=SUGGESTION_ARTIFACT_VERSION,
            path=str(out_path),
            input_hash=input_hash,
            metadata={
                "provenance": generated.provenance,
                "clip_count": len(generated.document["clips"]),
            },
        ))
        progress_cb(100.0, "Sugestões editoriais concluídas")
        return {
            "cached": False,
            "suggestion_artifact_id": artifact.id,
            "suggestion_path": artifact.path,
            "selection": generated.document,
            "provenance": generated.provenance,
        }
