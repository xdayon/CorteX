import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/open_studio.py"
spec = importlib.util.spec_from_file_location("open_studio", SCRIPT)
open_studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(open_studio)


def test_stops_service_only_after_window_closed_and_jobs_done(monkeypatch):
    profile = Path("/p")
    in_use = iter([True, True, False, False, False, False])  # window closes, then idle
    jobs = iter([2, 0])
    calls = []
    monkeypatch.setattr(open_studio, "profile_in_use", lambda _p: next(in_use))
    monkeypatch.setattr(open_studio, "active_jobs", lambda: next(jobs))
    monkeypatch.setattr(open_studio, "notify", lambda message: calls.append(("notify", message)))
    monkeypatch.setattr(open_studio.time, "sleep", lambda _s: None)
    monkeypatch.setattr(open_studio.subprocess, "run", lambda cmd, **_k: calls.append(tuple(cmd)))
    open_studio.stop_with_window(profile)
    assert [c[0] for c in calls] == ["notify", "systemctl"]
    assert calls[1] == ("systemctl", "--user", "stop", "cortex.service")


def test_reopened_window_cancels_the_stop(monkeypatch):
    # closed -> job running -> window reopened -> closed again -> idle -> stop
    in_use = iter([False, False, True, True, False, False, False])
    jobs = iter([1, 0, 0])
    stops = []
    monkeypatch.setattr(open_studio, "profile_in_use", lambda _p: next(in_use, False))
    monkeypatch.setattr(open_studio, "active_jobs", lambda: next(jobs, 0))
    monkeypatch.setattr(open_studio, "notify", lambda _m: None)
    monkeypatch.setattr(open_studio.time, "sleep", lambda _s: None)
    monkeypatch.setattr(open_studio.subprocess, "run", lambda cmd, **_k: stops.append(cmd))
    open_studio.stop_with_window(Path("/p"))
    assert len(stops) == 1


def test_profile_in_use_detects_own_profile_only(tmp_path):
    import subprocess
    import sys
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                              f"--user-data-dir={tmp_path}"])
    try:
        assert open_studio.profile_in_use(tmp_path)
        assert not open_studio.profile_in_use(tmp_path / "other")
    finally:
        child.kill()
        child.wait()
    assert not open_studio.profile_in_use(tmp_path)
