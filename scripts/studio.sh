#!/usr/bin/env bash
set -euo pipefail

CORTEX_STUDIO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CORTEX_STUDIO_ROOT"
source "$CORTEX_STUDIO_ROOT/scripts/cuda_env.sh"

if [[ ! -f apps/web/dist/index.html || ! -x .venv/bin/cortex-api ]]; then
  echo "Compile o Studio e instale o backend conforme README.md antes de iniciar." >&2
  exit 1
fi

# The origin stays local. A future authenticated tunnel can reach this port.
export CORTEX__APP__HOST=127.0.0.1
export CORTEX__APP__STUDIO_DIR="$CORTEX_STUDIO_ROOT/apps/web/dist"

# Let the desktop launcher discover the effective port, including .env overrides
# loaded by systemd. This contains no credentials and is never served by the API.
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from cortex.config import load_config
endpoint = Path('.cache/studio-endpoint.json')
endpoint.parent.mkdir(exist_ok=True)
temporary = endpoint.with_suffix('.tmp')
temporary.write_text(json.dumps({'port': load_config().app.port}))
temporary.replace(endpoint)
PY

children=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${children[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

.venv/bin/cortex-api &
children+=("$!")
./scripts/worker.sh &
children+=("$!")
wait -n "${children[@]}"
