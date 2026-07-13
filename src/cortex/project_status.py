"""Read the delivery roadmap into a small, API-safe project status document."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path


_PHASE_HEADING = re.compile(r"^## (Fase \d+ - .+?)\s*(?:\(([^)]+)\))?\s*$")
_CHECKBOX = re.compile(r"^- \[([ xX])\] (.+)$")
_BULLET = re.compile(r"^- (.+)$")


def roadmap_path() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "ROADMAP.md"


def read_project_status(path: Path | None = None) -> dict[str, object]:
    """Return phases and delivery counts from the versioned roadmap.

    The roadmap is deliberately the persisted source of truth: this module does
    not infer implementation state from source files or manufacture progress.
    """
    source = path or roadmap_path()
    lines = source.read_text(encoding="utf-8").splitlines()
    phases: list[dict[str, object]] = []
    current: dict[str, object] | None = None

    for line in lines:
        heading = _PHASE_HEADING.match(line)
        if heading:
            current = {
                "name": heading.group(1),
                "status": (heading.group(2) or "pendente").strip().lower(),
                "items": [],
            }
            phases.append(current)
            continue
        if current is None:
            continue

        checkbox = _CHECKBOX.match(line)
        bullet = _BULLET.match(line)
        if checkbox:
            current["items"].append({"label": checkbox.group(2), "completed": checkbox.group(1).lower() == "x"})
        elif bullet:
            current["items"].append({"label": bullet.group(1), "completed": False})
        elif line.startswith((" ", "\t")) and current["items"]:
            # Markdown wraps long list items onto indented continuation lines.
            current["items"][-1]["label"] = f"{current['items'][-1]['label']} {line.strip()}"

    for phase in phases:
        if "conclu" in str(phase["status"]):
            for item in phase["items"]:
                item["completed"] = True

    completed = sum(1 for phase in phases for item in phase["items"] if item["completed"])
    total = sum(len(phase["items"]) for phase in phases)
    return {
        "source": "docs/ROADMAP.md",
        "updated_at": datetime.fromtimestamp(source.stat().st_mtime, UTC).isoformat(),
        "summary": {
            "completed": completed,
            "pending": total - completed,
            "total": total,
            "progress_percent": round((completed / total) * 100) if total else 0,
        },
        "phases": phases,
    }
