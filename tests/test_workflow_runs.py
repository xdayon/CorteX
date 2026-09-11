from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from asgi_client import ASGITestClient as TestClient

from cortex.analyze.schemas import LoudnessMetrics, VadInterval
from cortex.api import create_app
from cortex.config import CortexConfig, load_config
from cortex.domain.models import SourceAsset, SourceKind, StageArtifact, WorkflowRun
from cortex.domain.store import DomainStore
from cortex.ingest.ffprobe import probe_media
from cortex.jobs import JobStore
from cortex.suggest.provider import SuggestionProviderResult
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine


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


class _SuggestionProvider:
    def generate(self, prompt, schema, *, should_cancel):
        del prompt, schema
        assert not should_cancel()
        clip = {
            "rank": 1,
            "title": "Ideia principal",
            "headline": "Uma ideia que vale o corte",
            "start_second": 0,
            "end_second": 40,
            "estimated_duration": 40,
            "primary_speaker": "Pessoa principal",
            "topic": "Teste",
            "pacing": "balanced",
            "hook": {"start_second": 0, "end_second": 5, "summary": "Gancho", "evidence": "Gancho"},
            "context": {"start_second": 5, "end_second": 30, "summary": "Contexto", "evidence": "Contexto"},
            "payoff": {"start_second": 30, "end_second": 40, "summary": "Conclusão", "evidence": "Conclusão"},
            "approximate_edl": [{
                "order": 0, "source_start": 0, "source_end": 40, "purpose": "development",
                "preferred_visual": "auto", "audio_mode": "source", "transition_in": "hard_cut",
                "mask_jump_with": "none", "confidence": 0.9, "notes": "Trecho contínuo",
            }],
            "scores": {
                "spoken_hook": 4, "standalone_clarity": 4, "emotion": 3, "quotability": 4,
                "payoff": 4, "compression_safety": 4, "audience_relevance": 4, "total": 27,
            },
            "reasoning": "Trecho autocontido.",
            "warnings": [],
        }
        return SuggestionProviderResult(
            document={"schema_version": "1.0", "selection_notes": "Teste", "clips": [clip]},
            provenance={"provider": "codex_cli", "model": "fixture"},
        )


