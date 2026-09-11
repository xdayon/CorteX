from __future__ import annotations

import re
from dataclasses import dataclass

from cortex.edit.schemas import EditPlanDocument
from cortex.transcribe.schemas import TranscriptDocument


@dataclass(frozen=True)
class TimelineWord:
    start: float
    end: float
    text: str
    segment: int


@dataclass(frozen=True)
class CaptionCue:
    start: float
    end: float
    text: str
    words: tuple[TimelineWord, ...] = ()


def build_timeline_words(
    plan: EditPlanDocument, transcript: TranscriptDocument
) -> list[TimelineWord]:
    source_words = [word for segment in transcript.segments for word in segment.words]
    mapped: list[TimelineWord] = []
    timeline_start = 0.0
    for index, segment in enumerate(plan.segments):
        if index:
            previous_transition = plan.segments[index - 1].transition
            timeline_start -= previous_transition.duration if previous_transition else 0.0
        for word in source_words:
            start = max(word.start, segment.start)
            end = min(word.end, segment.end)
            text = re.sub(r"\s+", " ", word.word).strip()
            if text and end > start:
                mapped.append(TimelineWord(
                    start=round(timeline_start + start - segment.start, 3),
                    end=round(timeline_start + end - segment.start, 3),
                    text=text,
                    segment=index,
                ))
        timeline_start += segment.end - segment.start
    ordered = sorted(mapped, key=lambda item: (item.start, item.end, item.segment))
    # Whisper artifacts can contain the same word twice at an identical boundary
    # when adjacent transcript segments overlap.  Removing only an exact mapped
    # duplicate prevents a caption line from repeating while preserving genuine
    # spoken repetitions at consecutive timestamps.
    unique: list[TimelineWord] = []
    seen: set[tuple[float, float, str]] = set()
    for word in ordered:
        key = (word.start, word.end, word.text.casefold())
        if key not in seen:
            seen.add(key)
            unique.append(word)
    return unique


def build_caption_cues(
    plan: EditPlanDocument,
    transcript: TranscriptDocument,
    *,
    max_words: int = 5,
    max_duration: float = 3.0,
    gap_threshold: float = 0.65,
) -> list[CaptionCue]:
    words = build_timeline_words(plan, transcript)
    chunks: list[list[TimelineWord]] = []
    current: list[TimelineWord] = []
    for word in words:
        split = bool(current) and (
            len(current) >= max_words
            or word.segment != current[-1].segment
            or word.start - current[-1].end > gap_threshold
            or word.end - current[0].start > max_duration
        )
        if split:
            chunks.append(current)
            current = []
        current.append(word)
    if current:
        chunks.append(current)

    cues: list[CaptionCue] = []
    for chunk in chunks:
        start = round(max(0.0, chunk[0].start), 3)
        end = round(min(plan.timeline_duration_seconds, chunk[-1].end), 3)
        if cues and start < cues[-1].end:
            previous = cues[-1]
            boundary = max(previous.start + 0.05, start)
            cues[-1] = CaptionCue(
                previous.start, boundary, previous.text, previous.words
            )
            start = boundary
        if end > start:
            cues.append(CaptionCue(
                start,
                end,
                " ".join(word.text for word in chunk),
                tuple(chunk),
            ))
    return cues


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_srt(cues: list[CaptionCue]) -> str:
    blocks = [
        f"{index}\n{_srt_timestamp(cue.start)} --> {_srt_timestamp(cue.end)}\n{cue.text}"
        for index, cue in enumerate(cues, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def corrected_transcript(transcript: TranscriptDocument, corrections) -> TranscriptDocument:
    """Apply caption-only word edits without mutating source text or timestamps."""
    if not corrections:
        return transcript
    result = transcript.model_copy(deep=True)
    words = [w for segment in result.segments for w in segment.words]
    seen = set()
    for correction in corrections:
        index = correction.word_index
        if index in seen or index >= len(words) or words[index].word != correction.original:
            raise ValueError("Correção não corresponde à transcrição original; reabra o editor")
        seen.add(index)
        words[index].word = re.sub(r"\s+", " ", correction.text).strip()
    return result
