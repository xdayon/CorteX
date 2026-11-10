#!/usr/bin/env bash
# PreToolUse (Write|Edit|NotebookEdit): blocks writing a secret into any file.
# Reads the hook JSON on stdin; exit 2 blocks the tool call and shows stderr to the agent.
set -uo pipefail
input="$(cat)"
path="$(jq -r '.tool_input.file_path // .tool_input.notebook_path // ""' <<<"$input")"
text="$(jq -r '[.tool_input.content, .tool_input.new_string, .tool_input.new_source]
               | map(select(. != null)) | join("\n")' <<<"$input")"
[[ -z "$text" ]] && exit 0

# The real secrets file is never written by an agent.
case "$path" in
  */.env|*/.env.local|*/.env.production) echo "Bloqueado: agentes não escrevem em $path (segredos)." >&2; exit 2 ;;
esac

# Assignments of known secret variables with a literal value (placeholders like <...> pass).
if grep -Eq '(^|[^A-Z_])(HF_TOKEN|SOUNDZA_API_KEY|ANTHROPIC_API_KEY|OPENAI_API_KEY|TUNNEL_TOKEN|CLOUDFLARE_API_TOKEN)[[:space:]]*[=:][[:space:]]*["'"'"']?[A-Za-z0-9_\-]{12,}' <<<"$text"; then
  echo "Bloqueado: o conteúdo atribui um valor real a uma variável de segredo em $path. Use o nome da variável, nunca o valor." >&2
  exit 2
fi

# Bare tokens with a recognizable prefix (Hugging Face, Anthropic, OpenAI, GitHub).
if grep -Eq '(^|[^A-Za-z0-9_])(hf_[A-Za-z0-9]{30,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|gh[pousr]_[A-Za-z0-9]{30,})' <<<"$text"; then
  echo "Bloqueado: há um token (hf_/sk-/gh*_) no que seria escrito em $path." >&2
  exit 2
fi

gitleaks_bin="$(command -v gitleaks || echo "$HOME/.local/bin/gitleaks")"
if [[ -x "$gitleaks_bin" ]]; then
  if ! report="$("$gitleaks_bin" stdin --no-banner --redact -l error -f json -r - <<<"$text" 2>/dev/null)"; then
    # The generic rule also matches placeholders like <seu-token>: keep only real-looking values.
    rules="$(jq -r '[.[] | select(.RuleID != "generic-api-key" or ((.Entropy >= 4.2) and (.Secret | test("[<>]|seu|your|xxx|change") | not)))
                    | .RuleID] | unique | join(", ")' <<<"$report" 2>/dev/null)"
    [[ -z "$rules" ]] && exit 0
    echo "Bloqueado: o gitleaks achou um segredo ($rules) no que seria escrito em $path." >&2
    exit 2
  fi
fi
exit 0
