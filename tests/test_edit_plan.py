from __future__ import annotations

from pathlib import Path

import pytest
from asgi_client import ASGITestClient as TestClient

from cortex.analyze.schemas import (
    AnalysisDocument,
    AnalysisEngineInfo,
    LoudnessMetrics,
    VadInterval,
    WaveformResolution,
)
from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact, TranscriptArtifact
from cortex.domain.store import DomainStore
from cortex.edit.boundary import EnergyTrack
from cortex.edit.planner import (
    plan_safe_segments,
    protect_segment_boundaries,
    resolve_jl_cuts,
    word_intervals,
)
from cortex.edit.profiles import PROFILES
from cortex.edit.quality import validate_edit_plan
from cortex.edit.schemas import EditPlanDocument
from cortex.edit.service import EditPlanService
from cortex.jobs import JobStore
from cortex.schemas import JobStatus
from cortex.transcribe.schemas import EngineInfo, TranscriptDocument, TranscriptSegment, TranscriptWord
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine


def _word(text: str, start: float, end: float) -> TranscriptWord:
    return TranscriptWord(start=start, end=end, word=text, probability=0.95)


def _transcript_document(words: list[TranscriptWord], duration: float) -> TranscriptDocument:
    segment = TranscriptSegment(
        id=0,
        start=words[0].start if words else 0.0,
        end=words[-1].end if words else duration,
        text=" ".join(w.word for w in words),
        avg_logprob=-0.1,
        no_speech_prob=0.01,
        words=words,
    )
    return TranscriptDocument(
        language="pt",
        language_probability=0.95,
        duration_seconds=duration,
        engine=EngineInfo(
            model="fake", requested_device="cpu", effective_device="cpu",
            requested_compute_type="int8", effective_compute_type="int8",
            batch_size=1, vad=True,
        ),
        segments=[segment],
    )


def _analysis_document(
    *,
    project_id: str,
    source_asset_id: str,
    transcript_artifact_id: str,
    duration: float,
    rms: list[float] | None = None,
    vad_intervals: list[VadInterval] | None = None,
) -> AnalysisDocument:
    sample_rate = 16000
    frame_seconds = 0.1
    frames_per_point = round(frame_seconds * sample_rate)
    frame_count = max(10, int(duration / frame_seconds) + 20)
    rms_values = rms if rms is not None else [0.5] * frame_count
    waveform = WaveformResolution(
        points=len(rms_values),
        frames_per_point=frames_per_point,
        peaks=[min(1.0, v * 1.4) for v in rms_values],
        rms=rms_values,
    )
    return AnalysisDocument(
        project_id=project_id,
        source_asset_id=source_asset_id,
        transcript_artifact_id=transcript_artifact_id,
        input_hash="fake-analysis-input-hash",
        normalized_audio_sha256="fake-audio-sha256",
        sample_rate=sample_rate,
        channels=1,
        duration_seconds=duration,
        engines=AnalysisEngineInfo(
            vad="test-vad", vad_version="0", vad_parameters={},
            waveform="test-waveform", loudness="test-loudness",
        ),
        waveform=[waveform],
        vad_intervals=vad_intervals or [],
        pauses=[],
        speech_density=[],
        overall_speech_ratio=0.5,
        loudness=LoudnessMetrics(
            integrated_lufs=-20.0, loudness_range_lu=2.0, true_peak_dbfs=-3.0,
            threshold_lufs=-30.0, engine="test-loudness",
        ),
        room_tone=[],
    )


def _config(tmp_path: Path) -> CortexConfig:
    base = load_config()
    return base.model_copy(update={
        "paths": base.paths.model_copy(update={
            "data_dir": tmp_path,
            "projects_dir": tmp_path / "projects",
            "cache_dir": tmp_path / "cache",
            "output_dir": tmp_path / "output",
            "database": tmp_path / "cortex.sqlite3",
        }),
    })


