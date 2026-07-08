#!/usr/bin/env bash
# Reaplica os mods deste repo por cima da instalação do podcli.
# Copia mods/ -> $PODCLI_HOME espelhando os caminhos, com backup .bak de cada
# arquivo que for sobrescrito.
set -euo pipefail

PODCLI_HOME="${PODCLI_HOME:-$HOME/.local/share/podcli}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODS_DIR="$REPO_DIR/mods"

if [[ ! -d "$PODCLI_HOME" ]]; then
  echo "ERRO: podcli não encontrado em $PODCLI_HOME" >&2
  echo "      defina PODCLI_HOME=/caminho/do/podcli e rode de novo." >&2
  exit 1
fi

echo ">> Aplicando mods em $PODCLI_HOME"
count=0
while IFS= read -r -d '' src; do
  rel="${src#"$MODS_DIR"/}"
  dst="$PODCLI_HOME/$rel"
  mkdir -p "$(dirname "$dst")"
  if [[ -f "$dst" ]] && ! cmp -s "$src" "$dst"; then
    cp -a "$dst" "$dst.bak"
    echo "   backup: $rel -> $rel.bak"
  fi
  cp -a "$src" "$dst"
  echo "   ok: $rel"
  count=$((count + 1))
done < <(find "$MODS_DIR" -type f -print0)

echo ">> $count arquivo(s) aplicado(s)."
echo ">> Lembre: copie .env.example -> $PODCLI_HOME/.env, preencha e REINICIE o podcli."
