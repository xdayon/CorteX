from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from cortex.config import CortexConfig
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
            "transcript_sha256": transcript_artifact.audio_sha256,
            "prompt_sha256": prompt_sha256,
            "schema_sha256": hashlib.sha256(
                json.dumps(schema, sort_keys=True).encode()
            ).hexdigest(),
            "provider": self._config.ai.model_dump(mode="json"),
            "request": request_payload,
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

        artifact_document = {
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