def _fixture(
    config: CortexConfig,
    domain: DomainStore,
    words: list[TranscriptWord],
    duration: float,
    *,
    rms: list[float] | None = None,
    vad_intervals: list[VadInterval] | None = None,
    source_probe: dict | None = None,
):
    project = domain.create_project("Edit plan fixture")
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="clip.wav",
        stored_path="source/clip.wav", sha256="fake-source-sha256", size_bytes=100,
        probe=source_probe or {},
    ))
    transcript_doc = _transcript_document(words, duration)
    transcript_path = config.paths.projects_dir / project.id / "transcripts" / "transcript.json"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(transcript_doc.model_dump_json(), encoding="utf-8")
    transcript = domain.create_transcript_artifact(TranscriptArtifact(
        project_id=project.id, source_asset_id=source.id, path=str(transcript_path),
        audio_sha256="fake-source-sha256", engine="fake", model="fake", device="cpu",
        compute_type="int8", language="pt", vad=True, batch_size=1, duration_seconds=duration,
    ))
    analysis_doc = _analysis_document(
        project_id=project.id, source_asset_id=source.id, transcript_artifact_id=transcript.id,
        duration=duration, rms=rms, vad_intervals=vad_intervals,
    )
    analysis_path = config.paths.projects_dir / project.id / "analysis" / "analysis.json"
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_path.write_text(analysis_doc.model_dump_json(), encoding="utf-8")
    analysis = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="analysis", schema_version=1,
        path=str(analysis_path), input_hash="fake-analysis-artifact-hash",
    ))
    return project, source, transcript, analysis


