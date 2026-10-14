#!/bin/sh
# Atualiza dependências, verifica FFmpeg/libass, roda testes, recompila a UI e reinicia o serviço.
set -u
cd "$(dirname "$0")/.." || exit 1
echo "== FFmpeg (precisa listar 'subtitles') =="
/usr/bin/ffmpeg -hide_banner -filters | grep -E " (ass|subtitles) "
echo "== Dependências Python =="
.venv/bin/python -m pip install -q -e '.[dev,transcription,analysis,youtube]' && echo DEPS_OK
echo "== Testes backend =="
.venv/bin/python -m pytest -q 2>&1 | tail -15
echo "== Interface web =="
(cd apps/web && npm test 2>&1 | tail -5 && npm run build 2>&1 | tail -3)
echo "== Reiniciando serviço =="
systemctl --user restart cortex.service && systemctl --user is-active cortex.service
echo "== FIM =="
