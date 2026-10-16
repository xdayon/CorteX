from __future__ import annotations

import json
from pathlib import Path

from asgi_client import ASGITestClient

from cortex.api import create_app
from cortex.clips import clip_key, derive_status, js_number
from cortex.domain.models import (
    ClipPublication, ExportBatch, ExportItem, ExportItemStatus, SourceAsset, SourceKind,
    StageArtifact, WorkflowRun,
)
from cortex.domain.store import DomainStore

from test_export_batches import _config

P = "/api/v1"


def _clip(rank: int, start: float, end: float, pacing: str = "fast") -> dict:
    return {
        "rank": rank, "title": f"Corte {rank}", "headline": f"Título {rank}",
        "headline_options": ["a", "b"], "start_second": start, "end_second": end,
        "estimated_duration": end - start, "pacing": pacing, "pieces": [{}, {}],
        "scores": {"total": 80 - rank}, "warnings": [],
    }


CLIPS = [_clip(1, 1400.0, 1460.0), _clip(2, 4948.8, 5010.2), _clip(3, 10, 70)]
KEYS = ["1:1400:1460:fast", "2:4948.8:5010.2:fast", "3:10:70:fast"]


def _artifact(domain, project_id, stage, tmp_path, name, document) -> StageArtifact:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return domain.create_stage_artifact(StageArtifact(
        project_id=project_id, stage=stage, path=str(path), input_hash=name,
    ))


def _world(tmp_path: Path):
    config = _config(tmp_path)
    domain = DomainStore(config.paths.database)
    project = domain.create_project("Ep")
    source = domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, stored_path="s.mp4",
        sha256="a" * 64, size_bytes=1,
    ))
    suggestion = _artifact(domain, project.id, "suggestion", tmp_path, "sug",
                           {"selection": {"clips": CLIPS}})
    run = domain.create_workflow_run(WorkflowRun(
        project_id=project.id, source_asset_id=source.id, status="ready_for_review",
        stage="review", artifacts={"suggestion": suggestion.id},
        brief={"count": 3, "minimum_seconds": 40, "maximum_seconds": 120},
    ))
    export = _artifact(domain, project.id, "render", tmp_path, "export-1", {
        "purpose": "export", "timeline_duration_seconds": 61.5,
        "output_path": str(tmp_path / "render-1.mp4"),
    })
    draft = _artifact(domain, project.id, "render", tmp_path, "draft-render-1", {
        "purpose": "draft", "output_path": str(tmp_path / "draft-render-1.mp4"),
    })
    loose = _artifact(domain, project.id, "render", tmp_path, "export-2", {
        "purpose": "export", "timeline_duration_seconds": 30,
        "output_path": str(tmp_path / "render-2.mp4"),
    })
    batch = ExportBatch(
        project_id=project.id, workflow_run_id=run.id, revision_id="r", idempotency_key="k",
    )
    batch.items = [ExportItem(
        batch_id=batch.id, project_id=project.id, position=0, clip_key=KEYS[0], title="t",
        clip=CLIPS[0], render_settings={"headline": {"text": "Manchete"}},
        status=ExportItemStatus.SUCCEEDED, render_artifact_id=export.id,
    )]
    with domain.transaction() as connection:
        domain.create_export_batch(batch, connection=connection)
    domain.upsert_clip_publication(ClipPublication(
        project_id=project.id, render_artifact_id=export.id, title="T", published=True,
        platforms=["instagram"],
    ))
    client = ASGITestClient(create_app(config))
    return client, domain, project, run, export, draft, loose


def _patch(client, project, run, key, **fields):
    return client.request("PATCH", f"{P}/clip-states", json={"items": [
        {"project_id": project.id, "run_id": run.id, "clip_key": key, **fields}]})


def test_js_number_and_key_mirror_javascript() -> None:
    assert js_number(1400.0) == "1400" and js_number(4948.8) == "4948.8"
    assert js_number(0.0000001) == "1e-7" and js_number(1e21) == "1e+21"
    assert js_number(0.00001) == "0.00001" and js_number(-0.0) == "0"
    assert clip_key(CLIPS[0]) == KEYS[0]


def test_derive_status_precedence() -> None:
    assert derive_status(None, [], None) == "new"
    assert derive_status(None, [{"x": 1}], None) == "exported"


def test_episode_clips_integrates_exports_publication_and_state(tmp_path: Path) -> None:
    client, _, project, run, export, draft, loose = _world(tmp_path)
    response = client.get(f"{P}/episodes/local-{project.id}/clips")
    assert response.status_code == 200
    body = response.json()
    assert [c["clip_key"] for c in body["clips"]] == KEYS
    first, second, third = body["clips"]
    assert (first["status"], second["status"], third["status"]) == ("posted", "new", "new")
    assert first["score"] == 79 and first["piece_count"] == 2
    assert first["exports"][0]["render_artifact_id"] == export.id
    assert first["exports"][0]["media_url"] == f"{P}/projects/{project.id}/renders/{export.id}/media"
    assert first["exports"][0]["headline"] == "Manchete"
    assert first["publication"]["published"] is True
    assert first["source_asset_id"] == run.source_asset_id
    assert body["runs"][0]["source_asset_id"] == run.source_asset_id
    assert body["runs"][0]["clip_count"] == 3 and body["runs"][0]["brief"]["count"] == 3
    assert [e["render_artifact_id"] for e in body["other_exports"]] == [loose.id]
    assert draft.id not in json.dumps(body)
    assert body["counts"]["posted"] == 1 and body["counts"]["new"] == 2
    assert body["counts"]["total"] == 3 and body["warnings"] == []
    assert client.get(f"{P}/episodes/nope/clips").status_code == 404


