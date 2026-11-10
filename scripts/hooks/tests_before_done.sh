#!/usr/bin/env bash
# Stop hook: "done" requires green checks for whatever code changed since the last green run.
# Backend changes → ruff + pytest; Studio changes → tsc + vitest. A failure blocks the stop once
# (the agent sees the failure and keeps working); a second stop in a row is allowed with a warning
# so a broken environment can never trap the session.
set -uo pipefail
input="$(cat)"
root="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
cd "$root" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

changed="$(git status --porcelain --untracked-files=all | awk '{print $NF}')"
backend="$(grep -E '^(src/|tests/|scripts/.*\.py$|prompts/|config/|pyproject\.toml$)' <<<"$changed" || true)"
web="$(grep -E '^apps/web/(src/|package\.json$|vite\.config\.ts$|tsconfig)' <<<"$changed" || true)"
[[ -z "$backend$web" ]] && exit 0

# Skip when exactly this state already passed.
stamp_file="$(git rev-parse --git-dir)/claude-checks-ok"
state="$( { git rev-parse HEAD; git diff HEAD; git ls-files --others --exclude-standard -z | xargs -0 -r sha256sum; } | sha256sum | cut -c1-64)"
[[ -f "$stamp_file" && "$(cat "$stamp_file")" == "$state" ]] && exit 0

log="$(mktemp)"; trap 'rm -f "$log"' EXIT
ok=1
if [[ -n "$backend" ]]; then
  .venv/bin/ruff check src/cortex scripts tests >>"$log" 2>&1 || ok=0
  [[ $ok == 1 ]] && { timeout 540 .venv/bin/pytest -q -x -p no:cacheprovider >>"$log" 2>&1 || ok=0; }
fi
if [[ $ok == 1 && -n "$web" ]]; then
  (cd apps/web && npx tsc --noEmit -p tsconfig.json && npx vitest run) >>"$log" 2>&1 || ok=0
fi

if [[ $ok == 1 ]]; then
  echo "$state" >"$stamp_file"
  exit 0
fi
summary="$(grep -E 'FAILED|ERROR|error|✗|×|Error:' "$log" | tail -15)"
if [[ "$(jq -r '.stop_hook_active // false' <<<"$input")" == "true" ]]; then
  jq -n --arg m "Verificações ainda falhando ao encerrar: $summary" '{systemMessage: $m}'
  exit 0
fi
jq -n --arg r "As verificações falharam; corrija antes de dizer que terminou (ou explique por que não dá):
$summary" '{decision: "block", reason: $r}'
exit 0
