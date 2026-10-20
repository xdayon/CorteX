#!/usr/bin/env bash
# Atualiza dependências, verifica FFmpeg/libass e Claude CLI, roda testes, recompila
# a UI e só então reinstala/reinicia o serviço — e nunca com job em andamento.
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n== %s ==\n' "$1"; }

step "FFmpeg (precisa do filtro 'subtitles')"
/usr/bin/ffmpeg -hide_banner -filters | grep -E " subtitles " >/dev/null \
  || { echo "FFmpeg sem libass/subtitles" >&2; exit 1; }
echo FFMPEG_OK

step "Dependências Python"
# O .venv criado pelo uv não traz pip; com uv disponível, ele instala no mesmo .venv.
if command -v uv >/dev/null; then
  uv pip install -q --python .venv/bin/python -e '.[dev,transcription,analysis,youtube]'
  uv lock --check
else
  .venv/bin/python -m pip install -q -e '.[dev,transcription,analysis,youtube]'
fi
echo DEPS_OK

step "Claude Code CLI (provider editorial)"
.venv/bin/python - <<'PY'
from cortex.config import load_config
from cortex.suggest.provider import resolve_claude_binary
config = load_config()
if config.ai.provider == "claude_code_cli":
    resolved = resolve_claude_binary(config.ai.claude_binary)
    print(f"{resolved.version} via {resolved.source}: {resolved.path}")
else:
    print(f"provider configurado: {config.ai.provider}")
PY

step "Testes backend"
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/cortex scripts tests

step "Interface web"
(cd apps/web && npm test && npm run build)

step "Jobs em andamento"
active=$(.venv/bin/python - <<'PY'
import sqlite3
from cortex.config import load_config
database = load_config().paths.database
try:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    print(connection.execute(
        "select count(*) from jobs where status in ('queued','running')").fetchone()[0])
except sqlite3.Error:
    print(0)
PY
)
if [[ "$active" != "0" ]]; then
  echo "Há $active job(s) na fila ou rodando; o serviço NÃO foi reiniciado." >&2
  echo "Espere terminar e rode: systemctl --user restart cortex.service" >&2
  exit 2
fi

step "Reinstalando e reiniciando o serviço"
.venv/bin/python scripts/install_local_service.py --install
systemctl --user daemon-reload
systemctl --user restart cortex.service
systemctl --user is-active cortex.service
echo "== FIM =="
