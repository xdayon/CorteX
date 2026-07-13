"""Deterministic PT-BR disfluency (filler word) detection.

Operates only on word-level timestamps from the transcript
(`TranscriptSegment.words`). No LLM is involved; the lexicon below is
versioned so that any change invalidates cached analysis artifacts.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from cortex.transcribe.schemas import TranscriptSegment, TranscriptWord

FILLER_LEXICON_VERSION = "1.0.0"

# Pure disfluencies: essentially never a meaningful standalone word in PT-BR,
# so they are always flagged regardless of surrounding pauses.
_PURE_HESITATIONS = frozenset({
    "eh", "ah", "ãh", "ã", "hum", "hã", "uhm", "uh", "hmm", "hmmm", "ahn",
})

# Words that are only disfluencies when isolated between pauses (i.e. spoken
# on their own, not as part of a sentence). Marking these unconditionally
# would flag legitimate uses ("é ele", "né que sim").
_CONTEXTUAL_ISOLATED = frozenset({"é", "né"})

_DEFAULT_PAUSE_THRESHOLD_SECONDS = 0.35

_STRIP_CHARS = ".,!?;:…\"'()[]“”‘’-–—"


class Filler(BaseModel):
    model_config = ConfigDict(extra="forbid")

    word: str
    start: float
    end: float
    kind: str  # "hesitation" | "repetition"
    confidence: float = Field(ge=0, le=1)


def _normalize(word: str) -> str:
    return word.strip().strip(_STRIP_CHARS).lower()


def detect_fillers(
    segments: list[TranscriptSegment],
    *,
    pause_threshold: float = _DEFAULT_PAUSE_THRESHOLD_SECONDS,
) -> list[Filler]:
    """Detects hesitations and immediate word repetitions from word timings.

    Returns an empty list when the transcript has no word-level timestamps.
    """
    flat: list[TranscriptWord] = [word for segment in segments for word in segment.words]
    if not flat:
        return []

    fillers: list[Filler] = []
    for index, word in enumerate(flat):
        normalized = _normalize(word.word)
        if not normalized:
            continue

        if normalized in _PURE_HESITATIONS:
            fillers.append(Filler(
                word=word.word, start=word.start, end=word.end,
                kind="hesitation", confidence=0.9,
            ))
            continue

        if normalized in _CONTEXTUAL_ISOLATED:
            gap_before = word.start - flat[index - 1].end if index > 0 else float("inf")
            gap_after = flat[index + 1].start - word.end if index < len(flat) - 1 else float("inf")
            if gap_before >= pause_threshold and gap_after >= pause_threshold:
                fillers.append(Filler(
                    word=word.word, start=word.start, end=word.end,
                    kind="hesitation", confidence=0.7,
                ))
            continue

        if index > 0:
            previous_normalized = _normalize(flat[index - 1].word)
            if previous_normalized and previous_normalized == normalized:
                fillers.append(Filler(
                    word=word.word, start=word.start, end=word.end,
                    kind="repetition", confidence=0.85,
                ))

    return fillers
