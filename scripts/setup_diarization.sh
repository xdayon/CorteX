#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Separate environment preserves the existing CTranslate2/CUDA stack.
uv venv --python 3.13 --seed "$ROOT/.venv-diarization"
"$ROOT/.venv-diarization/bin/python" -m pip install --upgrade pip
"$ROOT/.venv-diarization/bin/python" -m pip install torch==2.9.0 torchaudio==2.9.0 --index-url https://download.pytorch.org/whl/cpu
"$ROOT/.venv-diarization/bin/python" -m pip install pyannote.audio==4.0.6 torchcodec==0.8.1
"$ROOT/.venv-diarization/bin/python" -m pip check
