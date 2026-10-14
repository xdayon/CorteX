from __future__ import annotations

import hashlib
import json
import re
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
from cortex.domain.store import DomainStore, SourceAssetNotFoundError
from cortex.paths import suggestions_dir
from cortex.suggest.learning import build_learning_context
from cortex.suggest.provider import SuggestionProvider, SuggestionProviderCancelled

SUGGESTION_ARTIFACT_VERSION = 4
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Whole-word hesitation fillers stripped from the transcript sent to the LLM.
# They carry no editorial signal and only inflate token usage.
_HESITATION_PATTERN = re.compile(r"\b(?:eh|ah|hum|h[ãa]|uh|hmm)\b", re.IGNORECASE)
_MAX_PIECES = 15
_MIN_PIECE_SECONDS = 0.6
_TEASER_MIN_SECONDS = 1.2
_TEASER_MAX_SECONDS = 4.5


class SuggestionJobCancelled(RuntimeError):
    pass


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _strip_hesitations(text: str) -> str:
    return " ".join(_HESITATION_PATTERN.sub(" ", text).split())


_SENTENCE_END = (".", "?", "!", "…", ",", ";", ":")
_UNIT_MIN_SENTENCE_SECONDS = 1.2
_UNIT_MAX_SECONDS = 8.0
_UNIT_PAUSE_SECONDS = 0.6
_DANGLING_REFERENTS = frozenset({
    "isso", "isto", "esse", "essa", "esses", "essas", "ele", "ela", "eles", "elas",
    "aquilo", "aquele", "aquela",
})


def _unit_speaker_label(
    start: float, end: float, voice: ConfirmedVoice, primary_subject: str,
) -> str | None:
    overlap: dict[str, float] = {}
    for turn in voice.turns:
        shared = min(end, turn.end) - max(start, turn.start)
        if shared > 0:
            overlap[turn.speaker] = overlap.get(turn.speaker, 0.0) + shared
    if not overlap:
        return None
    dominant = max(overlap, key=lambda speaker: overlap[speaker])
    return "D" if dominant == voice.speaker else "O"


def _selection_units(
    transcript: dict[str, Any], *, voice: ConfirmedVoice | None, primary_subject: str,
) -> list[dict[str, Any]]:
    """Group word timings into sentence-sized units the model can target precisely.

    Whisper's batched segments span ~24 s, too coarse to compose a cut from, so
    the model sees one numbered line per clause (or per 8 s / pause) instead.
    """
    units: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def emit(start: float, end: float, raw_text: str) -> None:
        text = _strip_hesitations(raw_text)
        if not text:
            return
        label = _unit_speaker_label(start, end, voice, primary_subject) if voice else None
        units.append({
            "id": len(units), "start": start, "end": end, "text": text, "speaker_label": label,
        })

    def flush() -> None:
        if current:
            emit(
                float(current[0]["start"]), float(current[-1]["end"]),
                " ".join(str(word["word"]).strip() for word in current),
            )
            current.clear()

    for segment in transcript.get("segments") or []:
        words = segment.get("words") or []
        if not words:
            flush()
            emit(float(segment.get("start", 0)), float(segment.get("end", 0)),
                 str(segment.get("text") or ""))
            continue
        for word in words:
            if current and float(word["start"]) - float(current[-1]["end"]) >= _UNIT_PAUSE_SECONDS:
                flush()
            current.append(word)
            duration = float(word["end"]) - float(current[0]["start"])
            ends_sentence = str(word["word"]).strip().endswith(_SENTENCE_END)
            if duration >= _UNIT_MAX_SECONDS or (
                ends_sentence and duration >= _UNIT_MIN_SENTENCE_SECONDS
            ):
                flush()
    flush()
    return units


def _transcript_lines(units: list[dict[str, Any]]) -> str:
    """One line per unit: ``#id [start] text``. A piece lasts from its first
    line's start to the start of the line after it (one subtraction per piece;
    per-line durations made the model sum dozens of numbers and doubled its
    reasoning tokens in a real run). The speaker code (D/O) is written only when
    it changes, to save tokens."""
    lines = []
    previous_label = None
    for unit in units:
        label = unit.get("speaker_label")
        prefix = f"{label}: " if label and label != previous_label else ""
        if label:
            previous_label = label
        lines.append(f"#{unit['id']} [{float(unit['start']):.1f}] {prefix}{unit['text']}")
    return "\n".join(lines)


