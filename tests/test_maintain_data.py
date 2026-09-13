from __future__ import annotations

import os
import sqlite3
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from cortex.config import CortexConfig
_SPEC = spec_from_file_location(
    "maintain_data", Path(__file__).parents[1] / "scripts" / "maintain_data.py",
)
assert _SPEC is not None and _SPEC.loader is not None
maintain_data = module_from_spec(_SPEC)
_SPEC.loader.exec_module(maintain_data)


def _config(tmp_path: Path) -> CortexConfig:
    base = CortexConfig()
    return base.model_copy(update={
        "paths": base.paths.model_copy(update={
            "data_dir": tmp_path / "data",
            "projects_dir": tmp_path / "data" / "projects",
            "cache_dir": tmp_path / "data" / "cache",
            "output_dir": tmp_path / "data" / "output",
            "database": tmp_path / "data" / "cortex.sqlite3",
        }),
    })


def test_backup_and_restore_are_safe_and_exclude_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    with sqlite3.connect(config.paths.database) as database:
        database.execute("CREATE TABLE marker (value TEXT)")
        database.execute("INSERT INTO marker VALUES ('preserved')")
    artifact = config.paths.projects_dir / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")
    cached = config.paths.cache_dir / "frame.npz"
    cached.write_bytes(b"cache")
    monkeypatch.setattr(maintain_data, "load_config", lambda: config)

    archive = tmp_path / "backup.tar.gz"
    maintain_data.backup(archive, include_cache=False)
    target = tmp_path / "restored"
    maintain_data.restore(archive, target, apply=False)
    assert not target.exists()
    maintain_data.restore(archive, target, apply=True)

    assert (target / "projects" / "artifact.json").exists()
    assert not (target / "cache" / "frame.npz").exists()
    with sqlite3.connect(target / "cortex.sqlite3") as database:
        assert database.execute("SELECT value FROM marker").fetchone()[0] == "preserved"


def test_prune_cache_previews_before_deleting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    cached = config.paths.cache_dir / "old.bin"
    cached.write_bytes(b"old")
    os.utime(cached, (0, 0))
    monkeypatch.setattr(maintain_data, "load_config", lambda: config)

    maintain_data.prune_cache(1, apply=False)
    assert cached.exists()
    maintain_data.prune_cache(1, apply=True)
    assert not cached.exists()


def test_prune_cache_rejects_non_positive_age() -> None:
    with pytest.raises(SystemExit, match="maior que zero"):
        maintain_data.prune_cache(0, apply=False)
