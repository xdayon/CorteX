#!/usr/bin/env bash
# Atualiza dependências, verifica FFmpeg/libass e Claude CLI, roda testes e compila a UI numa
# pasta separada. Só com nenhum job na fila ou rodando ela troca a UI servida e reinstala o
# serviço; o serviço só é reiniciado se já estiver ligado (ele vive com a janela do app).
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n== %s ==\n' "$1"; }

active_jobs() {
  .venv/bin/python - <<'PY'
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
}

step "FFmpeg do produto (render.ffmpeg) com o filtro 'subtitles'"
FFMPEG="$(.venv/bin/python -c 'from cortex.config import load_config; print(load_config().render.ffmpeg)')"
"$FFMPEG" -hide_banner -filters | grep -E " subtitles " >/dev/null \
  || { echo "FFmpeg sem libass/subtitles: $FFMPEG" >&2; exit 1; }
echo "FFMPEG_OK ($FFMPEG)"

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

step "Interface web (compilada fora da pasta servida)"
# apps/web/dist é servida ao vivo: compilar direto nela quebra um Studio aberto.
(cd apps/web && npm ci --no-audit --no-fund && npm test \
  && npx tsc --noEmit -p tsconfig.json && npx vite build --outDir dist.next --emptyOutDir)

step "Jobs em andamento"
active=$(active_jobs)
if [[ "$active" != "0" ]]; then
  rm -rf apps/web/dist.next
  echo "Há $active job(s) na fila ou rodando; nada foi trocado nem reiniciado." >&2
  echo "Espere terminar e rode este script de novo." >&2
  exit 2
fi

step "Trocando a UI servida e reinstalando o serviço"
rm -rf apps/web/dist.old
[[ -d apps/web/dist ]] && mv apps/web/dist apps/web/dist.old
mv apps/web/dist.next apps/web/dist
rm -rf apps/web/dist.old
.venv/bin/python scripts/install_local_service.py --install
systemctl --user daemon-reload
# try-restart: reinicia só se o serviço estiver rodando; com o app fechado ele continua parado.
systemctl --user try-restart cortex.service
if systemctl --user is-active --quiet cortex.service; then
  echo "Serviço reiniciado com a versão nova."
else
  echo "Serviço parado (app fechado): a versão nova vale na próxima vez que abrir o CorteX."
fi
echo "== FIM =="
