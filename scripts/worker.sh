#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=./cuda_env.sh
source "$ROOT/scripts/cuda_env.sh"

exec "$ROOT/.venv/bin/python" -m cortex.worker "$@"