def test_edit_plan_clamps_transcript_tail_to_shortest_physical_stream(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    words = [_word("fim", 4.4, 5.1)]
    project, _source, transcript, analysis = _fixture(
        config,
        domain,
        words,
        5.2,
        source_probe={
            "streams": [
                {"codec_type": "video", "duration": "5.000000"},
                {"codec_type": "audio", "duration": "5.080000"},
            ],
            "format": {"duration": "5.200000"},
        },
    )
    result = EditPlanService(config, domain).run(
        transcript_artifact=transcript,
        analysis_artifact=analysis,
        start=4.0,
        end=5.2,
        profile="balanced",
        progress_cb=lambda *_args: None,
        should_cancel=lambda: False,
    )
    artifact = domain.get_stage_artifact(result["edit_plan_artifact_id"])
    document = EditPlanDocument.model_validate_json(Path(artifact.path).read_text(encoding="utf-8"))
    assert document.project_id == project.id
    assert document.clip_end == 5.0
    assert max(segment.video_end for segment in document.segments) <= 5.0
    assert max(segment.audio_end for segment in document.segments) <= 5.0


# -- 1. Long pause between sentences -----------------------------------------

def test_long_pause_between_phrases_cuts_with_transition_and_saving() -> None:
    profile = PROFILES["balanced"]
    words = [
        _word("isso", 0.5, 1.0),
        _word("mundo", 3.0, 3.5),
        _word("agora", 3.6, 4.0),
    ]
    energy = EnergyTrack(origin=0.0, frame_seconds=0.05, rms=[0.4] * 100)
    segments, diagnostics = plan_safe_segments(words, 0.0, 4.0, profile, energy)
    assert len(segments) == 2
    assert diagnostics["cuts"] == 1
    assert diagnostics["saved_seconds"] > 0
    assert segments[0]["timeline_order"] == 0
    assert segments[1]["timeline_order"] == 1
    assert segments[0]["transition"]["duration"] == pytest.approx(profile.crossfade)
    assert segments[0]["end"] < segments[1]["start"]


# -- 2. Rhetorical pause is preserved -----------------------------------------

def test_rhetorical_pause_at_end_of_sentence_is_preserved() -> None:
    profile = PROFILES["balanced"]
    words = [
        _word("acabou.", 0.2, 0.7),
        _word("depois", 2.0, 2.4),
        _word("voltou", 2.5, 2.9),
    ]
    energy = EnergyTrack(origin=0.0, frame_seconds=0.05, rms=[0.4] * 80)
    segments, diagnostics = plan_safe_segments(words, 0.0, 3.2, profile, energy)
    assert len(segments) == 1
    assert diagnostics["cuts"] == 0


# -- 3. Boundary inside word gets re-anchored ---------------------------------

def test_boundary_inside_word_is_snapped_by_quality_gate() -> None:
    profile = PROFILES["balanced"]
    word = _word("central", 1.0, 1.6)
    words = [word]
    # Quiet region right after the word, inside 1x boundary_radius (0.30) of
    # the offending boundary (1.58): window is [1.28, 1.88].
    rms = [0.5] * 200
    for i in range(164, 188):  # ~1.64s .. 1.88s
        rms[i] = 0.01
    energy = EnergyTrack(origin=0.0, frame_seconds=0.01, rms=rms)
    segments = [{"start": 0.5, "end": 1.58, "timeline_order": 0}]

    repaired, quality = validate_edit_plan(
        segments, words, profile, clip_start=0.5, clip_end=1.58, energy=energy,
    )

    assert quality["passed"] is True
    assert quality["degraded"] is False
    codes = {issue["code"] for issue in quality["issues"]}
    assert "boundary_snapped" in codes
    assert "boundary_inside_word" not in codes
    assert repaired[0]["end"] > 1.635  # snapped past the word's guarded end
    assert repaired[0]["end"] != 1.58


# -- 4. No safe point degrades to the full clip -------------------------------

def test_no_safe_point_within_radius_degrades_to_full_clip() -> None:
    profile = PROFILES["balanced"]
    word = _word("inteira", 1.0, 1.6)
    words = [word]
    segments = [{"start": 0.5, "end": 1.58, "timeline_order": 0}]

    # No energy track at all -> no candidate can ever be found, forcing the
    # degrade path.
    repaired, quality = validate_edit_plan(
        segments, words, profile, clip_start=0.2, clip_end=2.4, energy=None,
    )

    assert quality["passed"] is True
    assert quality["degraded"] is True
    assert repaired == [{"start": 0.2, "end": 2.4, "timeline_order": 0}]
    codes = {issue["code"] for issue in quality["issues"]}
    assert "boundary_inside_word" not in codes


# -- 5. Multi-segment plan with ordered timeline and transitions -------------

def test_multi_segment_plan_has_ordered_timeline_and_transitions() -> None:
    profile = PROFILES["dynamic"]
    words = [
        _word("um", 0.5, 1.0),
        _word("dois", 2.2, 2.6),
        _word("tres", 3.8, 4.2),
        _word("quatro", 5.4, 5.8),
    ]
    energy = EnergyTrack(origin=0.0, frame_seconds=0.05, rms=[0.4] * 140)
    segments, diagnostics = plan_safe_segments(words, 0.0, 6.3, profile, energy)

    assert diagnostics["cuts"] >= 2
    assert len(segments) >= 3
    orders = [segment["timeline_order"] for segment in segments]
    assert orders == sorted(orders) == list(range(len(segments)))
    for segment in segments[:-1]:
        assert segment["transition"]["duration"] == pytest.approx(profile.crossfade)
    assert "transition" not in segments[-1]
    for previous, following in zip(segments, segments[1:]):
        assert previous["end"] <= following["start"]

    protected = protect_segment_boundaries(segments, words, profile)
    assert len(protected) == len(segments)


# -- 6. Cache by input hash ----------------------------------------------------

def test_edit_plan_service_caches_by_input_hash(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    words = [
        _word("isso", 0.5, 1.0),
        _word("mundo", 3.0, 3.5),
        _word("agora", 3.6, 4.0),
    ]
    rms = [0.4] * 60
    _project, _source, transcript, analysis = _fixture(config, domain, words, 4.0, rms=rms)
    service = EditPlanService(config, domain)
    progress: list[float] = []

    first = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced",
        progress_cb=lambda value, _message: progress.append(value),
        should_cancel=lambda: False,
    )
    assert first["cached"] is False
    assert progress == sorted(progress) and progress[-1] == 100.0
    document = EditPlanDocument.model_validate_json(Path(first["edit_plan_path"]).read_text())
    assert document.schema_version == 2
    assert len(domain.list_stage_artifacts(transcript.project_id, "edit_plan")) == 1

    second = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced",
        progress_cb=lambda _value, _message: None, should_cancel=lambda: False,
    )
    assert second["cached"] is True
    assert second["edit_plan_artifact_id"] == first["edit_plan_artifact_id"]
    assert len(domain.list_stage_artifacts(transcript.project_id, "edit_plan")) == 1


# -- 7. API endpoints -----------------------------------------------------------

