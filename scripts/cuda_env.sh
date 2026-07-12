#!/usr/bin/env bash

# CTranslate2 carrega cuBLAS/cuDNN via dlopen. Os wheels NVIDIA mantêm essas
# bibliotecas dentro do venv, então o caminho precisa existir antes do Python.
CORTEX_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cuda_libs="$(find "$CORTEX_ROOT/.venv"/lib* \
  -path '*/site-packages/nvidia/*/lib' -type d 2>/dev/null | paste -sd: -)"

if [[ -n "$cuda_libs" ]]; then
  export LD_LIBRARY_PATH="$cuda_libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

unset cuda_libs CORTEX_ROOT