def test_patch_states_and_partial_update(tmp_path: Path) -> None:
    client, _, project, run, *_ = _world(tmp_path)
    key = KEYS[1]
    state = _patch(client, project, run, key, decision="discarded", discard_reason="weak_hook")
    assert state.status_code == 200
    item = state.json()["items"][0]
    assert item["decision"] == "discarded" and item["discard_reason"] == "weak_hook"
    saved = _patch(client, project, run, key, saved=True).json()["items"][0]
    assert saved["decision"] == "discarded" and saved["discard_reason"] == "weak_hook"
    assert saved["saved"] is True and saved["saved_at"] and saved["id"] == item["id"]
    noted = _patch(client, project, run, key, notes="ok").json()["items"][0]
    assert noted["saved_at"] == saved["saved_at"] and noted["notes"] == "ok"

    listing = client.get(f"{P}/episodes/local-{project.id}/clips").json()
    assert listing["clips"][1]["status"] == "discarded"
    cleared = _patch(client, project, run, key, decision=None, saved=False).json()["items"][0]
    assert cleared["decision"] is None and cleared["discard_reason"] is None
    assert cleared["saved"] is False and cleared["saved_at"] is None
    assert _patch(client, project, run, key, saved=True).status_code == 200
    listing = client.get(f"{P}/episodes/local-{project.id}/clips").json()
    assert listing["clips"][1]["status"] == "saved"
    _patch(client, project, run, key, saved=False)
    _patch(client, project, run, key, decision="approved")
    listing = client.get(f"{P}/episodes/local-{project.id}/clips").json()
    assert listing["clips"][1]["status"] == "approved"
    other = _patch(client, project, run, KEYS[2], decision="approved", saved=True)
    assert other.status_code == 200


def test_patch_validation_errors(tmp_path: Path) -> None:
    client, _, project, run, *_ = _world(tmp_path)
    assert _patch(client, project, run, KEYS[0], decision="approved",
                  discard_reason="other").status_code == 422
    assert _patch(client, project, run, KEYS[0], discard_reason="other").status_code == 422
    assert _patch(client, project, run, KEYS[0], decision="bogus").status_code == 422
    assert _patch(client, project, run, "9:9:9:fast", saved=True).status_code == 404
    bad_run = client.request("PATCH", f"{P}/clip-states", json={"items": [
        {"project_id": project.id, "run_id": "nope", "clip_key": KEYS[0]}]})
    assert bad_run.status_code == 404
    wrong_project = client.request("PATCH", f"{P}/clip-states", json={"items": [
        {"project_id": "other", "run_id": run.id, "clip_key": KEYS[0]}]})
    assert wrong_project.status_code == 404
    assert client.request("PATCH", f"{P}/clip-states", json={"items": []}).status_code == 422


def test_global_list_filter_and_summary(tmp_path: Path) -> None:
    client, _, project, run, *_ = _world(tmp_path)
    _patch(client, project, run, KEYS[1], decision="discarded", discard_reason="other")
    everything = client.get(f"{P}/clips").json()
    assert everything["total"] == 3
    assert everything["clips"][0]["clip_key"] == KEYS[1]
    assert everything["clips"][0]["episode_id"] == f"local-{project.id}"
    assert "episode_title" in everything["clips"][0]
    only = client.get(f"{P}/clips?status=posted,discarded").json()
    assert {c["status"] for c in only["clips"]} == {"posted", "discarded"}
    assert client.get(f"{P}/clips?status=new&limit=1").json()["clips"][0]["status"] == "new"
    assert client.get(f"{P}/clips?status=bogus").status_code == 422
    summary = client.get(f"{P}/clips/summary").json()
    counts = summary["episodes"][f"local-{project.id}"]
    assert counts["posted"] == 1 and counts["discarded"] == 1 and counts["new"] == 1
    assert counts["total"] == 3


def test_unreadable_suggestion_is_a_warning_not_a_500(tmp_path: Path) -> None:
    client, domain, project, run, *_ = _world(tmp_path)
    artifact = domain.get_stage_artifact(run.artifacts["suggestion"])
    Path(artifact.path).write_text("{broken", encoding="utf-8")
    response = client.get(f"{P}/episodes/local-{project.id}/clips")
    assert response.status_code == 200
    body = response.json()
    assert body["clips"] == [] and body["warnings"]
