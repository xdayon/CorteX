from __future__ import annotations

from cortex.analyze.fillers import detect_fillers
from cortex.transcribe.schemas import TranscriptSegment, TranscriptWord


def _segment(words: list[TranscriptWord]) -> TranscriptSegment:
    start = words[0].start if words else 0.0
    end = words[-1].end if words else 0.0
    return TranscriptSegment(
        id=0, start=start, end=end, text=" ".join(w.word for w in words),
        avg_logprob=-0.1, no_speech_prob=0.02, words=words,
    )


def test_isolated_hesitation_between_pauses_is_flagged():
    words = [
        TranscriptWord(start=0.0, end=0.5, word="Eu", probability=0.9),
        TranscriptWord(start=1.2, end=1.5, word="eh", probability=0.6),
        TranscriptWord(start=2.3, end=2.8, word="acho", probability=0.9),
    ]
    fillers = detect_fillers([_segment(words)])

    assert len(fillers) == 1
    assert fillers[0].word == "eh"
    assert fillers[0].kind == "hesitation"
    assert fillers[0].start == 1.2 and fillers[0].end == 1.5


def test_ne_mid_sentence_without_surrounding_pause_is_not_flagged():
    words = [
        TranscriptWord(start=0.0, end=0.3, word="Isso", probability=0.9),
        TranscriptWord(start=0.3, end=0.5, word="é", probability=0.9),
        TranscriptWord(start=0.5, end=0.65, word="né", probability=0.9),
        TranscriptWord(start=0.65, end=0.9, word="verdade", probability=0.9),
    ]
    fillers = detect_fillers([_segment(words)])

    assert fillers == []


def test_ne_isolated_between_pauses_is_flagged():
    words = [
        TranscriptWord(start=0.0, end=0.4, word="Isso", probability=0.9),
        TranscriptWord(start=1.0, end=1.2, word="né", probability=0.9),
        TranscriptWord(start=2.0, end=2.4, word="mesmo", probability=0.9),
    ]
    fillers = detect_fillers([_segment(words)])

    assert len(fillers) == 1
    assert fillers[0].word == "né"


def test_immediate_word_repetition_is_flagged():
    words = [
        TranscriptWord(start=0.0, end=0.2, word="eu", probability=0.9),
        TranscriptWord(start=0.2, end=0.4, word="eu", probability=0.9),
        TranscriptWord(start=0.4, end=0.7, word="vou", probability=0.9),
    ]
    fillers = detect_fillers([_segment(words)])

    assert len(fillers) == 1
    assert fillers[0].kind == "repetition"
    assert fillers[0].start == 0.2 and fillers[0].end == 0.4


def test_transcript_without_words_returns_empty_list():
    segment = TranscriptSegment(
        id=0, start=0.0, end=1.0, text="sem palavras", avg_logprob=-0.1,
        no_speech_prob=0.02, words=[],
    )

    assert detect_fillers([segment]) == []
    assert detect_fillers([]) == []
