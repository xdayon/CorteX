"""Automatic triangulation: clips whose scenes were never edited get their suggestions."""
from __future__ import annotations

from types import SimpleNamespace

from cortex import worker
from cortex.analyze.triangulation import effective_roles, scenes_for_plan
from cortex.diarize.service import DiarizationDocument
from cortex.domain.models import TranscriptArtifact, WorkflowRun
from cortex.edit.schemas import EditSegment
from cortex.library import EpisodeLibrary
from cortex.render.schemas import RenderSettings
from cortex.render.service import default_render_settings
from cortex.review_api import clip_render_settings
from cortex.schemas import Job, JobCreate, JobStatus, JobType, JobUpdate
from cortex.transcribe.schemas import EngineInfo, TranscriptDocument, TranscriptSegment

from test_camera_map import _setup, video  # noqa: F401 - module fixture
from test_scene_suggestions import ROLES, SHOTS, TURNS, _camera_map, _persist, _plan, _words, accept_footage

INSERT = {"kind": "reaction", "anchor": 30.0, "duration": 2.0, "source_start": 105.0}


def test_inserts_are_the_clips_own_choice_never_the_shared_settings():
    revision = SimpleNamespace(
        render_settings={"inserts": [INSERT], "headline": {"text": "x"}},
        clip_settings={"chosen": {"inserts": [INSERT]}, "none": {"inserts": []}, "styled": {"headline": {"text": "y"}}},
    )
    assert clip_render_settings(revision, "chosen")["inserts"] == [INSERT]
    assert clip_render_settings(revision, "none")["inserts"] == []
    # Never edited: automatic, even when old shared settings carry a list.
    assert clip_render_settings(revision, "styled")["inserts"] is None
    assert clip_render_settings(revision, "missing")["inserts"] is None
    assert RenderSettings.model_fields["inserts"].default is None


def test_confirmed_roles_win_over_the_suggested_ones():
    document = _camera_map(SHOTS)
    document.groups[0].subject_voice_share, document.groups[1].subject_voice_share = 0.45, 0.05
    roles, source = effective_roles({}, "map", document)
    assert source == "suggested" and roles == {0: "subject", 1: "interviewers", 2: "wide", 3: "ignore"}
    confirmed = {"artifact_id": "map", "roles": {"0": "interviewers", "1": "subject"}}
    assert effective_roles(confirmed, "map", document) == ({0: "interviewers", 1: "subject"}, "confirmed")
    # Roles confirmed on an older map do not apply to a newer one.
    assert effective_roles({**confirmed, "artifact_id": "old"}, "map", document)[1] == "suggested"


def _episode_data(tmp_path, domain, source):
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(TranscriptDocument(
        language="pt", duration_seconds=300.0,
        engine=EngineInfo(model="f", requested_device="cpu", effective_device="cpu", requested_compute_type="int8",
                          effective_compute_type="int8", batch_size=1, vad=True),
        segments=[TranscriptSegment(id=0, start=20, end=60, text="x", avg_logprob=0, no_speech_prob=0, words=_words(20, 60))],
    ).model_dump_json())
    transcript = domain.create_transcript_artifact(TranscriptArtifact(
        project_id=source.project_id, source_asset_id=source.id, path=str(transcript_path), audio_sha256="a",
        engine="f", model="f", device="cpu", compute_type="int8", language="pt", vad=True, batch_size=1,
        duration_seconds=300.0,
    ))
    plan = _plan([EditSegment(start=20, end=60, timeline_order=0)]).model_copy(update={
        "project_id": source.project_id, "source_asset_id": source.id, "transcript_artifact_id": transcript.id})
    return _persist(tmp_path, domain, source, "plan", "edit_plan", plan.model_dump_json()), plan


