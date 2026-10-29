"""Word-level alignment of the narration to the script beats, and the pilot's cut rule."""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from cortex.config import CortexConfig
from cortex.lessons.common import LessonError
from cortex.lessons.spec import Beat, LessonSpec, load_spec

LONG_FIRST_WORD = 0.6  # a first word longer than this is a held syllable: the onset is near its end
ONSET_BACKOFF = 0.35
CUT_BEFORE_ONSET = 0.2
MIN_GAP_AFTER_LAST_WORD = 0.05
END_TAIL = 0.7  # breathing room after the final spoken word, before the end card

Word = tuple[float, float, str]


@dataclass(frozen=True)
class BeatSpan:
    first_start: float
    first_end: float
    last_end: float


def _norm(word: str) -> str:
    """Lowercase ASCII token: accents dropped (conspiração -> conspiracao), punctuation removed."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", word.lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", plain)


def beat_spans(words: list[Word], beat_texts: list[str]) -> list[BeatSpan]:
    """Match the heard words to the script (difflib on normalised tokens) and return per-beat first/last word."""
    ref = [(k, _norm(w)) for k, text in enumerate(beat_texts) for w in text.split() if _norm(w)]
    hyp = [(s, e, _norm(w)) for s, e, w in words if _norm(w)]
    sm = difflib.SequenceMatcher(a=[r[1] for r in ref], b=[h[2] for h in hyp], autojunk=False)
    first: dict[int, tuple[float, float]] = {}
    last: dict[int, float] = {}
    for a, b, n in sm.get_matching_blocks():
        for i in range(n):
            k = ref[a + i][0]
            first.setdefault(k, (hyp[b + i][0], hyp[b + i][1]))
            last[k] = hyp[b + i][1]
    missing = [k for k in range(len(beat_texts)) if k not in first]
    if missing:
        raise LessonError(f"no narration words matched for beat index {missing}")
    return [BeatSpan(first[k][0], first[k][1], last[k]) for k in range(len(beat_texts))]


def cut_timings(spans: list[BeatSpan | None], beats: list[Beat], narration_end: float, lead_in: float) -> dict[str, dict[str, float]]:
    """Per-beat {start, duration} in video time (every value rounded to 0.01 s).

    Narrated beat k > 0 starts at its cut:
        onset = first word start, or first word end - 0.35 when that word lasts > 0.6 s
        cut   = max(previous beat's last word end + 0.05, onset - 0.2) + lead_in
    The first beat starts at 0.0 (the narration itself is delayed by lead_in). Each beat lasts until the next
    start. The last narrated beat ends at narration_end + lead_in + END_TAIL, where narration_end is the end
    of the final heard word (pilot: 163.68 + 0.8 + 0.7 = 165.18). A silent beat (hold) lasts exactly hold and
    must come after all narrated beats. `spans` has None for silent beats.
    """
    narrated = [i for i, b in enumerate(beats) if b.hold is None]
    if narrated != list(range(len(narrated))):
        raise LessonError("beats with 'hold' (silent) must come after all narrated beats")
    starts: list[float] = []
    for k in narrated:
        if k == 0:
            starts.append(0.0)
            continue
        span, prev = spans[k], spans[k - 1]
        onset = span.first_start
        if span.first_end - span.first_start > LONG_FIRST_WORD:
            onset = span.first_end - ONSET_BACKOFF
        starts.append(round(max(prev.last_end + MIN_GAP_AFTER_LAST_WORD, onset - CUT_BEFORE_ONSET) + lead_in, 2))
    end = round(narration_end + lead_in + END_TAIL, 2)
    scenes: dict[str, dict[str, float]] = {}
    for n, k in enumerate(narrated):
        nxt = starts[n + 1] if n + 1 < len(starts) else end
        scenes[beats[k].id] = {"start": starts[n], "duration": round(nxt - starts[n], 2)}
    cursor = end
    for b in beats[len(narrated):]:
        scenes[b.id] = {"start": cursor, "duration": round(b.hold, 2)}
        cursor = round(cursor + b.hold, 2)
    return scenes


def timings_payload(scenes: dict, lead_in: float, source: str) -> dict:
    last = max(scenes.values(), key=lambda s: s["start"])
    return {"source": source, "audio_offset": lead_in, "total": round(last["start"] + last["duration"], 2), "scenes": scenes}


def words_from_alignment(data: dict) -> list[Word]:
    return [(float(s), float(e), str(w)) for s, e, w in data["words"]]


def timings_from_words(words: list[Word], beats: list[Beat], lead_in: float, source: str) -> dict:
    narrated = [b for b in beats if b.hold is None]
    spans = beat_spans(words, [b.text for b in narrated])
    spans_all: list[BeatSpan | None] = [*spans, *([None] * (len(beats) - len(narrated)))]
    narration_end = max(e for _, e, w in words if _norm(w))
    return timings_payload(cut_timings(spans_all, beats, narration_end, lead_in), lead_in, source)


def transcribe_words(cfg: CortexConfig, audio: Path, device: str | None, language: str) -> tuple[list[Word], dict]:
    from cortex.transcribe.engine import FasterWhisperEngine

    tcfg = cfg.transcription
    requested = device or tcfg.device
    result = FasterWhisperEngine().transcribe(
        audio, model=tcfg.model, language=language, device=requested,
        compute_type=tcfg.compute_type, batch_size=0, vad=False, allow_cpu_fallback=False,
    )
    info = result.engine_info
    words = [(w.start, w.end, w.word) for seg in result.segments for w in seg.words]
    if not words:
        raise LessonError("transcription returned no words")
    return words, {"model": info.model, "requested_device": info.requested_device,
                   "effective_device": info.effective_device, "compute_type": info.effective_compute_type}


def align_lesson(cfg: CortexConfig, directory: Path, *, device: str | None = None, out_name: str = "timings.json") -> dict:
    spec: LessonSpec = load_spec(directory)
    audio = directory / spec.narration
    lead = cfg.lessons.lead_in_seconds
    words, engine = transcribe_words(cfg, audio, device, spec.language)
    source = f"{spec.narration} alignment (faster-whisper words), lead-in {lead:g} s"
    # Saved first: when the script and the take disagree, the heard words show where.
    (directory / "alignment.json").write_text(json.dumps(
        {"engine": engine, "language": spec.language, "end": max(e for _, e, _ in words), "words": words}), encoding="utf-8")
    timings = timings_from_words(words, spec.beats, lead, source)
    (directory / out_name).write_text(json.dumps(timings, indent=1) + "\n", encoding="utf-8")
    return timings
