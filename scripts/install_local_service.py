#!/usr/bin/env python3
"""Install a user service and app launcher; no sudo, network or secret copying."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess


def unit_quote(value: str) -> str:
    if any(c in value for c in "\n\r\x00"):
        raise ValueError("Invalid service path")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def service_text(root: Path, binary_dirs: list[Path]) -> str:
    # Resolve fnm/nvm links now: login-shell paths under /run disappear at logout.
    search_path = ":".join(dict.fromkeys([str(p) for p in binary_dirs] + ["/usr/local/bin", "/usr/bin", "/bin"]))
    return f"""[Unit]
Description=CorteX local Studio and media worker
After=network.target

[Service]
Type=simple
WorkingDirectory={str(root).replace('%', '%%')}
Environment={unit_quote('PATH=' + search_path)}
EnvironmentFile=-{str(root / '.env').replace('%', '%%')}
ExecStart=/usr/bin/bash {unit_quote(str(root / 'scripts/studio.sh'))}
Restart=always
RestartSec=5
KillMode=control-group
TimeoutStopSec=20
UMask=0077

[Install]
WantedBy=default.target
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Install and enable at user login; does not start jobs now")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    binary_dirs = []
    for binary in ("node", "codex"):
        location = shutil.which(binary)
        if location is None:
            raise SystemExit(f"Instale {binary} antes de configurar o serviço")
        # Codex can be an npm JS symlink. Its PATH entry must be the bin directory,
        # resolved via the neighboring node executable, not the JS package folder.
        parent = Path(location).parent.resolve()
        binary_dirs.append(parent)
    text = service_text(root, binary_dirs)
    if not args.install:
        print(text, end="")
        return
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    unit = config_home / "systemd/user/cortex.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    if unit.exists() and unit.read_text() != text:
        unit.with_suffix(".service.previous").write_text(unit.read_text())
    unit.write_text(text)
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    launcher = data_home / "applications/cortex.desktop"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    # Desktop Exec has its own escaping rules (including percent field codes).
    def desktop_quote(path: Path) -> str:
        value = str(path).replace("%", "%%")
        for char in ('\\', '"', '`', '$'):
            value = value.replace(char, '\\' + char)
        return '"' + value + '"'
    launcher.write_text(f"""[Desktop Entry]
Type=Application
Name=CorteX
Comment=Editor de cortes Dayon News
Exec={desktop_quote(root / '.venv/bin/python')} {desktop_quote(root / 'scripts/open_studio.py')}
Icon=video-x-generic
Terminal=false
Categories=AudioVideo;Video;
""")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "cortex.service"], check=True)
    print(f"Instalado: {unit}\nAtalho: {launcher}")
    print("Inicie quando desejar: systemctl --user start cortex.service")


if __name__ == "__main__":
    main()
