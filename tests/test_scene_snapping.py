from __future__ import annotations

from pathlib import Path

import pytest
from asgi_client import ASGITestClient as TestClient

from cortex.analyze.scene_schemas import SceneCut, SceneIndexDocument, SceneIndexEngineInfo
from cortex.api import create_app
from cortex.domain.models import StageArtifact
from cortex.domain.store import DomainStore
from cortex.edit.boundary import nearest_scene_cut, snap_segments_to_scene_cuts
from cortex.edit.schemas import EditPlanDocument
from cortex.edit.service import EditPlanService
from cortex.jobs import JobStore
from cortex.schemas import JobStatus
from cortex.transcribe.schemas import TranscriptWord
from cortex.worker import process_next

from conftest import FakeTranscriptionEngine
from test_edit_plan import _config, _fixture, _word


# -- 1. Pure function: snap within tolerance --------------------------------

def test_snap_within_tolerance_moves_boundary_to_scene_cut() -> None:
    words = [_word("ola", 0.0, 0.4), _word("mundo", 3.0, 3.4)]
    segments = [{"start": 0.0, "end": 2.55, "timeline_order": 0}]
    cuts = [2.4]
    snapped, issues = snap_segments_to_scene_cuts(segments, words, [], cuts, tolerance=0.5)
    assert snapped[0]["end"] == 2.4
    assert len(issues) == 1
    issue = issues[0]
    assert issue["code"] == "scene_snapped"
    assert issue["severity"] == "info"
    assert issue["boundary"] == "out"
    assert issue["snapped_from"] == 2.55
    assert issue["snapped_to"] == 2.4
    assert issue["delta_ms"] == pytest.approx(150.0)


def test_nearest_scene_cut_picks_closest_within_radius() -> None:
    assert nearest_scene_cut(10.0, [9.6, 10.3, 12.0], tolerance=0.5) == 10.3
    assert nearest_scene_cut(10.0, [20.0], tolerance=0.5) is None
    assert nearest_scene_cut(10.0, [], tolerance=0.5) is None


def test_snap_never_inverts_or_empties_short_segment() -> None:
    # segmento de 0.6s com cortes de cena dos dois lados: start seria puxado
    # para frente e end para trás; sem o guard de comprimento mínimo os dois
    # snaps juntos esvaziariam ou inverteriam o segmento.
    segments = [{"start": 2.5, "end": 3.1, "timeline_order": 0}]
    cuts = [2.95, 2.7]
    snapped, issues = snap_segments_to_scene_cuts(segments, [], [], cuts, tolerance=0.5)
    start = float(snapped[0]["start"])
    end = float(snapped[0]["end"])
    assert start < end
    assert end - start >= 0.2 - 1e-9
    assert all(issue["code"] == "scene_snapped" for issue in issues)


# -- 2. Refuses to snap when it would violate speech -------------------------

def test_snap_refused_when_candidate_falls_inside_active_word() -> None:
    words = [_word("central", 2.30, 2.60)]
    segments = [{"start": 0.0, "end": 2.55, "timeline_order": 0}]
    # The nearest scene cut lands inside the word "central" (2.30-2.60).
    cuts = [2.45]
    snapped, issues = snap_segments_to_scene_cuts(segments, words, [], cuts, tolerance=0.5)
    assert snapped[0]["end"] == 2.55  # untouched
    assert issues == []


def test_snap_refused_when_candidate_falls_inside_vad_interval() -> None:
    words: list[TranscriptWord] = []
    segments = [{"start": 0.0, "end": 2.55, "timeline_order": 0}]
    cuts = [2.45]
    # VAD marks 2.2-2.6 as protected speech even without word-level data.
    snapped, issues = snap_segments_to_scene_cuts(
        segments, words, [(2.2, 2.6)], cuts, tolerance=0.5
    )
    assert snapped[0]["end"] == 2.55
    assert issues == []


# -- 3. Multi-segment boundary snapping --------------------------------------

def test_snap_applies_independently_across_multiple_segments() -> None:
    words = [_word("um", 0.0, 0.3), _word("dois", 5.0, 5.3), _word("tres", 9.0, 9.3)]
    segments = [
        {"start": 0.0, "end": 2.52, "timeline_order": 0},
        {"start": 2.9, "end": 6.05, "timeline_order": 1},
    ]
    cuts = [2.5, 2.95, 6.0]
    snapped, issues = snap_segments_to_scene_cuts(segments, words, [], cuts, tolerance=0.2)
    assert snapped[0]["end"] == 2.5
    assert snapped[1]["start"] == 2.95
    assert snapped[1]["end"] == 6.0
    assert {issue["segment"] for issue in issues} == {0, 1}
    assert len(issues) == 3


# -- 4. No cuts / zero tolerance leaves the plan untouched --------------------

def test_snap_with_no_cuts_or_tolerance_is_a_no_op() -> None:
    words = [_word("ola", 0.0, 0.4)]
    segments = [{"start": 0.0, "end": 2.55, "timeline_order": 0}]
    snapped, issues = snap_segments_to_scene_cuts(segments, words, [], [], tolerance=0.5)
    assert snapped == segments
    assert issues == []
    snapped, issues = snap_segments_to_scene_cuts(segments, words, [], [2.5], tolerance=0.0)
    assert snapped == segments
    assert issues == []


# -- 5. Service-level: plan is byte-identical without a scene_index ----------

def _scene_index_document(project_id: str, source_asset_id: str, *, cuts: list[float]) -> SceneIndexDocument:
    return SceneIndexDocument(
        project_id=project_id, source_asset_id=source_asset_id,
        source_sha256="fake-source-sha256", input_hash="fake-scene-index-hash",
        duration_seconds=10.0,
        cuts=[SceneCut(time=t, score=20.0) for t in cuts],
        scenes=[], cut_count=len(cuts),
        engine=SceneIndexEngineInfo(
            ffmpeg_path="ffmpeg", ffmpeg_version="8.1.1", filter="scdet",
            threshold_requested=10.0, threshold_effective=10.0,
        ),
    )


