"""Per-clip refine pass ("enxugar"): drops digressions, restarts and repetitions
from a selected clip, phrase by phrase.

Runs only for clips the user is exporting (export batch or the export editor's
preview of a selected clip); results are cached by input hash, so the export
reuses what the editor already paid for. Phrases are built only inside the
clip's pieces, so the model can never re-include material the selection cut.
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
from cortex.suggest.service import provider_identity

TIGHTEN_ARTIFACT_VERSION = 2
PHRASE_ALGORITHM_VERSION = 2
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_SENTENCE_END_RE = re.compile(r"[.!?…,]$")
_MAX_PHRASE_WORDS = 14
_MAX_GAP_SECONDS = 0.25
_MINIMUM_KEPT_RATIO = 0.55
# Below this margin over the minimum duration there is nothing worth removing:
# skip the call entirely instead of spending tokens on a refusal.
_SKIP_MARGIN_SECONDS = 3.0
_MIN_PHRASES = 3


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


def build_piece_phrases(
    transcript: dict[str, Any], ranges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Phrases of every piece, numbered globally and tagged with their piece."""
    phrases: list[dict[str, Any]] = []
    for position, entry in enumerate(ranges):
        piece = entry.get("piece", entry.get("order", position))
        for phrase in build_phrases(
            transcript, float(entry["source_start"]), float(entry["source_end"]),
        ):
            phrases.append({**phrase, "id": len(phrases), "piece": piece})
    return phrases


def _merge_ranges(phrases: list[dict[str, Any]], keep_ids: list[int]) -> list[dict[str, Any]]:
    """Contiguous kept phrases of the same piece become one range; a range never
    bridges two pieces (that would re-include what the selection removed)."""
    keep_set = set(keep_ids)
    ranges: list[dict[str, Any]] = []
    previous_index: int | None = None
    for index, phrase in enumerate(phrases):
        if phrase["id"] not in keep_set:
            previous_index = None
            continue
        same_piece = bool(ranges) and ranges[-1].get("piece") == phrase.get("piece")
        if previous_index is not None and index == previous_index + 1 and same_piece:
            ranges[-1]["source_end"] = phrase["end"]
        else:
            entry = {
                "source_start": phrase["start"],
                "source_end": phrase["end"],
                "audio_mode": "source",
            }
            if phrase.get("piece") is not None:
                entry["piece"] = phrase["piece"]
            ranges.append(entry)
        previous_index = index
    return ranges


def _speech_seconds(phrases: list[dict[str, Any]], ids: set[int] | None = None) -> float:
    return sum(
        phrase["end"] - phrase["start"] for phrase in phrases
        if ids is None or phrase["id"] in ids
    )


