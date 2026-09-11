from cortex.edit.schemas import (
    EditPlanDiagnostics,
    EditPlanDocument,
    EditQualityReport,
    EditSegment,
    EditTransition,
)
from cortex.render.captions import build_caption_cues, build_timeline_words, render_srt
from cortex.transcribe.schemas import EngineInfo, TranscriptDocument, TranscriptSegment, TranscriptWord


def test_caption_cues_remap_source_words_to_crossfaded_timeline() -> None:
    plan = EditPlanDocument(
        project_id="project", source_asset_id="source", transcript_artifact_id="transcript",
        analysis_artifact_id="analysis", input_hash="hash", clip_start=0, clip_end=8,
        profile="balanced",
        segments=[
            EditSegment(
                start=1, end=3, timeline_order=0,
                transition=EditTransition(video="fade", audio="fade", duration=0.1),
            ),
            EditSegment(start=6, end=8, timeline_order=1),
        ],
        timeline_duration_seconds=3.9,
        diagnostics=EditPlanDiagnostics(
            profile="balanced", waveform_used=True, candidate_pauses=1, cuts=1,
            saved_seconds=3.9, crossfade=0.1, vad_used=True,
        ),
        quality=EditQualityReport(passed=True, issues=[], profile="balanced", degraded=False),
    )
    transcript = TranscriptDocument(
        duration_seconds=8,
        engine=EngineInfo(
            model="fixture", requested_device="cpu", effective_device="cpu",
            requested_compute_type="int8", effective_compute_type="int8",
            batch_size=1, vad=True,
        ),
        segments=[TranscriptSegment(
            id=0, start=0, end=8, text="fora primeiro removido segundo",
            avg_logprob=-0.1, no_speech_prob=0,
            words=[
                TranscriptWord(start=0.2, end=0.5, word="fora", probability=1),
                TranscriptWord(start=1.2, end=1.6, word="primeiro", probability=1),
                TranscriptWord(start=4, end=4.4, word="removido", probability=1),
                TranscriptWord(start=6.2, end=6.7, word="segundo", probability=1),
            ],
        )],
    )

    from cortex.render.captions import corrected_transcript
    from cortex.render.schemas import CaptionCorrection
    import pytest
    corrected = corrected_transcript(transcript, [CaptionCorrection(word_index=1, original="primeiro", text="CorteX")])
    assert "CorteX" in render_srt(build_caption_cues(plan, corrected))
    assert transcript.segments[0].words[1].word == "primeiro"
    assert corrected.segments[0].words[1].start == transcript.segments[0].words[1].start
    with pytest.raises(ValueError):
        corrected_transcript(transcript, [CaptionCorrection(word_index=1, original="texto errado", text="CorteX")])

    cues = build_caption_cues(plan, transcript)
    words = build_timeline_words(plan, transcript)

    assert [(word.start, word.end, word.text) for word in words] == [
        (0.2, 0.6, "primeiro"),
        (2.1, 2.6, "segundo"),
    ]
    assert [(cue.start, cue.end, cue.text) for cue in cues] == [
        (0.2, 0.6, "primeiro"),
        (2.1, 2.6, "segundo"),
    ]
    assert "00:00:02,100 --> 00:00:02,600" in render_srt(cues)


def test_caption_cues_do_not_overlap_inside_crossfade() -> None:
    plan = EditPlanDocument(
        project_id="project", source_asset_id="source", transcript_artifact_id="transcript",
        analysis_artifact_id="analysis", input_hash="hash", clip_start=0, clip_end=4,
        profile="balanced",
        segments=[
            EditSegment(
                start=0, end=2, timeline_order=0,
                transition=EditTransition(video="fade", audio="fade", duration=0.2),
            ),
            EditSegment(start=2, end=4, timeline_order=1),
        ],
        timeline_duration_seconds=3.8,
        diagnostics=EditPlanDiagnostics(
            profile="balanced", waveform_used=True, candidate_pauses=0, cuts=1,
            saved_seconds=0.2, crossfade=0.2, vad_used=True,
        ),
        quality=EditQualityReport(passed=True, issues=[], profile="balanced", degraded=False),
    )
    transcript = TranscriptDocument(
        duration_seconds=4,
        engine=EngineInfo(
            model="fixture", requested_device="cpu", effective_device="cpu",
            requested_compute_type="int8", effective_compute_type="int8",
            batch_size=1, vad=True,
        ),
        segments=[TranscriptSegment(
            id=0, start=0, end=4, text="antes depois", avg_logprob=-0.1, no_speech_prob=0,
            words=[
                TranscriptWord(start=1.7, end=2.0, word="antes", probability=1),
                TranscriptWord(start=2.0, end=2.3, word="depois", probability=1),
            ],
        )],
    )

    cues = build_caption_cues(plan, transcript, max_words=1)

    assert len(cues) == 2
    assert cues[0].end == cues[1].start
