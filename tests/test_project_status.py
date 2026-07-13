from __future__ import annotations

from cortex.api import create_app
from cortex.config import load_config
from cortex.project_status import read_project_status


def test_read_project_status_counts_checked_roadmap_items(tmp_path):
    roadmap = tmp_path / "ROADMAP.md"
    roadmap.write_text(
        "## Fase 0 - Fundação (concluída)\n\n- [x] item pronto\n- [ ] item pendente\n\n## Fase 1 - Produção\n\n- item sem checkbox\n",
        encoding="utf-8",
    )

    status = read_project_status(roadmap)

    assert status["summary"] == {"completed": 2, "pending": 1, "total": 3, "progress_percent": 67}
    assert status["phases"][0]["status"] == "concluída"
    assert status["phases"][1]["items"][0] == {"label": "item sem checkbox", "completed": False}


def test_project_status_endpoint_exposes_versioned_roadmap(tmp_path):
    base_config = load_config()
    config = base_config.model_copy(update={
        "paths": base_config.paths.model_copy(update={
            "data_dir": tmp_path,
            "projects_dir": tmp_path / "projects",
            "cache_dir": tmp_path / "cache",
            "output_dir": tmp_path / "output",
            "database": tmp_path / "cortex.sqlite3",
        }),
    })
    paths = create_app(config).openapi()["paths"]

    assert "/api/v1/project-status" in paths
    assert "get" in paths["/api/v1/project-status"]
