from __future__ import annotations

from pathlib import Path

from cortex.config import CortexConfig


def project_dir(config: CortexConfig, project_id: str) -> Path:
    return config.paths.projects_dir / project_id


def source_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "source"


def cache_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "cache"


def transcripts_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "transcripts"


def suggestions_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "suggestions"


def analysis_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "analysis"


def edit_plans_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "edit_plans"


def renders_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "renders"


def scenes_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "scenes"


def faces_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "faces"


def speakers_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "speakers"


def cameras_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "cameras"


def visual_quality_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "visual_quality"


def identities_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "identities"


def reaction_candidates_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "reaction_candidates"


def camera_plans_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "camera_plans"


def multicam_sync_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "multicam_sync"


def multicam_visual_dir(config: CortexConfig, project_id: str) -> Path:
    return project_dir(config, project_id) / "multicam_visual"