def test_one_action_persists_and_advances_to_review(
    tmp_path: Path, short_mp4: Path, monkeypatch,
) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Episódio")
    stored = config.paths.projects_dir / project.id / "source" / "episode.mp4"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(short_mp4.read_bytes())
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id,
        kind=SourceKind.UPLOAD,
        original_filename="episode.mp4",
        stored_path=str(stored.relative_to(config.paths.data_dir)),
        sha256=hashlib.sha256(stored.read_bytes()).hexdigest(),
        size_bytes=stored.stat().st_size,
        probe=probe_media(config.render.ffprobe, stored),
    ))
    interval = VadInterval(start=0.1, end=1.8, duration=1.7, start_sample=1600, end_sample=28800)
    monkeypatch.setattr("cortex.analyze.service.detect_speech", lambda _audio, _rate: ([interval], "test-vad"))
    monkeypatch.setattr(
        "cortex.analyze.service.measure_loudness",
        lambda _ffmpeg, _path: LoudnessMetrics(
            integrated_lufs=-20, loudness_range_lu=2, true_peak_dbfs=-3,
            threshold_lufs=-30, engine="test-loudness",
        ),
    )
    client = TestClient(create_app(config))

    response = client.post(
        f"/api/v1/projects/{project.id}/runs",
        json={"source_asset_id": source.id, "count": 10, "minimum_seconds": 40, "maximum_seconds": 120},
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    jobs = JobStore(config.paths.database)

    assert process_next(config, jobs, domain, FakeTranscriptionEngine(duration_seconds=40), _SuggestionProvider())
    assert domain.get_workflow_run(run_id).stage == "analyze"
    assert process_next(config, jobs, domain, FakeTranscriptionEngine(), _SuggestionProvider())
    assert domain.get_workflow_run(run_id).stage == "suggest"
    assert process_next(config, jobs, domain, FakeTranscriptionEngine(), _SuggestionProvider())

    run = domain.get_workflow_run(run_id)
    assert run.status == "ready_for_review"
    assert set(run.artifacts) == {"transcript", "analysis", "suggestion"}
    assert client.get(f"/api/v1/projects/{project.id}/runs/{run_id}").status_code == 200
    assert client.get(f"/api/v1/projects/{project.id}/sources").json()[0]["id"] == source.id


def test_run_rejects_inverted_duration(tmp_path: Path) -> None:
    config = _config(tmp_path)
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Episódio")
    client = TestClient(create_app(config))

    response = client.post(
        f"/api/v1/projects/{project.id}/runs",
        json={"source_asset_id": "missing", "minimum_seconds": 120, "maximum_seconds": 40},
    )
    assert response.status_code == 422


@pytest.fixture
def visual_workflow(tmp_path: Path):
    config = _config(tmp_path)
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Episódio")
    source = domain.create_source_asset(SourceAsset(
        id="source", project_id=project.id, kind=SourceKind.UPLOAD,
        stored_path="episode.mp4", sha256="a" * 64, size_bytes=1,
    ))
    analysis = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="analysis", input_hash="analysis", path="analysis.json",
    ))
    run = domain.create_workflow_run(WorkflowRun(
        project_id=project.id, source_asset_id=source.id, status="ready_for_review",
        artifacts={"analysis": analysis.id}, subject_identity_id="old-subject",
    ))
    stages = {
        "scene_index_artifact_id": "scene_index",
        "face_index_artifact_id": "face_index",
        "speaker_timeline_artifact_id": "speaker_timeline",
        "camera_timeline_artifact_id": "camera_timeline",
        "visual_quality_artifact_id": "visual_quality_index",
        "identity_index_artifact_id": "identity_index",
    }
    artifact_ids = {}
    for field, stage in stages.items():
        path = tmp_path / f"{stage}.json"
        path.write_text("{}", encoding="utf-8")
        artifact = domain.create_stage_artifact(StageArtifact(
            project_id=project.id, stage=stage, path=str(path), input_hash=stage,
        ))
        artifact_ids[field] = artifact.id
    common = {"project_id": project.id, "source_asset_id": source.id, "source_sha256": source.sha256}
    scene_ref = {"scene_index_artifact_id": artifact_ids["scene_index_artifact_id"]}
    scene_hash = {**scene_ref, "scene_index_input_hash": "scene_index"}
    face_ref = {"face_index_artifact_id": artifact_ids["face_index_artifact_id"], "face_index_input_hash": "face_index"}
    cpu = {"ffmpeg_path": "ffmpeg", "ffmpeg_version": "test", "decoder_requested": "cpu",
           "decoder_effective": "cpu", "device_requested": "cpu", "device_effective": "cpu"}
    documents = {
        "scene_index": {"duration_seconds": 1, "cuts": [], "scenes": [], "cut_count": 0,
                        "engine": {"ffmpeg_path": "ffmpeg", "ffmpeg_version": "test", "filter": "scdet",
                                   "threshold_requested": 10, "threshold_effective": 10}},
        "face_index": {**scene_ref, "duration_seconds": 1, "frames": [], "scenes": [], "frame_count": 0,
                       "engine": {"detector": "yunet", "model_path": "fixture.onnx", "providers": ["CPUExecutionProvider"],
                                  "score_threshold": 0.8, "nms_threshold": 0.3, "sample_fps": 1,
                                  "ffmpeg_path": "ffmpeg", "ffmpeg_version": "test"}},
        "speaker_timeline": {**scene_hash, **face_ref, "analysis_artifact_id": analysis.id,
                             "analysis_input_hash": analysis.input_hash, "duration_us": 1000000,
                             "observations": [], "segments": [], "engine": {
                                 **cpu, "algorithm": "test", "algorithm_version": "1",
                                 "sample_fps_requested": 1, "sample_fps_effective": 1,
                                 "frame_width_requested": 100, "frame_width_effective": 100,
                                 "vad_padding_seconds": 1, "motion_threshold": 0.1, "winner_margin": 0.1}},
        "camera_timeline": {**scene_hash, **face_ref,
                            "speaker_timeline_artifact_id": artifact_ids["speaker_timeline_artifact_id"],
                            "speaker_timeline_input_hash": "speaker_timeline", "duration_us": 1000000,
                            "scenes": [], "engine": {"algorithm": "test", "algorithm_version": "1",
                                                      "dominant_speaker_min_share": 0.5}},
        "visual_quality_index": {**scene_hash, **face_ref, "duration_us": 1000000, "frames": [], "scenes": [],
                                 "engine": {**cpu, "algorithm": "test", "algorithm_version": "1",
                                            "sample_fps_requested": 1, "sample_fps_effective": 1, "frame_width": 100,
                                            "black_luma_threshold": 20, "black_pixel_ratio_threshold": 0.9,
                                            "blur_score_threshold": 1, "freeze_delta_threshold": 0.1,
                                            "issue_share_threshold": 0.5, "face_edge_margin": 0.1}},
    }
    for stage, document in documents.items():
        (tmp_path / f"{stage}.json").write_text(json.dumps({**common, "input_hash": stage, **document}))
    identity_path = Path(domain.get_stage_artifact(artifact_ids["identity_index_artifact_id"]).path)
    identity_path.write_text(json.dumps({
        "schema_version": 1,
        "project_id": project.id,
        "source_asset_id": "source",
        "source_sha256": "a" * 64,
        "face_index_artifact_id": artifact_ids["face_index_artifact_id"],
        "face_index_input_hash": "face_index",
        "camera_timeline_artifact_id": artifact_ids["camera_timeline_artifact_id"],
        "camera_timeline_input_hash": "camera_timeline",
        "input_hash": "identity_index",
        "observations": [{
            "observation_id": "face-0-0", "time_us": 0, "scene_index": 0,
            "layout_id": "layout", "local_track_id": "track", "identity_id": "me",
            "status": "confirmed", "assignment_similarity": 0.9, "ambiguous_match": False,
        }],
        "identities": [{
            "identity_id": "me", "status": "confirmed", "observation_ids": ["face-0-0"],
            "layout_ids": ["layout"], "scene_indices": [0], "local_track_ids": ["track"],
            "sample_count": 1, "minimum_pair_similarity": 0.9, "evidence": ["test"],
        }],
        "unresolved_face_count": 0,
        "engine": {
            "algorithm": "test", "algorithm_version": "1", "recognizer": "sface_2021dec",
            "model_path": "fixture.onnx", "model_sha256": "b" * 64, "embedding_dimension": 128,
            "cosine_match_threshold": 0.5, "ambiguity_margin": 0.1,
            "provider": "CPUExecutionProvider", "identity_scope": "cross_layout_face_embedding",
        },
    }), encoding="utf-8")
    client = TestClient(create_app(config))

    return client, domain, run, artifact_ids