# Connectors and fillers that make a weak cold open. Removed only from the very
# start of a clip, at most a few words, never "é" (a verb) or "porque" (meaning).
_LEADING_FILLERS = frozenset({
    "então", "né", "tipo", "aí", "daí", "e", "mas", "cara", "assim", "bom", "olha",
    "enfim", "pois", "mano", "ah", "eh", "ã", "hum", "hã",
})
_MAX_LEADING_FILLER_WORDS = 4
_MAX_LEADING_FILLER_SECONDS = 1.5


def _flat_words(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Transcript words with their global index (same convention as caption
    corrections: position in the segment-ordered word list)."""
    words = []
    for segment in transcript.get("segments") or []:
        for word in segment.get("words") or []:
            words.append({**word, "index": len(words)})
    return words


def _word_key(text: str) -> str:
    return re.sub(r"[^\w]", "", str(text).casefold())


def _trim_leading_fillers(
    piece: dict[str, Any], words: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    inside = [
        word for word in words
        if float(word["start"]) >= float(piece["start"]) - 0.01
        and float(word["start"]) < float(piece["end"])
    ]
    removed: list[str] = []
    for word in inside[:_MAX_LEADING_FILLER_WORDS + 1]:
        if _word_key(word["word"]) not in _LEADING_FILLERS:
            break
        removed.append(str(word["word"]).strip())
    if not removed or len(removed) > _MAX_LEADING_FILLER_WORDS or len(removed) >= len(inside):
        return piece, []
    new_start = float(inside[len(removed)]["start"])
    if (
        new_start - float(piece["start"]) > _MAX_LEADING_FILLER_SECONDS
        or float(piece["end"]) - new_start < _MIN_PIECE_SECONDS
    ):
        return piece, []
    text_words = str(piece["text"]).split()
    return {**piece, "start": new_start, "text": " ".join(text_words[len(removed):])}, removed


def _emphasis_indices(
    phrases: list[str], pieces: list[dict[str, Any]], words: list[dict[str, Any]],
) -> list[int]:
    """Map the model's literal emphasis phrases to transcript word indices inside
    the clip's pieces. Phrases that are not found are ignored."""
    inside = [
        word for word in words
        if any(
            float(word["start"]) >= float(piece["start"]) - 0.01
            and float(word["end"]) <= float(piece["end"]) + 0.01
            for piece in pieces
        )
    ]
    keys = [_word_key(word["word"]) for word in inside]
    found: set[int] = set()
    for phrase in phrases[:6]:
        tokens = [_word_key(token) for token in str(phrase).split()]
        tokens = [token for token in tokens if token]
        if not tokens or len(tokens) > 3:
            continue
        for start in range(len(keys) - len(tokens) + 1):
            if keys[start:start + len(tokens)] == tokens:
                found.update(inside[start + offset]["index"] for offset in range(len(tokens)))
                break
    return sorted(found)


def _selection_rules(config: CortexConfig) -> dict[str, Any]:
    return {
        "max_envelope_seconds": config.ai.selection_max_envelope_seconds,
        "max_pieces": _MAX_PIECES,
        "min_piece_seconds": _MIN_PIECE_SECONDS,
    }


def _normalize_text(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def _clip_ranges(clip: dict[str, Any]) -> list[tuple[float, float]]:
    pieces = clip.get("pieces")
    if pieces:
        return [(float(piece["start"]), float(piece["end"])) for piece in pieces]
    return [(float(clip["start_second"]), float(clip["end_second"]))]


def _ranges_overlap(
    first: list[tuple[float, float]], second: list[tuple[float, float]],
) -> bool:
    return any(
        min(a_end, b_end) > max(a_start, b_start)
        for a_start, a_end in first for b_start, b_end in second
    )


def _resolve_pieces(
    raw_pieces: list[Any], units: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Turn the model's unit-id ranges into timed, ordered pieces; never reorders."""
    warnings: list[str] = []
    ranges: list[list[int]] = []
    for index, raw in enumerate(raw_pieces, start=1):
        from_id = raw.get("from_id") if isinstance(raw, dict) else None
        to_id = raw.get("to_id") if isinstance(raw, dict) else None
        if (
            isinstance(from_id, bool) or isinstance(to_id, bool)
            or not isinstance(from_id, int) or not isinstance(to_id, int)
            or not 0 <= from_id < len(units) or not 0 <= to_id < len(units)
            or to_id < from_id
        ):
            warnings.append(f"Peça {index} descartada: IDs de linha inválidos.")
            continue
        if ranges and from_id <= ranges[-1][1]:
            warnings.append(f"Peça {index} descartada: fora de ordem cronológica ou sobreposta.")
            continue
        if ranges and from_id == ranges[-1][1] + 1:
            ranges[-1][1] = to_id
            continue
        ranges.append([from_id, to_id])
    pieces: list[dict[str, Any]] = []
    for from_id, to_id in ranges:
        start, end = float(units[from_id]["start"]), float(units[to_id]["end"])
        if end - start < _MIN_PIECE_SECONDS:
            warnings.append(f"Peça descartada: trecho de {end - start:.1f}s é curto demais.")
            continue
        pieces.append({
            "from_id": from_id, "to_id": to_id, "start": start, "end": end,
            "text": " ".join(str(unit["text"]) for unit in units[from_id:to_id + 1]),
        })
    return pieces, warnings


def _piece_warnings(clip: dict[str, Any], pieces: list[dict[str, Any]]) -> list[str]:
    del clip
    warnings: list[str] = []
    for number, piece in enumerate(pieces[1:], start=2):
        words = piece["text"].split()
        first = _normalize_text(words[0]) if words else ""
        if first in _DANGLING_REFERENTS:
            warnings.append(
                f"A peça {number} começa com '{first}': confira se o referente ficou de fora."
            )
    return warnings


def _resolve_teaser(
    raw_ids: Any, pieces: list[dict[str, Any]], units: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Map the model's teaser line ids to a timed phrase inside one piece. An
    invalid teaser is dropped with a warning, never repaired."""
    if not isinstance(raw_ids, list) or not raw_ids:
        return None, []
    ids = raw_ids
    if any(isinstance(item, bool) or not isinstance(item, int) for item in ids):
        return None, ["Teaser descartado: IDs de linha inválidos."]
    if any(not 0 <= item < len(units) for item in ids):
        return None, ["Teaser descartado: IDs de linha fora da transcrição."]
    if any(later != earlier + 1 for earlier, later in zip(ids, ids[1:])):
        return None, ["Teaser descartado: as linhas não são consecutivas."]
    if not any(piece["from_id"] <= ids[0] and ids[-1] <= piece["to_id"] for piece in pieces):
        return None, ["Teaser descartado: as linhas não estão dentro de uma única peça do corte."]
    start, end = float(units[ids[0]]["start"]), float(units[ids[-1]]["end"])
    if not any(
        start >= float(piece["start"]) - 0.01 and end <= float(piece["end"]) + 0.01
        for piece in pieces
    ):
        return None, ["Teaser descartado: o trecho ficou fora das peças após o ajuste do início."]
    duration = end - start
    if duration < _TEASER_MIN_SECONDS:
        return None, [f"Teaser descartado: {duration:.1f}s é curto demais (mínimo {_TEASER_MIN_SECONDS}s)."]
    if duration > _TEASER_MAX_SECONDS:
        return None, [f"Teaser descartado: {duration:.1f}s é longo demais (máximo {_TEASER_MAX_SECONDS}s)."]
    text = " ".join(str(unit["text"]) for unit in units[ids[0]:ids[-1] + 1])
    return {"start": start, "end": end, "text": text}, []


def _headline_options(clip: dict[str, Any]) -> list[str]:
    raw = clip.get("headlines")
    if not isinstance(raw, list):
        raw = [clip.get("headline")]
    options: list[str] = []
    for item in raw:
        text = " ".join(str(item or "").split())[:120]
        if text and text.casefold() not in {option.casefold() for option in options}:
            options.append(text)
    return options[:3]


def _resolve_clip(
    clip: dict[str, Any], *, units: list[dict[str, Any]], minimum_seconds: float | None,
    maximum_seconds: float | None, duration_seconds: float | None,
    max_envelope_seconds: float | None, words: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    score = clip.get("score")
    raw_pieces = clip.get("pieces")
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 10:
        return None
    if not isinstance(raw_pieces, list) or not raw_pieces or len(raw_pieces) > _MAX_PIECES:
        return None
    headlines = _headline_options(clip)
    if not headlines:
        return None
    pieces, dropped = _resolve_pieces(raw_pieces, units)
    if not pieces:
        return None
    lead_trimmed: list[str] = []
    if words:
        pieces[0], lead_trimmed = _trim_leading_fillers(pieces[0], words)
    total = sum(piece["end"] - piece["start"] for piece in pieces)
    if minimum_seconds is not None and total < float(minimum_seconds):
        return None
    if maximum_seconds is not None and total > float(maximum_seconds):
        return None
    envelope = pieces[-1]["end"] - pieces[0]["start"]
    if max_envelope_seconds is not None and envelope > float(max_envelope_seconds):
        return None
    if duration_seconds is not None and pieces[-1]["end"] > float(duration_seconds):
        return None
    emphasis = [str(item) for item in (clip.get("emphasis") or []) if str(item).strip()][:6]
    teaser, teaser_warnings = _resolve_teaser(clip.get("teaser_ids"), pieces, units)
    return {
        **clip, "pieces": pieces, "teaser": teaser, "headlines": headlines, "emphasis": emphasis,
        "emphasis_word_indices": _emphasis_indices(emphasis, pieces, words) if words else [],
        "lead_trimmed": " ".join(lead_trimmed),
        "warnings": [*(clip.get("warnings") or []), *dropped, *_piece_warnings(clip, pieces), *teaser_warnings],
    }


def _drop_overlaps(clips: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    ordered = sorted(
        clips, key=lambda clip: (-int(clip["score"]), _clip_ranges(clip)[0][0]),
    )
    kept: list[dict[str, Any]] = []
    for clip in ordered:
        ranges = _clip_ranges(clip)
        if not any(_ranges_overlap(ranges, _clip_ranges(other)) for other in kept):
            kept.append(clip)
    return kept, len(clips) - len(kept)


def _normalize_clip(rank: int, clip: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    pieces = clip["pieces"]
    start, end = float(pieces[0]["start"]), float(pieces[-1]["end"])
    first_words = str(pieces[0]["text"]).split()
    hook_quote = str(clip.get("hook_quote") or " ".join(first_words[:14]))
    last_words = str(pieces[-1]["text"]).split()
    payoff_quote = str(clip.get("payoff_quote") or " ".join(last_words[-14:]))
    headlines = clip.get("headlines") or [clip.get("headline") or clip["title"]]
    hook_end = min(start + 5.0, float(pieces[0]["end"]))
    payoff_start = max(end - 8.0, float(pieces[-1]["start"]))
    count = len(pieces)

    def purpose(index: int) -> str:
        if index == 0:
            return "hook"
        return "payoff" if index == count - 1 else "development"

    return {
        "rank": rank,
        "title": clip["title"],
        "headline": headlines[0],
        "headline_options": list(headlines),
        "emphasis": list(clip.get("emphasis") or []),
        "emphasis_word_indices": list(clip.get("emphasis_word_indices") or []),
        "lead_trimmed": clip.get("lead_trimmed") or "",
        "teaser": clip.get("teaser"),
        "start_second": start,
        "end_second": end,
        "estimated_duration": sum(float(p["end"]) - float(p["start"]) for p in pieces),
        "primary_speaker": brief.get("primary_subject") or "Dayon",
        "pacing": "dynamic",
        "pieces": [dict(piece) for piece in pieces],
        "transcript_text": " … ".join(str(piece["text"]) for piece in pieces),
        "connective_logic": str(clip.get("connective_logic") or ""),
        "hook": {
            "start_second": start, "end_second": hook_end,
            "summary": hook_quote, "evidence": hook_quote,
        },
        "context": {
            "start_second": start, "end_second": end,
            "summary": clip.get("reason") or clip["title"], "evidence": hook_quote,
        },
        "payoff": {
            "start_second": payoff_start, "end_second": end,
            "summary": payoff_quote, "evidence": payoff_quote,
        },
        "approximate_edl": [{
            "order": index, "piece": index, "source_start": float(piece["start"]),
            "source_end": float(piece["end"]),
            "purpose": purpose(index), "preferred_visual": "primary_speaker",
            "audio_mode": "source", "transition_in": "hard_cut",
            "mask_jump_with": "none", "confidence": 1.0,
            "notes": f"Peça {index + 1} de {count}; o motor de edição resolve o corte fino e o enxugamento.",
        } for index, piece in enumerate(pieces)],
        "scores": {"total": int(clip["score"])},
        "reasoning": clip["reason"],
        "warnings": list(clip.get("warnings") or []),
    }


def _validate_and_normalize_selection(
    document: dict[str, Any], *, brief: dict[str, Any], transcript: dict[str, Any],
    units: list[dict[str, Any]] | None = None, max_envelope_seconds: float | None = None,
) -> dict[str, Any]:
    if units is None:
        units = _selection_units(transcript, voice=None, primary_subject="")
    minimum_seconds = brief.get("minimum_seconds")
    maximum_seconds = brief.get("maximum_seconds")
    duration_seconds = transcript.get("duration_seconds")
    raw_clips = list(document["clips"])
    words = _flat_words(transcript)
    resolved = [
        item for item in (
            _resolve_clip(
                clip, units=units, minimum_seconds=minimum_seconds,
                maximum_seconds=maximum_seconds, duration_seconds=duration_seconds,
                max_envelope_seconds=max_envelope_seconds, words=words,
            ) for clip in raw_clips
        ) if item is not None
    ]
    malformed_count = len(raw_clips) - len(resolved)
    deduped, overlap_dropped = _drop_overlaps(resolved)
    deduped.sort(key=lambda clip: -int(clip["score"]))
    notes = str(document.get("selection_notes") or "")
    if malformed_count:
        notes += f" {malformed_count} corte(s) fora da pontuação, duração, peças ou janela do episódio foram removidos."
    if overlap_dropped:
        notes += f" {overlap_dropped} corte(s) sobrepostos com pontuação menor foram removidos."
    if not deduped:
        raise ValueError(
            "Nenhum corte válido sobrou após a validação de pontuação, duração e sobreposição. "
            "Nenhuma seleção foi salva; ajuste a direção editorial ou os limites de duração."
        )
    clips = [_normalize_clip(rank, clip, brief) for rank, clip in enumerate(deduped, start=1)]
    return {
        "schema_version": "1.0",
        "selection_notes": notes.strip(),
        "clips": clips,
    }


def provider_identity(config: CortexConfig, *, effort: str | None = None) -> dict[str, Any]:
    """Only what changes the editorial output belongs in cache keys: binary paths
    and timeouts must not invalidate (and re-bill) cached selections."""
    ai = config.ai
    if ai.provider == "claude_code_cli":
        return {"provider": ai.provider, "model": ai.claude_model,
                "effort": effort or ai.claude_effort}
    return {"provider": ai.provider, "model": ai.codex_model,
            "effort": effort or ai.codex_reasoning_effort}


def selection_system_context(
    prompt_template: str, units: list[dict[str, Any]], transcript: dict[str, Any],
    *, episode_title: str | None, learning: str = "",
) -> str:
    """Stable part of the selection request (cached by the CLI across calls on
    the same episode): editorial instructions + the whole episode."""
    duration = transcript.get("duration_seconds")
    header = [f"Título: {episode_title}"] if episode_title else []
    if isinstance(duration, (int, float)):
        header.append(f"Duração: {float(duration) / 60:.0f} min")
    return (
        f"{prompt_template.strip()}\n\n"
        + (f"{learning.strip()}\n\n" if learning.strip() else "")
        + "## Episódio\n"
        + ("\n".join(header) + "\n\n" if header else "")
        + "## Transcrição\n" + _transcript_lines(units)
    )


def selection_request(
    brief: dict[str, Any], config: CortexConfig, *, second_round: bool,
) -> str:
    payload = {
        "brief": {key: value for key, value in brief.items() if key != "confirmed_voice"},
        "selection_rules": _selection_rules(config),
    }
    round_note = (
        "Segunda rodada: os intervalos em `brief.exclude_ranges` já foram escolhidos ou "
        "sugeridos. Devolva só cortes novos, fora deles, até completar `brief.count`.\n\n"
    ) if second_round else ""
    return (
        f"{round_note}Pedido atual:\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "Responda com o objeto JSON do schema."
    )


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

    def _select(
        self, *, brief: dict[str, Any], transcript: dict[str, Any],
        units: list[dict[str, Any]], voice: ConfirmedVoice | None,
        prompt_template: str, schema: dict[str, Any], second_round: bool,
        should_cancel: Callable[[], bool], episode_title: str | None = None,
        learning: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, list[tuple[dict[str, Any], dict[str, Any]]]]:
        system_context = selection_system_context(
            prompt_template, units, transcript, episode_title=episode_title,
            learning=learning,
        )
        prompt = selection_request(brief, self._config, second_round=second_round)
        if len(system_context) + len(prompt) > self._config.ai.max_input_chars:
            raise ValueError(
                "transcrição excede o limite do provider; análise em chunks ainda é necessária "
                f"({len(system_context) + len(prompt)} > {self._config.ai.max_input_chars} caracteres)"
            )
        try:
            generated = self._provider.generate(
                prompt, schema, should_cancel=should_cancel, system_context=system_context,
            )
        except SuggestionProviderCancelled as exc:
            raise SuggestionJobCancelled() from exc
        if should_cancel():
            raise SuggestionJobCancelled()

        errors = sorted(Draft202012Validator(schema).iter_errors(generated.document), key=str)
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path) or "root"
            raise ValueError(f"resposta da IA inválida em {location}: {first.message}")

        maximum = brief.get("maximum_seconds")
        if maximum is not None and not 0 < float(maximum) < float("inf"):
            raise ValueError("maximum_seconds deve ser positivo e finito")
        selection = _validate_and_normalize_selection(
            generated.document, brief=brief, transcript=transcript, units=units,
            max_envelope_seconds=self._config.ai.selection_max_envelope_seconds,
        )

        voice_validation = None
        metrics: list[tuple[dict[str, Any], dict[str, Any]]] = []
        if voice is not None:
            turns = [turn.model_dump() for turn in voice.turns]
            fresh = []
            for clip in selection["clips"]:
                envelope_share = subject_share(
                    turns, voice.speaker, clip["start_second"], clip["end_second"],
                )
                editorial_share = subject_share_for_ranges(
                    turns, voice.speaker,
                    ((item["source_start"], item["source_end"])
                     for item in clip["approximate_edl"] if item["audio_mode"] != "room_tone"),
                )
                accepted = editorial_share >= 0.5 and (
                    len(clip["pieces"]) > 1 or envelope_share >= 0.5
                )
                metrics.append((clip, {
                    "clip_rank": clip["rank"],
                    "envelope_subject_share": envelope_share,
                    "editorial_subject_share": editorial_share,
                    "accepted": accepted,
                }))
                if accepted:
                    fresh.append(clip)
            if not fresh:
                envelope_failures = sum(
                    len(c["pieces"]) == 1 and m["envelope_subject_share"] < 0.5
                    for c, m in metrics
                )
                editorial_failures = sum(m["editorial_subject_share"] < 0.5 for _, m in metrics)
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
            }
            selection["clips"] = fresh
        excluded = brief.get("exclude_ranges") or []
        if excluded:
            clips = selection["clips"]
            excluded_ranges = [(float(old["start"]), float(old["end"])) for old in excluded]
            fresh = [
                clip for clip in clips
                if not _ranges_overlap(_clip_ranges(clip), excluded_ranges)
            ]
            if not fresh:
                raise ValueError("A seleção repetiu trechos já sugeridos. Nenhum corte novo foi salvo; ajuste a direção editorial.")
            if len(fresh) != len(clips):
                selection["selection_notes"] += " Trechos já sugeridos foram removidos."
                selection["clips"] = fresh
        return selection, generated.provenance, voice_validation, metrics

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
        prompt_path = _PROJECT_ROOT / "prompts/clip_selection.pt-BR.md"
        schema_path = _PROJECT_ROOT / "prompts/clip_selection.v5.schema.json"
        prompt_template = prompt_path.read_text(encoding="utf-8")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        units = _selection_units(
            transcript, voice=voice, primary_subject=str(brief.get("primary_subject") or "Dayon"),
        )

        episode_title = None
        try:
            asset = self._domain.get_source_asset(transcript_artifact.source_asset_id)
            episode_title = (asset.original_filename or "").strip() or None
        except SourceAssetNotFoundError:
            pass

        learning = build_learning_context(self._domain)

        # The model never sees the (huge) diarization turns, but the hash must change with the voice.
        request_brief = {key: value for key, value in brief.items() if key != "confirmed_voice"}
        prompt_sha256 = hashlib.sha256(prompt_template.encode()).hexdigest()
        input_hash = _sha256_json({
            "artifact_version": SUGGESTION_ARTIFACT_VERSION,
            "duration_validation_version": 2,
            "transcript_sha256": transcript_artifact.audio_sha256,
            "prompt_sha256": prompt_sha256,
            "schema_sha256": hashlib.sha256(
                json.dumps(schema, sort_keys=True).encode()
            ).hexdigest(),
            "provider": provider_identity(self._config),
            "selection_mode": "composed_v2",
            "selection_rules": _selection_rules(self._config),
            "request": {
                "brief": request_brief,
                "system_context_sha256": hashlib.sha256(selection_system_context(
                    prompt_template, units, transcript, episode_title=episode_title,
                    learning=learning,
                ).encode()).hexdigest(),
            },
            **({"voice_attribution_version": VOICE_ATTRIBUTION_VERSION,
                "confirmed_voice": brief["confirmed_voice"]} if voice else {}),
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

        progress_cb(5.0, "Preparando seleção editorial")
        select_kwargs = dict(
            transcript=transcript, units=units, voice=voice,
            prompt_template=prompt_template, schema=schema, should_cancel=should_cancel,
            episode_title=episode_title, learning=learning,
        )
        selection, provenance, voice_validation, metrics = self._select(
            brief=brief, second_round=False, **select_kwargs,
        )
        progress_cb(80.0, "Validando resposta estruturada")

        requested = brief.get("count")
        top_up = None
        if isinstance(requested, int) and len(selection["clips"]) < 0.6 * requested:
            missing = requested - len(selection["clips"])
            progress_cb(90.0, "Buscando mais cortes")
            top_up_brief = {
                **brief,
                "count": missing,
                "exclude_ranges": [
                    *(brief.get("exclude_ranges") or []),
                    *({"start": start, "end": end}
                      for clip in selection["clips"] for start, end in _clip_ranges(clip)),
                ],
            }
            try:
                extra, extra_provenance, _, extra_metrics = self._select(
                    brief=top_up_brief, second_round=True, **select_kwargs,
                )
            except SuggestionJobCancelled:
                raise
            except Exception as exc:  # the first round is already a usable result
                selection["selection_notes"] = (
                    f"{selection['selection_notes']} Segunda rodada de cortes falhou "
                    f"({type(exc).__name__}: {exc}); mantido o resultado da primeira."
                ).strip()
                top_up = {"requested": missing, "added": 0, "error": str(exc)}
            else:
                first_ranges = [r for clip in selection["clips"] for r in _clip_ranges(clip)]
                extra["clips"] = [
                    clip for clip in extra["clips"]
                    if not _ranges_overlap(_clip_ranges(clip), first_ranges)
                ]
                merged = sorted(
                    [*selection["clips"], *extra["clips"]],
                    key=lambda clip: -clip["scores"]["total"],
                )
                for rank, clip in enumerate(merged, start=1):
                    clip["rank"] = rank
                selection["clips"] = merged
                added = len(extra["clips"])
                selection["selection_notes"] = (
                    f"{selection['selection_notes']} {added} corte(s) adicionados em uma "
                    "segunda rodada."
                ).strip()
                top_up = {"requested": missing, "added": added, "provenance": extra_provenance}
                metrics = [*metrics, *extra_metrics]
        if voice_validation is not None:
            kept = {id(clip) for clip in selection["clips"]}
            voice_validation["clips"] = [
                {**metric, "clip_rank": clip["rank"]} if id(clip) in kept else metric
                for clip, metric in metrics
            ]

        artifact_document = {
            "artifact_schema_version": SUGGESTION_ARTIFACT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input_hash": input_hash,
            "transcript_artifact_id": transcript_artifact.id,
            "analysis_artifact_id": analysis_artifact.id if analysis_artifact else None,
            "provenance": {
                **provenance,
                "prompt_sha256": prompt_sha256,
                "schema_id": schema.get("$id"),
                **({"top_up": top_up} if top_up is not None else {}),
            },
            "selection": selection,
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
                "provenance": provenance,
                "clip_count": len(selection["clips"]),
            },
        ))
        progress_cb(100.0, "Sugestões editoriais concluídas")
        return {
            "cached": False,
            "suggestion_artifact_id": artifact.id,
            "suggestion_path": artifact.path,
            "selection": selection,
            "provenance": artifact_document["provenance"],
        }