def test_edit_plan_endpoint_creates_job_and_get_returns_document(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    words = [
        _word("isso", 0.5, 1.0),
        _word("mundo", 3.0, 3.5),
        _word("agora", 3.6, 4.0),
    ]
    rms = [0.4] * 60
    project, _source, transcript, analysis = _fixture(config, domain, words, 4.0, rms=rms)
    client = TestClient(create_app(config))

    response = client.post(
        f"/api/v1/projects/{project.id}/edit-plans",
        json={
            "transcript_artifact_id": transcript.id,
            "analysis_artifact_id": analysis.id,
            "start": 0.0,
            "end": 4.0,
            "profile": "balanced",
        },
    )
    assert response.status_code == 201, response.text
    queued = response.json()
    assert queued["type"] == "edit_plan" and queued["status"] == "queued"

    jobs = JobStore(config.paths.database)
    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued["id"])
    assert final.status == JobStatus.SUCCEEDED, final.error
    assert final.result is not None and final.result["cached"] is False

    artifact_id = final.result["edit_plan_artifact_id"]
    fetched = client.get(f"/api/v1/projects/{project.id}/edit-plans/{artifact_id}")
    assert fetched.status_code == 200, fetched.text
    payload = fetched.json()
    assert payload["artifact"]["stage"] == "edit_plan"
    assert payload["document"]["schema_version"] == 2
    assert payload["document"]["quality"]["passed"] is True


# -- 8. J/L-cut resolution (Gate 4, Session G) --------------------------------


def _jl_segments() -> list[dict]:
    return [
        {
            "start": 0.0, "end": 2.0, "timeline_order": 0,
            "transition": {"video": "micro_dissolve", "audio": "equal_power_crossfade", "duration": 0.06},
        },
        {"start": 2.5, "end": 4.0, "timeline_order": 1},
    ]


def test_resolve_jl_cuts_applies_offset_and_classifies_kind() -> None:
    words = [_word("um", 1.0, 1.8), _word("dois", 2.7, 3.2)]
    rms = [0.5] * 500
    for i in range(180, 195):
        rms[i] = 0.01
    energy = EnergyTrack(origin=0.0, frame_seconds=0.01, rms=rms)
    forbidden = word_intervals(words)
    expected_candidate = energy.quiet_boundary(2.0, 0.30, forbidden)
    expected_offset = round(expected_candidate - 2.0, 3)
    assert abs(expected_offset) > 0.001  # sanity: fixture actually moves the boundary

    resolved, issues, counts = resolve_jl_cuts(_jl_segments(), words, [], energy, 0.30)

    assert issues == []
    transition = resolved[0]["transition"]
    assert transition["audio_offset_seconds"] == pytest.approx(expected_offset)
    expected_kind = "j_cut" if expected_offset < 0 else "l_cut"
    assert transition["kind"] == expected_kind
    assert counts[f"{expected_kind}s"] == 1
    assert counts["jl_blocked"] == 0
    assert resolved[0]["audio_end"] == pytest.approx(round(2.0 + expected_offset, 3))
    assert resolved[1]["audio_start"] == pytest.approx(round(2.5 + expected_offset, 3))
    # boundaries with no neighbor never carry an offset (see coverage invariant)
    assert resolved[0]["audio_start"] == resolved[0]["start"]
    assert resolved[1]["audio_end"] == resolved[1]["end"]
    # audio duration gained by one segment is exactly what its neighbor lost
    total_video = (resolved[0]["end"] - resolved[0]["start"]) + (resolved[1]["end"] - resolved[1]["start"])
    total_audio = (resolved[0]["audio_end"] - resolved[0]["audio_start"]) + (
        resolved[1]["audio_end"] - resolved[1]["audio_start"]
    )
    assert total_audio == pytest.approx(total_video)


def test_resolve_jl_cuts_blocks_when_entire_window_is_forbidden_by_word() -> None:
    words = [_word("continuo", 1.7, 2.3)]  # covers the whole +/-0.30 search radius
    energy = EnergyTrack(origin=0.0, frame_seconds=0.01, rms=[0.5] * 500)

    resolved, issues, counts = resolve_jl_cuts(_jl_segments(), words, [], energy, 0.30)

    assert counts == {"j_cuts": 0, "l_cuts": 0, "jl_blocked": 1}
    assert len(issues) == 1
    assert issues[0]["code"] == "jl_no_safe_boundary"
    transition = resolved[0]["transition"]
    assert transition.get("kind") is None
    assert transition.get("audio_offset_seconds") is None
    assert resolved[0]["audio_end"] == resolved[0]["end"]
    assert resolved[1]["audio_start"] == resolved[1]["start"]