def select_identity(fixture, identity="me"):
    client, _, run, artifact_ids = fixture
    return client.request(
        "PUT", f"/api/v1/projects/{run.project_id}/runs/{run.id}/identity",
        json={"interviewer_identity_id": identity, **artifact_ids},
    )


def test_interviewer_and_visual_artifacts_are_persisted(visual_workflow) -> None:
    client, domain, run, artifact_ids = visual_workflow
    response = select_identity(visual_workflow)
    assert response.status_code == 200, response.text
    persisted = domain.get_workflow_run(run.id)
    assert persisted.interviewer_identity_id == "me"
    assert persisted.subject_identity_id == "old-subject"
    assert persisted.artifacts["identity_index"] == artifact_ids["identity_index_artifact_id"]
    reopened = client.get(f"/api/v1/projects/{run.project_id}/runs/{run.id}").json()
    assert reopened["interviewer_identity_id"] == "me"


def test_old_subject_is_not_reinterpreted_as_interviewer(visual_workflow) -> None:
    client, domain, run, _ = visual_workflow
    old_data = run.model_dump(mode="json")
    old_data.pop("interviewer_identity_id")
    assert WorkflowRun.model_validate(old_data).interviewer_identity_id is None
    response = client.get(f"/api/v1/projects/{run.project_id}/runs/{run.id}")
    assert response.json()["subject_identity_id"] == "old-subject"
    assert response.json()["interviewer_identity_id"] is None


