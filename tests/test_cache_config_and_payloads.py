from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from asgi_client import ASGITestClient

from cortex.api import create_app
from cortex.clips import _clip_row
from cortex.config import ClipConfig, load_config
from cortex.domain.models import (
    SourceAsset, SourceKind, StageArtifact, TranscriptArtifact, WorkflowRun,
)
from cortex.domain.store import DomainStore
from cortex.jobs import JobStore
from cortex.library import public_run
from cortex.schemas import JobCreate, JobStatus, JobType, JobUpdate
from cortex.transcribe.service import TranscribeService
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine
from test_edit_plan import _fixture, _word
from test_export_batches import _config

P = "/api/v1"
ROOT = Path(__file__).resolve().parents[1]
VOICE = {
    "speaker": "Dayon",
    "turns": [{"start": 0, "end": 3, "speaker": "Dayon"}, {"start": 3, "end": 4, "speaker": "Dayon"}],
}


def _world(tmp_path: Path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Ep")
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, stored_path="s.mp4",
        sha256="a" * 64, size_bytes=1,
    ))
    return config, domain, project, source


# -- 1. hotwords and the transcript cache -------------------------------------------

def _transcribe(tmp_path: Path):
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Hotwords")
    wav = tmp_path / "src.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(wav)],
        capture_output=True, check=True, timeout=30,
    )
    stored = config.paths.projects_dir / project.id / "source" / "tone.wav"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(wav.read_bytes())
    import hashlib
    asset = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename="tone.wav",
        stored_path=str(stored.relative_to(config.paths.data_dir)),
        sha256=hashlib.sha256(stored.read_bytes()).hexdigest(),
        size_bytes=stored.stat().st_size, probe={},
    ))
    engine = FakeTranscriptionEngine()
    service = TranscribeService(config, domain, engine)

    def run(hotwords):
        return service.run(
            source_asset=asset, overrides={"hotwords": hotwords} if hotwords is not None else {},
            progress_cb=lambda *_a: None, should_cancel=lambda: False,
        )

    return domain, engine, run


def test_transcript_cache_follows_per_run_hotwords(tmp_path: Path) -> None:
    domain, engine, run = _transcribe(tmp_path)
    first = run(["Maria  Silva", "Recife"])
    assert first["cached"] is False
    stored = domain.get_transcript_artifact(first["transcript_artifact_id"])
    assert stored.hotwords == ["Maria  Silva", "Recife"]

    assert run(None)["transcript_artifact_id"] == first["transcript_artifact_id"]
    subset = run(["maria silva"])
    assert subset["cached"] is True and subset["transcript_artifact_id"] == first["transcript_artifact_id"]
    assert len(engine.calls) == 1

    fresh = run(["Maria Silva", "Olinda"])
    assert fresh["cached"] is False and len(engine.calls) == 2
    assert fresh["transcript_artifact_id"] != first["transcript_artifact_id"]
    assert "Olinda" in engine.calls[1]["hotwords"]


def test_old_transcript_artifact_without_hotwords_still_loads() -> None:
    document = {
        "id": "t1", "project_id": "p", "source_asset_id": "s", "path": "x.json",
        "audio_sha256": "h", "engine": "e", "model": "m", "device": "cpu",
        "compute_type": "int8", "language": "pt", "vad": True, "batch_size": 1,
        "duration_seconds": 1.0,
    }
    artifact = TranscriptArtifact.model_validate(document)
    assert artifact.hotwords == []


# -- 2. clip defaults ---------------------------------------------------------------

def test_clip_defaults_endpoint_and_run_fill(tmp_path: Path) -> None:
    config, domain, project, source = _world(tmp_path)
    client = ASGITestClient(create_app(config))
    body = client.get(f"{P}/settings/clip-defaults").json()
    assert body == {
        "count": config.clips.count, "minimum_seconds": config.clips.minimum_seconds,
        "maximum_seconds": config.clips.maximum_seconds,
    }

    response = client.post(f"{P}/projects/{project.id}/runs", json={"source_asset_id": source.id})
    assert response.status_code == 201, response.text
    brief = response.json()["brief"]
    assert (brief["count"], brief["minimum_seconds"], brief["maximum_seconds"]) == (
        config.clips.count, config.clips.minimum_seconds, config.clips.maximum_seconds)

    explicit = client.post(f"{P}/projects/{project.id}/runs", json={
        "source_asset_id": source.id, "count": 4, "minimum_seconds": 30, "maximum_seconds": 90})
    assert explicit.json()["brief"]["count"] == 4
    assert explicit.json()["brief"]["minimum_seconds"] == 30


