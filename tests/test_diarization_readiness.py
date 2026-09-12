from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex.diarize.service import readiness


@pytest.fixture
def isolated_readiness(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime" / "python"
    runtime.parent.mkdir()
    runtime.touch()
    cache = tmp_path / "huggingface"
    cache.mkdir()
    monkeypatch.setenv("CORTEX_DIARIZATION_PYTHON", str(runtime))
    monkeypatch.setenv("HF_HOME", str(cache))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HF_TOKEN_PATH", raising=False)
    return runtime, cache


def test_missing_credential_blocks_available_runtime_with_actionable_reason(isolated_readiness):
    state = readiness()
    assert state["runtime_installed"] is True
    assert state["token_configured"] is False
    assert state["ready"] is False
    assert state["device"] == "cpu"
    assert len(state["missing"]) == 1
    assert "HF_TOKEN" in state["missing"][0]
    assert state["model_url"] == "https://huggingface.co/pyannote/speaker-diarization-community-1"


def test_saved_login_is_recognized_without_environment_token_or_disclosure(isolated_readiness):
    _runtime, cache = isolated_readiness
    synthetic_credential = "fixture-credential-not-a-real-token"
    (cache / "token").write_text(synthetic_credential + "\n")
    state = readiness()
    assert state["token_configured"] is True
    assert state["ready"] is True
    assert state["missing"] == []
    assert synthetic_credential not in json.dumps(state)


def test_environment_credential_is_recognized_without_saved_login(isolated_readiness, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "synthetic-environment-credential")
    state = readiness()
    assert state["ready"] is True
    assert "synthetic-environment-credential" not in json.dumps(state)


def test_empty_saved_login_does_not_unlock_diarization(isolated_readiness):
    _runtime, cache = isolated_readiness
    (cache / "token").write_text(" \n\t")
    assert readiness()["token_configured"] is False
    assert readiness()["ready"] is False


def test_custom_token_path_takes_precedence_and_recheck_observes_new_login(
    isolated_readiness, tmp_path, monkeypatch,
):
    _runtime, cache = isolated_readiness
    (cache / "token").write_text("unused-synthetic-default")
    selected = tmp_path / "selected-login"
    monkeypatch.setenv("HF_TOKEN_PATH", str(selected))
    assert readiness()["ready"] is False
    selected.write_text("synthetic-selected-login")
    assert readiness()["ready"] is True


def test_xdg_cache_login_is_used_when_hf_home_is_not_set(isolated_readiness, tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HOME")
    login = tmp_path / "xdg" / "huggingface" / "token"
    login.parent.mkdir(parents=True)
    login.write_text("synthetic-xdg-login")
    assert readiness()["ready"] is True


def test_missing_runtime_stays_blocked_even_with_saved_login(isolated_readiness):
    runtime, cache = isolated_readiness
    runtime.unlink()
    (cache / "token").write_text("synthetic-saved-login")
    state = readiness()
    assert state["runtime_installed"] is False
    assert state["token_configured"] is True
    assert state["ready"] is False
    assert len(state["missing"]) == 1
    assert "scripts/setup_diarization.sh" in state["missing"][0]


def test_unreadable_saved_login_is_an_unready_state_not_an_exception(isolated_readiness, monkeypatch):
    _runtime, cache = isolated_readiness
    token_file = cache / "token"
    token_file.touch()
    original = Path.read_text

    def denied(path, *args, **kwargs):
        if path == token_file:
            raise PermissionError("synthetic access denial")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    assert readiness()["ready"] is False


def test_pinned_pyannote_batch_generator_is_consumed_and_returns_prediction():
    from cortex.diarize.runner import single_file_prediction
    output = object()
    observed = []

    def pipeline(files, **options):
        observed.append((files, options))
        yield files[0], output

    assert single_file_prediction(pipeline, {'uri': 'episode'}, num_speakers=3) is output
    assert observed == [([{'uri': 'episode'}], {'num_speakers': 3})]


def test_progress_accepts_numpy_counters_and_persists_atomic_json(tmp_path):
    import numpy as np
    from cortex.diarize.runner import write_progress
    target = tmp_path / 'progress.json'
    write_progress(target, 'segmentation', np.int64(200), np.int64(32))
    assert json.loads(target.read_text()) == {'step': 'segmentation', 'total': 200, 'completed': 32}
    assert not target.with_suffix('.tmp').exists()