def test_resolve_jl_cuts_blocks_when_entire_window_is_forbidden_by_vad() -> None:
    vad_intervals = [VadInterval(start=1.6, end=2.4, duration=0.8, start_sample=0, end_sample=1)]
    energy = EnergyTrack(origin=0.0, frame_seconds=0.01, rms=[0.5] * 500)

    resolved, issues, counts = resolve_jl_cuts(_jl_segments(), [], vad_intervals, energy, 0.30)

    assert counts["jl_blocked"] == 1
    assert issues[0]["code"] == "jl_no_safe_boundary"
    assert resolved[0]["audio_end"] == resolved[0]["end"]
    assert resolved[1]["audio_start"] == resolved[1]["start"]


def test_resolve_jl_cuts_noop_when_max_offset_zero() -> None:
    words = [_word("um", 1.0, 1.8), _word("dois", 2.7, 3.2)]
    energy = EnergyTrack(origin=0.0, frame_seconds=0.01, rms=[0.5] * 500)

    resolved, issues, counts = resolve_jl_cuts(_jl_segments(), words, [], energy, 0.0)

    assert issues == []
    assert counts == {"j_cuts": 0, "l_cuts": 0, "jl_blocked": 0}
    assert resolved[0]["audio_start"] == resolved[0]["start"]
    assert resolved[0]["audio_end"] == resolved[0]["end"]
    assert "kind" not in resolved[0]["transition"]


def _jl_fixture_words() -> list[TranscriptWord]:
    return [
        _word("isso", 0.5, 1.0),
        _word("mundo", 3.0, 3.5),
        _word("agora", 3.6, 4.0),
    ]


def _jl_fixture_rms() -> list[float]:
    # Frame grid matches energy_track_from_analysis for a 4.0s clip with the
    # default 0.1s analysis frame (see _analysis_document): 60 frames. A
    # quiet dip at indices 16-17 (~1.65s-1.75s) sits inside the J/L search
    # radius (0.30s-0.50s past the balanced profile's planned cut at 1.25s)
    # but outside the planner's own 0.30s boundary_radius, so it only
    # influences J/L resolution, not where the pause itself gets cut.
    rms = [0.4] * 60
    rms[16] = 0.01
    rms[17] = 0.01
    return rms


def test_edit_plan_v2_matches_v1_behavior_when_jl_disabled(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    words = _jl_fixture_words()
    _project, _source, transcript, analysis = _fixture(config, domain, words, 4.0, rms=_jl_fixture_rms())
    service = EditPlanService(config, domain)

    result = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced",
        progress_cb=lambda _v, _m: None, should_cancel=lambda: False,
    )
    document = EditPlanDocument.model_validate_json(Path(result["edit_plan_path"]).read_text())

    assert document.schema_version == 2
    assert document.jl_settings.enabled is False
    assert document.jl_settings.max_offset_seconds == 0.0
    assert document.diagnostics.j_cuts == 0
    assert document.diagnostics.l_cuts == 0
    assert document.diagnostics.jl_blocked == 0
    for segment in document.segments:
        assert segment.video_start == segment.start
        assert segment.video_end == segment.end
        assert segment.audio_start == segment.start
        assert segment.audio_end == segment.end
        assert segment.transition is None or segment.transition.kind == "crossfade"
        assert segment.transition is None or segment.transition.audio_offset_seconds == 0.0


def test_edit_plan_jl_enabled_applies_offset_within_profile_limit(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    words = _jl_fixture_words()
    _project, _source, transcript, analysis = _fixture(config, domain, words, 4.0, rms=_jl_fixture_rms())
    service = EditPlanService(config, domain)

    result = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced", jl_cut=True,
        progress_cb=lambda _v, _m: None, should_cancel=lambda: False,
    )
    document = EditPlanDocument.model_validate_json(Path(result["edit_plan_path"]).read_text())

    assert document.jl_settings.enabled is True
    assert document.jl_settings.max_offset_seconds == pytest.approx(PROFILES["balanced"].max_jl_offset)
    assert document.diagnostics.l_cuts == 1
    assert document.diagnostics.j_cuts == 0
    assert document.diagnostics.jl_blocked == 0
    assert len(document.segments) == 2
    first, second = document.segments
    assert first.transition is not None
    assert first.transition.kind == "l_cut"
    assert first.transition.audio_offset_seconds == pytest.approx(0.4)
    assert abs(first.transition.audio_offset_seconds) <= PROFILES["balanced"].max_jl_offset + 1e-9
    assert first.audio_end == pytest.approx(1.65)
    assert second.audio_start == pytest.approx(3.25)
    # Coverage invariant: total audio seconds == total video seconds. Also
    # implicitly proven by EditPlanDocument validating at all — a violation
    # would have raised a ValidationError during service.run().
    total_video = sum(seg.video_end - seg.video_start for seg in document.segments)
    total_audio = sum(seg.audio_end - seg.audio_start for seg in document.segments)
    assert total_audio == pytest.approx(total_video)
    assert first.video_end == pytest.approx(1.25)
    assert second.video_start == pytest.approx(2.85)