@pytest.mark.parametrize("identity", ["missing", ""])
def test_interviewer_rejects_invalid_identity(visual_workflow, identity) -> None:
    _, domain, run, _ = visual_workflow
    response = select_identity(visual_workflow, identity)
    assert response.status_code == (422 if not identity else 400)
    assert domain.get_workflow_run(run.id).interviewer_identity_id is None


@pytest.mark.parametrize("field", [
    "scene_index_artifact_id", "face_index_artifact_id", "speaker_timeline_artifact_id",
    "camera_timeline_artifact_id", "visual_quality_artifact_id", "identity_index_artifact_id",
])
@pytest.mark.parametrize("property_name", ["project_id", "source_asset_id", "source_sha256", "input_hash"])
def test_identity_rejects_visual_artifact_from_different_source(visual_workflow, field, property_name) -> None:
    _, domain, run, artifact_ids = visual_workflow
    path = Path(domain.get_stage_artifact(artifact_ids[field]).path)
    document = json.loads(path.read_text())
    document[property_name] = "another"
    path.write_text(json.dumps(document))
    assert select_identity(visual_workflow).status_code == 400
    assert domain.get_workflow_run(run.id).artifacts == run.artifacts


@pytest.mark.parametrize(("stage", "dependency"), [
    ("face_index", "scene_index"),
    ("speaker_timeline", "scene_index"), ("speaker_timeline", "face_index"), ("speaker_timeline", "analysis"),
    ("camera_timeline", "scene_index"), ("camera_timeline", "face_index"), ("camera_timeline", "speaker_timeline"),
    ("visual_quality", "scene_index"), ("visual_quality", "face_index"),
    ("identity_index", "face_index"), ("identity_index", "camera_timeline"),
])
def test_identity_rejects_mixed_dependency_chain(visual_workflow, stage, dependency) -> None:
    _, domain, run, artifact_ids = visual_workflow
    path = Path(domain.get_stage_artifact(artifact_ids[f"{stage}_artifact_id"]).path)
    document = json.loads(path.read_text())
    document[f"{dependency}_artifact_id"] = "another-artifact"
    path.write_text(json.dumps(document))
    assert select_identity(visual_workflow).status_code == 400
    assert domain.get_workflow_run(run.id).interviewer_identity_id is None


@pytest.mark.parametrize("damage", ["missing", "invalid", "unconfirmed", "hash"])
def test_identity_rejects_unavailable_or_unconfirmed_artifacts(visual_workflow, damage) -> None:
    _, domain, run, artifact_ids = visual_workflow
    path = Path(domain.get_stage_artifact(artifact_ids["identity_index_artifact_id"]).path)
    document = json.loads(path.read_text())
    if damage == "missing":
        path.unlink()
    elif damage == "invalid":
        path.write_text("not json")
    else:
        if damage == "unconfirmed":
            document["identities"][0]["status"] = "single_layout"
        else:
            document["face_index_input_hash"] = "stale"
        path.write_text(json.dumps(document))
    assert select_identity(visual_workflow).status_code == (410 if damage == "missing" else 400)
    assert domain.get_workflow_run(run.id).interviewer_identity_id is None
