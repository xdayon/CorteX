"""Per-clip "tighten" pass: drops digressions/restarts from a selected clip.

Runs only for clips that are actually being exported (see
``cortex.export_batches._queue_export_plan``), never for previews, so token
spend stays tied to real deliverables.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from cortex.config import CortexConfig
from cortex.domain.models import StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.paths import suggestions_dir
from cortex.suggest.provider import SuggestionProvider, SuggestionProviderCancelled

TIGHTEN_ARTIFACT_VERSION = 1
PHRASE_ALGORITHM_VERSION = 1
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_SENTENCE_END_RE = re.compile(r"[.!?…,]$")
_MAX_PHRASE_WORDS = 14
_MAX_GAP_SECONDS = 0.25
_MINIMUM_KEPT_RATIO = 0.4


class TightenJobCancelled(RuntimeError):
    pass


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_phrases(transcript: dict[str, Any], start: float, end: float) -> list[dict[str, Any]]:
    """Split the words of ``transcript`` inside [start, end] into phrases.

    A phrase boundary is placed after a word ending in one of ``. ! ? … ,``,
    at a gap of at least ``_MAX_GAP_SECONDS`` between consecutive words, or
    after ``_MAX_PHRASE_WORDS`` words — whichever comes first.
    """
    words: list[dict[str, Any]] = []
    for segment in transcript.get("segments") or []:
        for word in segment.get("words") or []:
            word_start = float(word.get("start", 0))
            word_end = float(word.get("end", word_start))
            if word_end <= start or word_start >= end:
                continue
            text = str(word.get("word") or "").strip()
            if not text:
                continue
            words.append({
                "start": max(word_start, start),
                "end": min(word_end, end),
                "word": text,
            })
    words.sort(key=lambda word: word["start"])

    phrases: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        phrases.append({
            "id": len(phrases),
            "start": current[0]["start"],
            "end": current[-1]["end"],
            "text": " ".join(word["word"] for word in current),
        })

    for index, word in enumerate(words):
        current.append(word)
        is_last = index == len(words) - 1
        ends_sentence = bool(_SENTENCE_END_RE.search(word["word"]))
        gap = (words[index + 1]["start"] - word["end"]) if not is_last else None
        if is_last or ends_sentence or (gap is not None and gap >= _MAX_GAP_SECONDS) or len(current) >= _MAX_PHRASE_WORDS:
            flush()
            current = []
    return phrases


def _merge_ranges(phrases: list[dict[str, Any]], keep_ids: list[int]) -> list[dict[str, Any]]:
    keep_set = set(keep_ids)
    ranges: list[dict[str, Any]] = []
    previous_index: int | None = None
    for index, phrase in enumerate(phrases):
        if phrase["id"] not in keep_set:
            previous_index = None
            continue
        if previous_index is not None and index == previous_index + 1:
            ranges[-1]["source_end"] = phrase["end"]
        else:
            ranges.append({
                "source_start": phrase["start"],
                "source_end": phrase["end"],
                "audio_mode": "source",
            })
        previous_index = index
    return ranges


class TightenService:
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
        start: float,
        end: float,
        brief: dict[str, Any] | None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
    ) -> dict[str, Any]:
        if should_cancel():
            raise TightenJobCancelled()
        start, end = float(start), float(end)
        transcript = json.loads(Path(transcript_artifact.path).read_text(encoding="utf-8"))
        phrases = build_phrases(transcript, start, end)
        if not phrases:
            raise ValueError("Não há frases na janela do corte para enxugar.")

        prompt_path = _PROJECT_ROOT / "prompts/clip_tighten.pt-BR.md"
        schema_path = _PROJECT_ROOT / "prompts/clip_tighten.schema.json"
        prompt_template = prompt_path.read_text(encoding="utf-8")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        prompt_sha256 = hashlib.sha256(prompt_template.encode()).hexdigest()
        schema_sha256 = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()

        primary_subject = (brief or {}).get("primary_subject")
        request_payload = {
            "primary_subject": primary_subject,
            "phrases": [
                {"id": p["id"], "start": round(p["start"], 3), "end": round(p["end"], 3), "text": p["text"]}
                for p in phrases
            ],
        }
        input_hash = _sha256_json({
            "artifact_version": TIGHTEN_ARTIFACT_VERSION,
            "phrase_algorithm_version": PHRASE_ALGORITHM_VERSION,
            "transcript_sha256": transcript_artifact.audio_sha256,
            "start": start,
            "end": end,
            "prompt_sha256": prompt_sha256,
            "schema_sha256": schema_sha256,
            "provider": self._config.ai.model_dump(mode="json"),
            "request": request_payload,
        })

        cached = self._domain.find_cached_stage_artifact(
            project_id=transcript_artifact.project_id,
            stage="tighten",
            input_hash=input_hash,
            schema_version=TIGHTEN_ARTIFACT_VERSION,
        )
        if cached is not None:
            progress_cb(100.0, "Enxugamento em cache reutilizado")
            document = json.loads(Path(cached.path).read_text(encoding="utf-8"))
            return {
                "cached": True,
                "tighten_artifact_id": cached.id,
                "tighten_path": cached.path,
                "ranges": document["ranges"],
                "provenance": document.get("provenance"),
            }

        prompt = (
            f"{prompt_template}\n\n## Frases\n"
            f"{json.dumps(request_payload, ensure_ascii=False, separators=(',', ':'))}"
        )

        progress_cb(10.0, "Preparando enxugamento do corte")
        try:
            generated = self._provider.generate(prompt, schema, should_cancel=should_cancel)
        except SuggestionProviderCancelled as exc:
            raise TightenJobCancelled() from exc
        if should_cancel():
            raise TightenJobCancelled()
        progress_cb(80.0, "Validando resposta do enxugamento")

        errors = sorted(Draft202012Validator(schema).iter_errors(generated.document), key=str)
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path) or "root"
            raise ValueError(f"resposta de enxugamento inválida em {location}: {first.message}")

        # Order and duplicates carry no editorial meaning (phrases are never
        # reordered), so normalize them; ids that do not exist are dropped and
        # recorded instead of blocking the export.
        valid_ids = {p["id"] for p in phrases}
        returned_ids = generated.document["keep_ids"]
        keep_ids = sorted(set(returned_ids) & valid_ids)
        unknown_ids = sorted(set(returned_ids) - valid_ids)
        if not keep_ids:
            raise ValueError("Enxugamento não manteve nenhuma frase; nenhum corte foi enxugado.")

        total_duration = sum(p["end"] - p["start"] for p in phrases)
        kept_duration = sum(p["end"] - p["start"] for p in phrases if p["id"] in keep_ids)
        kept_ratio = (kept_duration / total_duration) if total_duration > 0 else 0.0
        if kept_ratio < _MINIMUM_KEPT_RATIO:
            raise ValueError(
                "Enxugamento removeu frases demais; duração mantida abaixo de "
                f"{_MINIMUM_KEPT_RATIO:.0%} do original. Nenhum corte foi enxugado."
            )

        ranges = _merge_ranges(phrases, keep_ids)

        artifact_document = {
            "artifact_schema_version": TIGHTEN_ARTIFACT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input_hash": input_hash,
            "transcript_artifact_id": transcript_artifact.id,
            "start": start,
            "end": end,
            "phrases": phrases,
            "keep_ids": keep_ids,
            "ignored_unknown_ids": unknown_ids,
            "notes": generated.document.get("notes", ""),
            "ranges": ranges,
            "provenance": generated.provenance,
        }
        out_dir = suggestions_dir(self._config, transcript_artifact.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"tighten-{input_hash[:12]}.json"
        out_path.write_text(
            json.dumps(artifact_document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifact = self._domain.create_stage_artifact(StageArtifact(
            project_id=transcript_artifact.project_id,
            stage="tighten",
            schema_version=TIGHTEN_ARTIFACT_VERSION,
            path=str(out_path),
            input_hash=input_hash,
            metadata={"provenance": generated.provenance, "kept_ratio": kept_ratio},
        ))
        progress_cb(100.0, "Enxugamento do corte concluído")
        return {
            "cached": False,
            "tighten_artifact_id": artifact.id,
            "tighten_path": artifact.path,
            "ranges": ranges,
            "provenance": generated.provenance,
        }
