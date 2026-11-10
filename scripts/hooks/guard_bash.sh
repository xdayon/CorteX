#!/usr/bin/env bash
# PreToolUse (Bash): the project's never-break rules for shell commands (AGENTS.md).
# Deny rules in settings.json match by prefix only; this also catches chained commands.
set -uo pipefail
raw="$(jq -r '.tool_input.command // ""')"
# Heredoc bodies are data (file contents, scripts' text), not commands: drop them before matching.
cmd="$(awk '
  inbody { if ($0 == term) inbody = 0; next }
  { print }
  match($0, /<<-?[[:space:]]*["'"'"']?[A-Za-z_][A-Za-z0-9_]*["'"'"']?/) {
    term = substr($0, RSTART, RLENGTH); gsub(/^<<-?[[:space:]]*["'"'"']?|["'"'"']$/, "", term); inbody = 1
  }' <<<"$raw")"
block() { echo "Bloqueado pela regra do projeto: $1" >&2; exit 2; }

# Secrets: never print, source or stage the real .env (the .env.example template is fine).
env_file='(^|[[:space:]/"'"'"'])\.env([[:space:]"'"'"';|&)]|$)'
if grep -Eq "(cat|less|more|head|tail|bat|grep|rg|sed|awk|strings|xxd|od|source|\.|cp|scp|base64)[^|;&]*${env_file}" <<<"$cmd"; then
  block "não ler nem copiar o .env (segredos)."
fi
grep -Eq 'git[[:space:]]+add[^|;&]*(-f|--force)[^|;&]*(\.env|data/)' <<<"$cmd" && block "não versionar .env nem data/."
grep -Eq 'git[[:space:]]+add[^|;&]*([[:space:]]|^)(\./)?(\.env|data)(/|[[:space:]]|$)' <<<"$cmd" && block "não versionar .env nem data/."

# User data: media, SQLite, lessons and the ElevenLabs/Flow inbox are never deleted by an agent.
# Protected: the project's data/ and entrada/ (relative, ./, or under the CorteX checkout), the
# SQLite and Claude's own config. A different folder that happens to be called data is not.
if grep -Eq '(^|[;&|[:space:]])(rm|shred|truncate|find[^|;&]*-delete)([[:space:]]|$)[^|;&]*([[:space:]"'"'"'](\./)?(data|entrada)(/|[[:space:]"'"'"']|$)|CorteX/(data|entrada)(/|[[:space:]"'"'"']|$)|cortex\.sqlite3|\.claude)' <<<"$cmd"; then
  block "não apagar data/, entrada/, o banco SQLite nem a configuração do Claude."
fi
# find names the folder before -delete/-exec rm.
if grep -Eq '(^|[;&|[:space:]])find[^|;&]*((^|[[:space:]"'"'"'])(\./)?(data|entrada)(/|[[:space:]"'"'"']|$)|CorteX/(data|entrada)(/|[[:space:]"'"'"']|$))[^|;&]*(-delete|-exec[[:space:]]+rm)' <<<"$cmd"; then
  block "não apagar data/ nem entrada/."
fi

# Git history and uncommitted work.
grep -Eq 'git[[:space:]]+push[^|;&]*(--force([^-]|$)|-f([[:space:]]|$))' <<<"$cmd" && block "sem push --force (use --force-with-lease só com pedido explícito)."
grep -Eq 'git[[:space:]]+(reset[[:space:]]+--hard|clean[[:space:]]+-[a-zA-Z]*f|checkout[[:space:]]+--[[:space:]]|restore[[:space:]]+(--worktree[[:space:]]+)?\.)' <<<"$cmd" \
  && block "não descartar trabalho não commitado (reset --hard, clean -f, checkout --, restore .)."
grep -Eq 'git[[:space:]]+stash[[:space:]]*($|[;&|]|pop|drop|clear)' <<<"$cmd" && block "não usar stash sem rótulo (outras sessões compartilham a pilha)."

# The live service: only the user restarts it; agents never build into the served dist/.
grep -Eq 'systemctl[^|;&]*--user[^|;&]*(restart|stop|start|kill|try-restart)[^|;&]*cortex' <<<"$cmd" && block "agentes não ligam, desligam nem reiniciam o cortex.service; peça ao usuário."
# Command position only (start, or after ; & | ( or a newline), so searching for the phrase is fine.
at_cmd='(^|[;&|(])[[:space:]]*'
grep -Eq "${at_cmd}npm[[:space:]]+run[[:space:]]+build" <<<"$cmd" && block "npm run build grava apps/web/dist, servida ao vivo; use npx vite build --outDir <rascunho> --emptyOutDir."
grep -Eq "${at_cmd}(npx[[:space:]]+)?vite[[:space:]]+build" <<<"$cmd" && ! grep -Eq -- '--outDir' <<<"$cmd" && block "vite build sem --outDir grava apps/web/dist, servida ao vivo."
exit 0