def test_edit_plan_without_scene_index_is_unchanged(tmp_path: Path) -> None:
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

    baseline = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced",
        progress_cb=lambda _v, _m: None, should_cancel=lambda: False,
    )
    baseline_doc = EditPlanDocument.model_validate_json(Path(baseline["edit_plan_path"]).read_text())

    # A scene cut far from every boundary must never move a segment or the
    # plan's diagnostics, and must not appear in the quality issues.
    scene_index_doc = _scene_index_document(_project.id, _source.id, cuts=[1.9])
    scenes_dir = config.paths.projects_dir / _project.id / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    scene_index_path = scenes_dir / "scene-index.json"
    scene_index_path.write_text(scene_index_doc.model_dump_json(), encoding="utf-8")
    scene_index_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=_project.id, stage="scene_index", schema_version=1,
        path=str(scene_index_path), input_hash="fake-scene-index-artifact-hash",
    ))

    with_far_cut = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced",
        progress_cb=lambda _v, _m: None, should_cancel=lambda: False,
        scene_index_artifact=scene_index_artifact,
    )
    with_far_cut_doc = EditPlanDocument.model_validate_json(Path(with_far_cut["edit_plan_path"]).read_text())

    assert with_far_cut_doc.segments == baseline_doc.segments
    assert with_far_cut_doc.input_hash != baseline_doc.input_hash  # scene_index still enters the hash
    assert with_far_cut_doc.diagnostics.scene_snap_count == 0
    assert not any(issue.code == "scene_snapped" for issue in with_far_cut_doc.quality.issues)


def test_edit_plan_applies_scene_snap_and_records_issue(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    words = [
        _word("isso", 0.5, 1.0),
        _word("mundo", 3.0, 3.5),
        _word("agora", 3.6, 4.0),
    ]
    rms = [0.4] * 60
    project, source, transcript, analysis = _fixture(config, domain, words, 4.0, rms=rms)
    service = EditPlanService(config, domain)

    baseline = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced",
        progress_cb=lambda _v, _m: None, should_cancel=lambda: False,
    )
    baseline_doc = EditPlanDocument.model_validate_json(Path(baseline["edit_plan_path"]).read_text())
    assert len(baseline_doc.segments) >= 2, "fixture must produce a cut to snap"
    boundary_to_move = baseline_doc.segments[0]["end"] if isinstance(baseline_doc.segments[0], dict) else baseline_doc.segments[0].end
    scene_cut_time = round(boundary_to_move + 0.2, 3)

    scene_index_doc = _scene_index_document(project.id, source.id, cuts=[scene_cut_time])
    scenes_dir = config.paths.projects_dir / project.id / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    scene_index_path = scenes_dir / "scene-index.json"
    scene_index_path.write_text(scene_index_doc.model_dump_json(), encoding="utf-8")
    scene_index_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="scene_index", schema_version=1,
        path=str(scene_index_path), input_hash="fake-scene-index-artifact-hash-2",
    ))

    snapped_result = service.run(
        transcript_artifact=transcript, analysis_artifact=analysis,
        start=0.0, end=4.0, profile="balanced",
        progress_cb=lambda _v, _m: None, should_cancel=lambda: False,
        scene_index_artifact=scene_index_artifact,
    )
    snapped_doc = EditPlanDocument.model_validate_json(Path(snapped_result["edit_plan_path"]).read_text())
    assert snapped_doc.diagnostics.scene_snap_count >= 1
    assert any(issue.code == "scene_snapped" for issue in snapped_doc.quality.issues)
    assert snapped_doc.scene_index_artifact_id == scene_index_artifact.id


# -- 6. API endpoint accepts scene_index_artifact_id --------------------------

def test_edit_plan_endpoint_accepts_scene_index_artifact(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    words = [
        _word("isso", 0.5, 1.0),
        _word("mundo", 3.0, 3.5),
        _word("agora", 3.6, 4.0),
    ]
    rms = [0.4] * 60
    project, source, transcript, analysis = _fixture(config, domain, words, 4.0, rms=rms)

    scene_index_doc = _scene_index_document(project.id, source.id, cuts=[1.2])
    scenes_dir = config.paths.projects_dir / project.id / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    scene_index_path = scenes_dir / "scene-index.json"
    scene_index_path.write_text(scene_index_doc.model_dump_json(), encoding="utf-8")
    scene_index_artifact = domain.create_stage_artifact(StageArtifact(
        project_id=project.id, stage="scene_index", schema_version=1,
        path=str(scene_index_path), input_hash="fake-scene-index-artifact-hash-3",
    ))

    client = TestClient(create_app(config))
    response = client.post(
        f"/api/v1/projects/{project.id}/edit-plans",
        json={
            "transcript_artifact_id": transcript.id,
            "analysis_artifact_id": analysis.id,
            "scene_index_artifact_id": scene_index_artifact.id,
            "start": 0.0,
            "end": 4.0,
            "profile": "balanced",
        },
    )
    assert response.status_code == 201, response.text
    queued = response.json()

    jobs = JobStore(config.paths.database)
    assert process_next(config, jobs, domain, FakeTranscriptionEngine()) is True
    final = jobs.get(queued["id"])
    assert final.status == JobStatus.SUCCEEDED, final.error

    artifact_id = final.result["edit_plan_artifact_id"]
    fetched = client.get(f"/api/v1/projects/{project.id}/edit-plans/{artifact_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["document"]["scene_index_artifact_id"] == scene_index_artifact.id
