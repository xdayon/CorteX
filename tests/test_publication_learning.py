from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from cortex.api import create_app
from cortex.domain.models import ClipPublication, StageArtifact
from cortex.domain.store import ClipPublicationNotFoundError, DomainStore
from cortex.publication_api import PublicationUpsert
from cortex.suggest.learning import build_learning_context
from cortex.suggest.service import SuggestionService

from asgi_client import ASGITestClient

from test_render_presets_api import _config as _api_config
from test_selection_units import ScriptedProvider, _doc
from test_suggestion_cli import _analysis, _config, _transcript


def _pub(project_id: str, n: int, **fields) -> ClipPublication:
    base = dict(
        project_id=project_id, render_artifact_id=f"render-{n}", title=f"Corte {n}",
        headline=f"Título {n}", hook_text=f"Gancho {n}", duration_seconds=60 + n,
    )
    return ClipPublication(**{**base, **fields})


def _store(tmp_path: Path) -> tuple[DomainStore, str]:
    store = DomainStore(tmp_path / "domain.sqlite3")
    return store, store.create_project("P").id


# -- store -----------------------------------------------------------------

def test_store_upsert_get_list_delete(tmp_path: Path) -> None:
    store, project = _store(tmp_path)
    first = store.upsert_clip_publication(_pub(project, 1, views=10))
    again = store.upsert_clip_publication(_pub(project, 1, views=99, rating=1))
    assert again.id == first.id and again.created_at == first.created_at
    assert store.get_clip_publication(project, "render-1").views == 99
    store.upsert_clip_publication(_pub(project, 2))
    assert [p.render_artifact_id for p in store.list_clip_publications()] == ["render-2", "render-1"]
    store.delete_clip_publication(project, "render-1")
    with pytest.raises(ClipPublicationNotFoundError):
        store.get_clip_publication(project, "render-1")
    with pytest.raises(ClipPublicationNotFoundError):
        store.delete_clip_publication(project, "render-1")


def test_migration_on_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    store = DomainStore(path)
    project = store.create_project("Antigo").id
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE clip_publications")
        connection.execute("DELETE FROM schema_migrations WHERE id LIKE '%clip-publications%'")
    reopened = DomainStore(path)
    reopened.upsert_clip_publication(_pub(project, 1))
    assert reopened.get_project(project).name == "Antigo"
    with sqlite3.connect(path) as connection:
        ids = {row[0] for row in connection.execute("SELECT id FROM schema_migrations")}
    assert "2026-10-14-clip-publications-v1" in ids


# -- API -------------------------------------------------------------------

def _render(domain: DomainStore, project_id: str, tmp_path: Path, name: str) -> StageArtifact:
    manifest = tmp_path / f"{name}.json"
    manifest.write_text(json.dumps({
        "timeline_duration_seconds": 74.2,
        "requested_settings": {"headline": {"text": "Ninguém te conta isso"}},
    }), encoding="utf-8")
    return domain.create_stage_artifact(StageArtifact(
        project_id=project_id, stage="render", path=str(manifest), input_hash=name,
    ))


def test_publication_api(tmp_path: Path) -> None:
    config = _api_config(tmp_path)
    client = ASGITestClient(create_app(config))
    domain = DomainStore(config.paths.database)
    first = client.post("/api/v1/projects", json={"name": "A"}).json()["id"]
    second = client.post("/api/v1/projects", json={"name": "B"}).json()["id"]
    render_a = _render(domain, first, tmp_path, "a").id
    render_b = _render(domain, first, tmp_path, "b").id

    def url(project: str, render: str) -> str:
        return f"/api/v1/projects/{project}/renders/{render}/publication"

    saved = client.request("PUT", url(first, render_a), json={
        "published": True, "platforms": ["instagram"], "views": 1200, "rating": 1,
        "url": "https://instagram.com/reel/x",
    })
    assert saved.status_code == 200
    body = saved.json()
    assert (body["headline"], body["title"], body["duration_seconds"]) == (
        "Ninguém te conta isso", "Ninguém te conta isso", 74.2)
    updated = client.request("PUT", url(first, render_a), json={"title": "Meu título", "views": 2000})
    assert updated.json()["id"] == body["id"] and updated.json()["rating"] is None
    assert client.get(url(first, render_a)).json()["views"] == 2000
    client.request("PUT", url(first, render_b), json={"views": 5})
    listed = client.get("/api/v1/publications?limit=10").json()
    assert [p["render_artifact_id"] for p in listed] == [render_b, render_a]

    assert client.request("PUT", url(second, render_a), json={}).status_code == 404
    assert client.request("PUT", url(first, "inexistente"), json={}).status_code == 404
    missing = client.get(url(first, "inexistente"))
    assert missing.status_code == 404 and missing.json()["detail"] == "Publicação não registrada"
    assert client.request("DELETE", url(second, render_a)).status_code == 404
    assert client.request("PUT", url(first, render_a), json={"views": -3}).status_code == 422
    assert client.request("DELETE", url(first, render_a)).status_code == 204
    assert client.get(url(first, render_a)).status_code == 404


@pytest.mark.parametrize("payload", [
    {"views": -1}, {"retention_3s_pct": 101}, {"url": "ftp://x.com"}, {"url": "javascript:alert(1)"},
    {"rating": 2}, {"platforms": ["youtube"]}, {"notes": "x" * 301}, {"hook_text": "x" * 201},
    {"avg_watch_seconds": -0.5},
])
def test_publication_validation(payload: dict) -> None:
    with pytest.raises(ValidationError):
        PublicationUpsert(**payload)


