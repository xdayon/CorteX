#!/usr/bin/env python3
"""Start the installed Studio service and open its effective local address."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen


def app_commands(url: str) -> list[tuple[list[str], Path]]:
    """Chrome command lines (and their dedicated profile) in order of preference."""
    # A dedicated profile is a separate Chrome process, so --app is always honored even
    # when the user's normal Chrome is open (it would otherwise just get a blank tab).
    app_args = [f"--app={url}", "--class=CorteX", "--no-first-run", "--no-default-browser-check"]
    candidates = []
    if subprocess.run(["flatpak", "info", "com.google.Chrome"], capture_output=True, timeout=15).returncode == 0:
        profile = Path.home() / ".var/app/com.google.Chrome/cortex-app"
        candidates.append((["flatpak", "run", "com.google.Chrome",
                            f"--user-data-dir={profile}", *app_args], profile))
    profile = Path.home() / ".config/cortex/chrome-app"
    candidates += [([name, f"--user-data-dir={profile}", *app_args], profile)
                   for name in ("google-chrome", "google-chrome-stable")]
    return candidates


def open_app_window(url: str) -> tuple[subprocess.Popen, Path] | None:
    """Own app window in Google Chrome (decodes every source codec); else the default browser.

    Returns the Chrome process and its profile so the caller can tell when the window
    closes; None when only the default browser was opened (its lifetime is unknowable).
    """
    for command, profile in app_commands(url):
        try:
            return subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL), profile
        except OSError:
            continue
    subprocess.run(["xdg-open", url], check=True, timeout=15)
    return None


def profile_in_use(profile: Path) -> bool:
    """True while any other process runs Chrome on this profile (window still open)."""
    needle = f"--user-data-dir={profile}".encode()
    me = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            if needle in (entry / "cmdline").read_bytes().split(b"\0"):
                return True
        except OSError:
            continue
    return False


def active_jobs() -> int:
    """Queued or running jobs; stopping the worker now would lose their progress."""
    from cortex.config import load_config
    try:
        connection = sqlite3.connect(f"file:{load_config().paths.database}?mode=ro", uri=True)
        try:
            return connection.execute(
                "select count(*) from jobs where status in ('queued','running')").fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error:
        return 0


def notify(message: str) -> None:
    try:
        subprocess.run(["notify-send", "-a", "CorteX", "CorteX", message],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def stop_with_window(profile: Path, *, poll: float = 2.0) -> None:
    """Block until the app window is closed and no job is left, then stop the service."""
    warned = False
    while True:
        while profile_in_use(profile):
            time.sleep(poll)
        while (jobs := active_jobs()) and not profile_in_use(profile):
            if not warned:
                notify(f"{jobs} job(s) em andamento: o CorteX fecha quando terminarem.")
                warned = True
            time.sleep(poll * 5)
        if profile_in_use(profile):  # reopened while waiting for the jobs
            warned = False
            continue
        subprocess.run(["systemctl", "--user", "stop", "cortex.service"], timeout=60)
        return


def run_window(url: str) -> None:
    """Open the window; the first launcher stays alive and stops the service on close."""
    lock = open(Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "cortex-app.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another launcher owns the window's lifetime; this click only focuses/opens a window.
        open_app_window(url)
        return
    opened = open_app_window(url)
    if opened is None:
        return  # default browser: cannot tell when it closes, leave the service running
    process, profile = opened
    process.wait()
    stop_with_window(profile)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true", help="Check startup without opening a tab")
    args = parser.parse_args()
    subprocess.run(["systemctl", "--user", "start", "cortex.service"], check=True, timeout=30)
    endpoint = Path(__file__).resolve().parents[1] / ".cache/studio-endpoint.json"
    deadline = time.monotonic() + 30
    url = None
    while url is None and time.monotonic() < deadline:
        try:
            port = json.loads(endpoint.read_text())["port"]
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("Porta local inválida")
            candidate = f"http://127.0.0.1:{port}"
            with urlopen(f"{candidate}/api/v1/health", timeout=1) as response:
                if response.status == 200:
                    url = candidate
        except (OSError, ValueError, KeyError, URLError):
            pass
        if url is None:
            time.sleep(0.5)
    if url is None:
        raise SystemExit("CorteX não respondeu em 30s. Consulte journalctl --user -u cortex.service")
    print(url, flush=True)
    if not args.no_browser:
        run_window(url)


if __name__ == "__main__":
    main()
