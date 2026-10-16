#!/usr/bin/env python3
"""Start the installed Studio service and open its effective local address."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen


def open_app_window(url: str) -> None:
    """Own app window in Google Chrome (decodes every source codec); else the default browser."""
    # A dedicated profile is a separate Chrome process, so --app is always honored even
    # when the user's normal Chrome is open (it would otherwise just get a blank tab).
    app_args = [f"--app={url}", "--class=CorteX", "--no-first-run", "--no-default-browser-check"]
    candidates = []
    if subprocess.run(["flatpak", "info", "com.google.Chrome"], capture_output=True).returncode == 0:
        profile = Path.home() / ".var/app/com.google.Chrome/cortex-app"
        candidates.append(["flatpak", "run", "com.google.Chrome",
                           f"--user-data-dir={profile}", *app_args])
    profile = Path.home() / ".config/cortex/chrome-app"
    candidates += [[name, f"--user-data-dir={profile}", *app_args]
                   for name in ("google-chrome", "google-chrome-stable")]
    for command in candidates:
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return
        except OSError:
            continue
    subprocess.run(["xdg-open", url], check=True, timeout=15)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true", help="Check startup without opening a tab")
    args = parser.parse_args()
    subprocess.run(["systemctl", "--user", "start", "cortex.service"], check=True, timeout=30)
    endpoint = Path(__file__).resolve().parents[1] / ".cache/studio-endpoint.json"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            port = json.loads(endpoint.read_text())["port"]
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("Porta local inválida")
            url = f"http://127.0.0.1:{port}"
            with urlopen(f"{url}/api/v1/health", timeout=1) as response:
                if response.status == 200:
                    if not args.no_browser:
                        open_app_window(url)
                    print(url)
                    return
        except (OSError, ValueError, KeyError, URLError):
            pass
        time.sleep(0.5)
    raise SystemExit("CorteX não respondeu em 30s. Consulte journalctl --user -u cortex.service")


if __name__ == "__main__":
    main()
