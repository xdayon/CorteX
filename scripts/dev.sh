#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API="$ROOT/.venv/bin/cortex-api"

# shellcheck source=./cuda_env.sh
source "$ROOT/scripts/cuda_env.sh"

if [[ ! -x "$API" ]]; then
  echo "CorteX backend nao instalado." >&2
  echo "Rode: python3 -m venv .venv && .venv/bin/pip install -e '.[dev,transcription,analysis,youtube]'" >&2
  exit 1
fi

if [[ ! -x "$ROOT/apps/web/node_modules/.bin/vite" ]]; then
  echo "Frontend nao instalado." >&2
  echo "Rode: cd apps/web && npm install" >&2
  exit 1
fi

children=()
cleanup() {
  for pid in "${children[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "CorteX API:    http://127.0.0.1:8787/docs"
echo "CorteX Studio: http://127.0.0.1:5173"
echo "CorteX Worker: rodando em background (ingestao/transcricao/analise/selecao)"

(cd "$ROOT" && exec "$API") &
children+=("$!")

(cd "$ROOT" && exec "$ROOT/scripts/worker.sh") &
children+=("$!")

(cd "$ROOT/apps/web" && exec npm run dev -- --host 127.0.0.1) &
children+=("$!")

wait -n "${children[@]}"