def test_render_worker_resolves_the_automatic_inserts(tmp_path, video, monkeypatch):  # noqa: F811
    checked = accept_footage(monkeypatch)
    config, _jobs, domain, source = _setup(tmp_path, video)
    library = EpisodeLibrary(config, domain)
    library.reconcile()
    plan_artifact, plan = _episode_data(tmp_path, domain, source)

    resolved, summary = worker._auto_triangulation(config, domain, plan_artifact, _settings(config))
    assert resolved.inserts == [] and summary["status"] == "needs_camera_map"

    voice = _persist(tmp_path, domain, source, "voice", "diarization", DiarizationDocument(
        engine="voice-match", engine_version="1", requested_device="cpu", effective_device="cpu",
        elapsed_seconds=1, turns=TURNS).model_dump_json())
    camera = _camera_map(SHOTS).model_copy(update={
        "project_id": source.project_id, "voice_artifact_id": voice.id, "source_sha256": source.sha256})
    camera_artifact = _persist(tmp_path, domain, source, "camera", "camera_map", camera.model_dump_json())
    episode = library.for_project(source.project_id)
    episode.camera_roles = {"artifact_id": camera_artifact.id, "roles": {str(k): v for k, v in ROLES.items()}}
    library.save(episode)

    resolved, summary = worker._auto_triangulation(config, domain, plan_artifact, _settings(config))
    assert checked, "the worker checks borrowed footage"
    expected = scenes_for_plan(domain, library.for_project(source.project_id), plan, config).suggestions.suggestions
    assert [i.suggestion_id for i in resolved.inserts] == [s.id for s in expected] and len(expected) == 2
    assert [i.kind for i in resolved.inserts] == ["wide", "reaction"]
    assert summary == {"mode": "auto", "status": "ok", "algorithm_version": "2", "inserts": 2,
                       "camera_map_artifact_id": camera_artifact.id, "roles_source": "confirmed"}


def _settings(config) -> RenderSettings:
    settings = default_render_settings(config)
    assert settings.inserts is None
    return settings


def test_cameras_are_mapped_once_after_the_selection(tmp_path, video, monkeypatch):  # noqa: F811
    config, jobs, domain, source = _setup(tmp_path, video)
    run = domain.create_workflow_run(WorkflowRun(project_id=source.project_id, source_asset_id=source.id))
    monkeypatch.setattr("cortex.diarize.voice_match.load_profile", lambda _config: object())

    def finished(kind: JobType) -> Job:
        job = jobs.create(JobCreate(type=kind, project_id=source.project_id, payload={"workflow_run_id": run.id}))
        jobs.update(job.id, JobUpdate(status=JobStatus.RUNNING))
        return jobs.update(job.id, JobUpdate(status=JobStatus.SUCCEEDED, result={}))

    def camera_jobs():
        return [j for j in jobs.list() if j.type == JobType.CAMERA_MAP]

    worker._queue_camera_map_after_selection(finished(JobType.ANALYSIS), config, jobs, domain)
    assert camera_jobs() == []
    selection = finished(JobType.SUGGESTION)
    worker._queue_camera_map_after_selection(selection, config, jobs, domain)
    worker._queue_camera_map_after_selection(selection, config, jobs, domain)
    queued = camera_jobs()
    assert len(queued) == 1 and queued[0].payload == {"source_asset_id": source.id}
    # Without a voiceprint the roles cannot be suggested: nothing is queued.
    monkeypatch.setattr("cortex.diarize.voice_match.load_profile", lambda _config: None)
    other = domain.create_workflow_run(WorkflowRun(project_id=source.project_id, source_asset_id="other"))
    job = jobs.create(JobCreate(type=JobType.SUGGESTION, project_id=source.project_id, payload={"workflow_run_id": other.id}))
    jobs.update(job.id, JobUpdate(status=JobStatus.RUNNING))
    worker._queue_camera_map_after_selection(jobs.update(job.id, JobUpdate(status=JobStatus.SUCCEEDED, result={})), config, jobs, domain)
    assert len(camera_jobs()) == 1