def test_run_duration_order_is_rechecked_after_filling_defaults(tmp_path: Path) -> None:
    config, domain, project, source = _world(tmp_path)
    client = ASGITestClient(create_app(config))
    both = client.post(f"{P}/projects/{project.id}/runs", json={
        "source_asset_id": source.id, "minimum_seconds": 100, "maximum_seconds": 50})
    assert both.status_code == 422
    only_min = client.post(f"{P}/projects/{project.id}/runs", json={
        "source_asset_id": source.id, "minimum_seconds": config.clips.maximum_seconds + 10})
    assert only_min.status_code == 422
    assert "maximum_seconds" in only_min.text


def test_clip_config_defaults_match_yaml() -> None:
    assert ClipConfig().minimum_seconds == 40
    assert not hasattr(ClipConfig(), "pacing_profile")


# -- 3. v1 scores ---------------------------------------------------------------------

def test_v1_scores_are_rescaled_to_ten() -> None:
    run = WorkflowRun(project_id="p", source_asset_id="s")
    v1 = {key: 5 for key in (
        "spoken_hook", "standalone_clarity", "emotion", "quotability",
        "payoff", "compression_safety", "audience_relevance")}
    assert _clip_row({"scores": {**v1, "total": 35}}, "k", run, None, [])["score"] == 10.0
    assert _clip_row({"scores": {**v1, "total": 29}}, "k", run, None, [])["score"] == 8.3
    assert _clip_row({"scores": {"total": 9}}, "k", run, None, [])["score"] == 9


# -- 4. lighter responses -------------------------------------------------------------

def _run_with_voice(domain, project, source) -> WorkflowRun:
    return domain.create_workflow_run(WorkflowRun(
        project_id=project.id, source_asset_id=source.id,
        brief={"count": 3, "confirmed_voice": VOICE},
    ))


def test_browser_runs_do_not_carry_confirmed_voice(tmp_path: Path) -> None:
    config, domain, project, source = _world(tmp_path)
    run = _run_with_voice(domain, project, source)
    plain = domain.create_workflow_run(WorkflowRun(
        project_id=project.id, source_asset_id=source.id, brief={"count": 3}))
    assert public_run(run)["brief"] == {"count": 3, "has_confirmed_voice": True}
    assert public_run(plain)["brief"]["has_confirmed_voice"] is False
    client = ASGITestClient(create_app(config))
    fetched = client.get(f"{P}/projects/{project.id}/runs/{run.id}").json()
    assert "confirmed_voice" not in fetched["brief"] and fetched["brief"]["has_confirmed_voice"] is True
    episodes = client.get(f"{P}/episodes").json()
    entry = next(e for e in episodes if project.id in e["project_ids"])
    assert "suggestions" not in entry
    assert all("confirmed_voice" not in r["brief"] for r in entry["runs"])
    assert domain.get_workflow_run(run.id).brief["confirmed_voice"] == VOICE


def test_edit_plan_voice_from_run_matches_explicit_voice(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    words = [_word("isso", 0.5, 1.0), _word("mundo", 3.0, 3.5), _word("agora", 3.6, 4.0)]
    project, source, transcript, analysis = _fixture(config, domain, words, 4.0, rms=[0.4] * 60)
    run = _run_with_voice(domain, project, source)
    client = ASGITestClient(create_app(config))
    jobs = JobStore(config.paths.database)
    base = {
        "transcript_artifact_id": transcript.id, "analysis_artifact_id": analysis.id,
        "start": 0.0, "end": 4.0, "profile": "balanced",
    }
    url = f"{P}/projects/{project.id}/edit-plans"
    explicit = client.post(url, json={**base, "confirmed_voice": VOICE}).json()
    from_run = client.post(url, json={**base, "workflow_run_id": run.id}).json()
    assert jobs.get(from_run["id"]).payload["confirmed_voice"] == VOICE
    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    first, second = jobs.get(explicit["id"]), jobs.get(from_run["id"])
    assert first.status == second.status == JobStatus.SUCCEEDED, (first.error, second.error)
    assert second.result["cached"] is True
    assert second.result["edit_plan_artifact_id"] == first.result["edit_plan_artifact_id"]

    other = domain.create_project("Other")
    stranger = domain.create_workflow_run(WorkflowRun(
        project_id=other.id, source_asset_id=source.id, brief={"confirmed_voice": VOICE}))
    assert client.post(url, json={**base, "workflow_run_id": stranger.id}).status_code == 400
    assert client.post(url, json={**base, "workflow_run_id": "missing"}).status_code == 404


def test_render_request_reads_voice_from_run(tmp_path: Path) -> None:
    config, domain, project, source = _world(tmp_path)
    run = _run_with_voice(domain, project, source)
    plan = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="edit_plan", path=str(tmp_path / "p.json"), input_hash="p"))
    client = ASGITestClient(create_app(config))
    job = client.post(f"{P}/projects/{project.id}/renders", json={
        "edit_plan_artifact_id": plan.id, "workflow_run_id": run.id})
    assert job.status_code == 201, job.text
    assert JobStore(config.paths.database).get(job.json()["id"]).payload["confirmed_voice"] == VOICE