class TightenService:
    def __init__(
        self,
        config: CortexConfig,
        domain: DomainStore,
        provider: SuggestionProvider,
        *,
        effort: str | None = None,
    ) -> None:
        self._config = config
        self._domain = domain
        self._provider = provider
        self._effort = effort

    def run(
        self,
        *,
        transcript_artifact: TranscriptArtifact,
        start: float,
        end: float,
        brief: dict[str, Any] | None,
        progress_cb: Callable[[float, str], None],
        should_cancel: Callable[[], bool],
        ranges: list[dict[str, Any]] | None = None,
        headline: str | None = None,
        minimum_seconds: float | None = None,
        maximum_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Return ``status`` accepted (with ``ranges``), skipped or rejected.

        A rejected or skipped refine leaves the clip exactly as selected; the
        reason is persisted and shown, never hidden.
        """
        if should_cancel():
            raise TightenJobCancelled()
        start, end = float(start), float(end)
        transcript = json.loads(Path(transcript_artifact.path).read_text(encoding="utf-8"))
        piece_ranges = [
            entry for entry in (ranges or [])
            if entry.get("audio_mode") != "room_tone"
        ] or [{"source_start": start, "source_end": end, "piece": 0}]
        phrases = build_piece_phrases(transcript, piece_ranges)
        if not phrases:
            raise ValueError("Não há frases na janela do corte para enxugar.")

        prompt_path = _PROJECT_ROOT / "prompts/clip_tighten.pt-BR.md"
        schema_path = _PROJECT_ROOT / "prompts/clip_tighten.schema.json"
        prompt_template = prompt_path.read_text(encoding="utf-8")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        prompt_sha256 = hashlib.sha256(prompt_template.encode()).hexdigest()
        schema_sha256 = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()

        original_seconds = _speech_seconds(phrases)
        # Normalized so 40 and 40.0 (int from a brief, float from JSON) share the
        # same cache entry; a mismatch would silently re-bill the refine call.
        minimum_seconds = float(minimum_seconds) if minimum_seconds is not None else None
        maximum_seconds = float(maximum_seconds) if maximum_seconds is not None else None
        request_payload = {
            "primary_subject": (brief or {}).get("primary_subject"),
            "headline": headline or None,
            "target": {
                "minimum_seconds": minimum_seconds,
                "maximum_seconds": maximum_seconds,
                "original_seconds": round(original_seconds, 1),
            },
            "phrases": [
                {"id": p["id"], "piece": p["piece"], "dur": round(p["end"] - p["start"], 1),
                 "text": p["text"]}
                for p in phrases
            ],
        }
        input_hash = _sha256_json({
            "artifact_version": TIGHTEN_ARTIFACT_VERSION,
            "phrase_algorithm_version": PHRASE_ALGORITHM_VERSION,
            "transcript_sha256": transcript_artifact.audio_sha256,
            "ranges": [(p["start"], p["end"], p["piece"]) for p in phrases],
            "prompt_sha256": prompt_sha256,
            "schema_sha256": schema_sha256,
            "provider": provider_identity(self._config, effort=self._effort),
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
                "status": document["status"],
                "reason": document.get("reason"),
                "ranges": document.get("ranges"),
                "provenance": document.get("provenance"),
            }

        def persist(status: str, *, reason: str | None, keep_ids: list[int] | None,
                    ranges_out: list[dict[str, Any]] | None, notes: str = "",
                    unknown_ids: list[int] | None = None,
                    provenance: dict[str, Any] | None = None) -> dict[str, Any]:
            kept_ratio = (
                _speech_seconds(phrases, set(keep_ids)) / original_seconds
                if keep_ids and original_seconds > 0 else None
            )
            artifact_document = {
                "artifact_schema_version": TIGHTEN_ARTIFACT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "input_hash": input_hash,
                "transcript_artifact_id": transcript_artifact.id,
                "start": start,
                "end": end,
                "status": status,
                "reason": reason,
                "phrases": phrases,
                "keep_ids": keep_ids,
                "kept_ratio": kept_ratio,
                "ignored_unknown_ids": unknown_ids or [],
                "notes": notes,
                "ranges": ranges_out,
                "provenance": provenance,
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
                metadata={"provenance": provenance, "status": status, "kept_ratio": kept_ratio},
            ))
            progress_cb(100.0, "Enxugamento do corte concluído")
            return {
                "cached": False,
                "tighten_artifact_id": artifact.id,
                "tighten_path": artifact.path,
                "status": status,
                "reason": reason,
                "ranges": ranges_out,
                "provenance": provenance,
            }

        floor = float(minimum_seconds) if minimum_seconds else 0.0
        if len(phrases) < _MIN_PHRASES or original_seconds < floor + _SKIP_MARGIN_SECONDS:
            return persist(
                "skipped", keep_ids=None, ranges_out=None,
                reason="Corte já está no limite mínimo de duração; nada para enxugar.",
            )

        prompt = (
            "Corte a refinar:\n"
            f"{json.dumps(request_payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
            "Responda com o objeto JSON do schema."
        )
        progress_cb(10.0, "Preparando enxugamento do corte")
        try:
            generated = self._provider.generate(
                prompt, schema, should_cancel=should_cancel, system_context=prompt_template,
            )
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
        # reordered), so normalize them; unknown ids are dropped and recorded.
        valid_ids = {p["id"] for p in phrases}
        returned_ids = generated.document["keep_ids"]
        keep_ids = sorted(set(returned_ids) & valid_ids)
        unknown_ids = sorted(set(returned_ids) - valid_ids)
        notes = str(generated.document.get("notes", ""))
        kept_seconds = _speech_seconds(phrases, set(keep_ids))
        reason = None
        if not keep_ids:
            reason = "A IA não manteve nenhuma frase válida; o corte segue sem enxugar."
        elif kept_seconds / original_seconds < _MINIMUM_KEPT_RATIO:
            reason = (
                f"A IA removeu mais de {1 - _MINIMUM_KEPT_RATIO:.0%} da fala; "
                "o corte segue sem enxugar."
            )
        elif floor and kept_seconds < floor:
            reason = (
                f"Enxugado ficaria com {kept_seconds:.0f}s, abaixo do mínimo de {floor:.0f}s; "
                "o corte segue sem enxugar."
            )
        if reason is not None:
            return persist(
                "rejected", reason=reason, keep_ids=keep_ids, ranges_out=None, notes=notes,
                unknown_ids=unknown_ids, provenance=generated.provenance,
            )
        return persist(
            "accepted", reason=None, keep_ids=keep_ids,
            ranges_out=_merge_ranges(phrases, keep_ids), notes=notes,
            unknown_ids=unknown_ids, provenance=generated.provenance,
        )