def test_edit_plan_jl_user_max_offset_clamped_by_profile(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    words = _jl_fixture_words()
    _project, _source, transcript, analysis = _fixture(config, domain, words, 4.0, rms=_jl_fixture_rms())
    service = EditPlanService(config, domain)

    result = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced", jl_cut=True, max_jl_offset_seconds=999.0,
        progress_cb=lambda _v, _m: None, should_cancel=lambda: False,
    )
    document = EditPlanDocument.model_validate_json(Path(result["edit_plan_path"]).read_text())

    assert document.jl_settings.max_offset_seconds == pytest.approx(PROFILES["balanced"].max_jl_offset)


def test_edit_plan_jl_toggle_changes_hash_and_artifact(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    words = _jl_fixture_words()
    _project, _source, transcript, analysis = _fixture(config, domain, words, 4.0, rms=_jl_fixture_rms())
    service = EditPlanService(config, domain)

    without_jl = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced", jl_cut=False,
        progress_cb=lambda _v, _m: None, should_cancel=lambda: False,
    )
    with_jl = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced", jl_cut=True,
        progress_cb=lambda _v, _m: None, should_cancel=lambda: False,
    )
    with_jl_again = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced", jl_cut=True,
        progress_cb=lambda _v, _m: None, should_cancel=lambda: False,
    )

    assert without_jl["cached"] is False
    assert with_jl["cached"] is False
    assert without_jl["edit_plan_artifact_id"] != with_jl["edit_plan_artifact_id"]
    assert with_jl_again["cached"] is True
    assert with_jl_again["edit_plan_artifact_id"] == with_jl["edit_plan_artifact_id"]


def test_edit_plan_jl_metadata_records_requested_and_effective(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    words = _jl_fixture_words()
    _project, _source, transcript, analysis = _fixture(config, domain, words, 4.0, rms=_jl_fixture_rms())
    service = EditPlanService(config, domain)

    result = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced", jl_cut=True,
        progress_cb=lambda _v, _m: None, should_cancel=lambda: False,
    )
    artifact = domain.get_stage_artifact(result["edit_plan_artifact_id"])
    assert artifact.metadata["requested_jl"] is True
    assert artifact.metadata["effective_jl"] is True
    assert artifact.metadata["effective_max_jl_offset_seconds"] == pytest.approx(
        PROFILES["balanced"].max_jl_offset
    )


def test_resolve_jl_cuts_blocks_when_neighbor_segment_cannot_absorb_offset() -> None:
    # A quiet dip pulls the audio cut ~0.2s early (J-cut), but the outgoing
    # segment is only 0.15s long: applying the offset would invert its audio
    # clock. The boundary must degrade to a synchronous cut, never raise.
    segments = [
        {
            "start": 1.85, "end": 2.0, "timeline_order": 0,
            "transition": {"video": "micro_dissolve", "audio": "equal_power_crossfade", "duration": 0.06},
        },
        {"start": 2.5, "end": 4.0, "timeline_order": 1},
    ]
    rms = [0.5] * 500
    for i in range(178, 182):  # quiet dip around 1.78s-1.82s
        rms[i] = 0.01
    energy = EnergyTrack(origin=0.0, frame_seconds=0.01, rms=rms)

    resolved, issues, counts = resolve_jl_cuts(segments, [], [], energy, 0.30)

    assert counts == {"j_cuts": 0, "l_cuts": 0, "jl_blocked": 1}
    assert issues[0]["code"] == "jl_no_safe_boundary"
    assert resolved[0]["audio_end"] == resolved[0]["end"]
    assert resolved[1]["audio_start"] == resolved[1]["start"]