def test_jobs_listing_active_and_summary(tmp_path: Path) -> None:
    config, domain, project, source = _world(tmp_path)
    jobs = JobStore(config.paths.database)
    done = jobs.create(JobCreate(type=JobType.TRANSCRIPTION, payload={"big": "x" * 50}))
    jobs.claim_next()
    jobs.update(done.id, JobUpdate(status=JobStatus.SUCCEEDED, result={"r": 1}))
    queued = jobs.create(JobCreate(type=JobType.TRANSCRIPTION, project_id=project.id, payload={"k": 1}))
    client = ASGITestClient(create_app(config))
    assert {j["id"] for j in client.get(f"{P}/jobs").json()} == {done.id, queued.id}
    active = client.get(f"{P}/jobs", params={"active": "true", "view": "summary"}).json()
    assert [j["id"] for j in active] == [queued.id]
    assert set(active[0]) == {
        "id", "type", "status", "stage", "progress", "message", "project_id",
        "created_at", "updated_at", "error"}
    full = client.get(f"{P}/jobs", params={"active": "true"}).json()
    assert [j["id"] for j in full] == [queued.id] and "payload" in full[0]
    assert client.get(f"{P}/jobs", params={"view": "bogus"}).status_code == 422


# -- 5. swallowed failures are logged -------------------------------------------------

def test_cancel_probe_failure_is_logged(tmp_path: Path, caplog) -> None:
    from cortex.worker import _job_is_cancelled
    jobs = JobStore(tmp_path / "j.sqlite3")
    with caplog.at_level("WARNING", logger="cortex.worker"):
        assert _job_is_cancelled(jobs, "missing") is False
    assert "missing" in caplog.text


# -- 6. schema provenance -------------------------------------------------------------

@pytest.mark.parametrize("name,schema_id", [
    ("clip_selection.v5.schema.json", "cortex/clip_selection/v5"),
    ("clip_tighten.schema.json", "cortex/clip_tighten/v2"),
])
def test_prompt_schemas_are_versioned_and_valid(name: str, schema_id: str) -> None:
    from jsonschema import Draft202012Validator
    schema = json.loads((ROOT / "prompts" / name).read_text(encoding="utf-8"))
    assert schema["$id"] == schema_id
    # The Claude CLI validates --json-schema with a draft-07 validator that rejects a 2020-12
    # "$schema" ("no schema with key or ref"), which failed every refine call; never declare it.
    assert "$schema" not in schema
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema)


# -- 7. config defaults and .env.example ----------------------------------------------

def test_code_defaults_equal_the_yaml(monkeypatch) -> None:
    from cortex.config import CortexConfig
    for name in [n for n in __import__("os").environ if n.startswith("CORTEX__")]:
        monkeypatch.delenv(name)
    loaded = load_config(ROOT / "config/cortex.yaml").model_dump()
    defaults = CortexConfig().model_dump()
    machine_specific = {("render", "ffmpeg"), ("render", "ffprobe")}
    for section in ("ai", "transcription", "lessons", "ingest", "clips", "render"):
        for key, value in loaded[section].items():
            if (section, key) in machine_specific:
                continue
            assert defaults[section][key] == value, f"{section}.{key}"
    assert "pacing_profile" not in loaded["clips"]


def test_env_example_overrides_load(tmp_path: Path, monkeypatch) -> None:
    for name in [n for n in __import__("os").environ if n.startswith("CORTEX__")]:
        monkeypatch.delenv(name)
    pairs = re.findall(
        r"^#?\s*(CORTEX__[A-Z0-9_]+)=(.*)$", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M)
    assert pairs, "the example should list overrides"
    for name, value in pairs:
        monkeypatch.setenv(name, value)
    load_config(ROOT / "config/cortex.yaml")