# -- learning context --------------------------------------------------------

def _fixture(store: DomainStore, project: str) -> None:
    rows = [
        dict(views=12300, shares=200, saves=180, rating=1, headline="Os ETs já estão aqui"),
        dict(views=800, shares=2, saves=1, rating=1, headline="Pirâmides e consciência"),
        dict(views=5000, shares=100, saves=50, rating=0, headline="Saúde e sistema"),
        dict(views=40, rating=-1, headline="Intro do episódio"),
        dict(views=9000, shares=10, saves=5, rating=-1, headline="Conversa solta"),
        dict(views=3000, shares=90, saves=60, headline="Segredo antigo"),
    ]
    for index, row in enumerate(rows, 1):
        store.upsert_clip_publication(_pub(project, index, published=True, **row))


def test_learning_context_threshold(tmp_path: Path) -> None:
    store, project = _store(tmp_path)
    assert build_learning_context(store) == ""
    store.upsert_clip_publication(_pub(project, 1, rating=1))
    store.upsert_clip_publication(_pub(project, 2, views=10))
    store.upsert_clip_publication(_pub(project, 3, published=True))
    assert build_learning_context(store) == ""
    store.upsert_clip_publication(_pub(project, 3, rating=0))
    assert build_learning_context(store).startswith("## Aprendizado do perfil")


def test_learning_context_ranking_format_and_size(tmp_path: Path) -> None:
    store, project = _store(tmp_path)
    _fixture(store, project)
    text = build_learning_context(store)
    assert text == build_learning_context(store)
    best, worst = text.split("Funcionaram pior:")
    best_lines = [line for line in best.splitlines() if line.startswith("- ")]
    worst_lines = [line for line in worst.splitlines() if line.startswith("- ")]
    assert len(best_lines) == 3 and len(worst_lines) == 2
    # An "ok" clip is neither a model to follow nor one to avoid.
    assert "Saúde e sistema" not in text
    assert best_lines[0] == (
        '- "Os ETs já estão aqui" — gancho: "Gancho 1" — 61 s — 12,3 mil views'
        " · 3,1% compart.+salv. — nota: ótimo"
    )
    assert "Pirâmides e consciência" in best_lines[1] and "800 views" in best_lines[1]
    assert "Segredo antigo" in best_lines[2]
    assert "Intro do episódio" in worst_lines[0] and "nota: ruim" in worst_lines[0]
    assert "compart" not in worst_lines[0]
    assert "sem copiar frases" in text.splitlines()[-1]
    assert len(text) < 1750


def test_learning_context_size_bound_with_many_long_records(tmp_path: Path) -> None:
    store, project = _store(tmp_path)
    for index in range(40):
        store.upsert_clip_publication(_pub(
            project, index, headline="H" * 280, hook_text="G" * 190, views=1000 + index, rating=1,
        ))
    assert len(build_learning_context(store)) < 1750


# -- selection ---------------------------------------------------------------

def test_selection_context_has_learning_and_cache_key_changes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    domain = DomainStore(config.paths.database)
    project = domain.create_project("P").id
    transcript = _transcript(domain, tmp_path)
    analysis = _analysis(domain, tmp_path, transcript)
    brief = {"minimum_seconds": 5, "maximum_seconds": 20, "count": 1}

    def run(provider):
        return SuggestionService(config, domain, provider).run(
            transcript_artifact=transcript, analysis_artifact=analysis, brief=dict(brief),
            progress_cb=lambda _p, _m: None, should_cancel=lambda: False,
        )

    first = ScriptedProvider([_doc((0, 10, 7))])
    run(first)
    assert "## Aprendizado do perfil" not in first.prompts[0]
    assert run(ScriptedProvider([]))["cached"] is True

    _fixture(domain, project)
    second = ScriptedProvider([_doc((0, 10, 7))])
    assert run(second)["cached"] is False
    prompt = second.prompts[0]
    assert prompt.index("## Aprendizado do perfil") < prompt.index("## Episódio")
    assert "Os ETs já estão aqui" in prompt

    domain.upsert_clip_publication(_pub(project, 1, published=True, views=12300, rating=-1))
    assert run(ScriptedProvider([_doc((0, 10, 7))]))["cached"] is False


def test_unrated_clips_need_clear_engagement_signal(tmp_path: Path) -> None:
    store, project = _store(tmp_path)
    rows = [
        dict(views=1000, shares=50, saves=50),   # 10% -> best (above median)
        dict(views=1000, shares=20, saves=10),   # 3% -> median, best
        dict(views=1000, shares=10, saves=10),   # 2% -> neither
        dict(views=1000, shares=2, saves=3),     # 0.5% -> worst (below median / 2)
        dict(views=50, shares=40, saves=40),     # too few views -> excluded
    ]
    for index, row in enumerate(rows, 1):
        store.upsert_clip_publication(_pub(project, index, published=True, headline=f"C{index}", **row))
    text = build_learning_context(store)
    best, worst = text.split("Funcionaram pior:")
    assert '"C1"' in best and '"C2"' in best and '"C3"' not in text and '"C5"' not in text
    assert '"C4"' in worst
