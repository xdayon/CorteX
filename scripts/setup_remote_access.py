#!/usr/bin/env python3
"""Liga o acesso remoto do CorteX: Cloudflare Tunnel (token) + validação do Access.

Rodar no PC de casa, depois de criar o túnel e a aplicação do Access no painel
(passo a passo em docs/LOCAL_AND_CLOUDFLARE.md):

    .venv/bin/python scripts/setup_remote_access.py

- O token do túnel é pedido sem eco e gravado em ~/.config/cortex/tunnel.env (0600);
  nunca vai para o repositório nem para o histórico do shell.
- Hostname, team domain, AUD e e-mail vão para o .env do projeto (não versionado),
  como CORTEX__REMOTE_ACCESS__*; as demais linhas do .env são preservadas.
- Cria e ativa o serviço de usuário cortex-tunnel.service (cloudflared).
"""
from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
TOKEN_FILE = CONFIG_HOME / "cortex" / "tunnel.env"
UNIT_FILE = CONFIG_HOME / "systemd" / "user" / "cortex-tunnel.service"
BREW_CLOUDFLARED = Path("/home/linuxbrew/.linuxbrew/bin/cloudflared")
_TOKEN_RE = re.compile(r"(eyJ[A-Za-z0-9_\-=]{40,})")


def _ask(prompt: str, default: str = "") -> str:
    value = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return value or default


def _cloudflared() -> str:
    found = shutil.which("cloudflared") or (str(BREW_CLOUDFLARED) if BREW_CLOUDFLARED.is_file() else None)
    if not found:
        raise SystemExit("cloudflared não encontrado; instale com: brew install cloudflared")
    return found


def _write_token() -> None:
    if TOKEN_FILE.is_file() and _ask("Já existe um token salvo. Substituir? (s/N)", "n").lower() != "s":
        return
    pasted = getpass.getpass(
        "Cole o comando de instalação do painel ou só o token do túnel (não aparece na tela): "
    )
    match = _TOKEN_RE.search(pasted)
    if not match:
        raise SystemExit("Não encontrei um token de túnel no texto colado.")
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        TOKEN_FILE.write_text(f"TUNNEL_TOKEN={match.group(1)}\n", encoding="utf-8")
    finally:
        os.umask(old_umask)
    TOKEN_FILE.chmod(0o600)
    print(f"Token salvo em {TOKEN_FILE} (somente o seu usuário lê).")


def _update_env(values: dict[str, str]) -> None:
    env_path = ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    seen: set[str] = set()
    updated = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in values:
            updated.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            updated.append(line)
    updated.extend(f"{key}={value}" for key, value in values.items() if key not in seen)
    temporary = env_path.with_suffix(".tmp")
    old_umask = os.umask(0o077)
    try:
        temporary.write_text("\n".join(updated) + "\n", encoding="utf-8")
    finally:
        os.umask(old_umask)
    temporary.replace(env_path)
    print(f"Identificadores do Access gravados em {env_path}.")


def _install_unit(cloudflared: str) -> None:
    UNIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    UNIT_FILE.write_text(f"""[Unit]
Description=CorteX remote access (Cloudflare Tunnel)
After=network-online.target cortex.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile={TOKEN_FILE}
ExecStart={cloudflared} tunnel --no-autoupdate run
Restart=always
RestartSec=5
UMask=0077

[Install]
WantedBy=default.target
""", encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "cortex-tunnel.service"], check=True)
    print("Serviço cortex-tunnel.service ativado.")


def main() -> None:
    cloudflared = _cloudflared()
    print("Acesso remoto do CorteX (Cloudflare Tunnel + Access)\n")
    hostname = _ask("Endereço público", "cortex.synapses.academy")
    team = _ask("Team domain do Zero Trust (ex.: seutime.cloudflareaccess.com)")
    audience = _ask("Application Audience (AUD) Tag da aplicação do Access")
    email = _ask("E-mail autorizado (o mesmo da política do Access)")
    if not (team.endswith(".cloudflareaccess.com") and audience and "@" in email):
        raise SystemExit("Team domain, AUD ou e-mail inválidos; confira no painel e rode de novo.")
    _write_token()
    _update_env({
        "CORTEX__REMOTE_ACCESS__ENABLED": "true",
        "CORTEX__REMOTE_ACCESS__PUBLIC_HOSTNAME": hostname,
        "CORTEX__REMOTE_ACCESS__TEAM_DOMAIN": team,
        "CORTEX__REMOTE_ACCESS__AUDIENCE": audience,
        "CORTEX__REMOTE_ACCESS__ALLOWED_EMAILS": f"[{email}]",
    })
    _install_unit(cloudflared)
    print(
        "\nFalta reiniciar o CorteX para ele validar o Access (sem job rodando):\n"
        "  systemctl --user restart cortex.service\n"
        "Para o CorteX e o túnel subirem no boot mesmo sem login:\n"
        "  loginctl enable-linger $USER\n"
        f"Depois abra https://{hostname} no iPhone ou no notebook."
    )


if __name__ == "__main__":
    main()
