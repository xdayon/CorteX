#!/usr/bin/env python3
"""Start the installed Studio service and open its effective local address."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen


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
                        subprocess.run(["xdg-open", url], check=True, timeout=15)
                    print(url)
                    return
        except (OSError, ValueError, KeyError, URLError):
            pass
        time.sleep(0.5)
    raise SystemExit("CorteX não respondeu em 30s. Consulte journalctl --user -u cortex.service")


if __name__ == "__main__":
    main()
