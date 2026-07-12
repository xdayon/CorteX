#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=./cuda_env.sh
source "$ROOT/scripts/cuda_env.sh"

tmp="$(mktemp --suffix=.mp4 cortex-nvenc-XXXXXX)"
trap 'rm -f "$tmp"' EXIT

echo "== NVIDIA =="
nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap \
  --format=csv,noheader

echo
echo "== NVENC smoke test =="
ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i color=black:s=320x240:d=1:r=30 \
  -c:v h264_nvenc -preset p5 -cq 23 -pix_fmt yuv420p "$tmp"
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,width,height,pix_fmt \
  -of default=noprint_wrappers=1 "$tmp"

echo
echo "GPU e H.264 NVENC funcionais para o CorteX."

echo
echo "== CTranslate2 =="
"$ROOT/.venv/bin/python" - <<'PY'
import ctranslate2

devices = ctranslate2.get_cuda_device_count()
if devices < 1:
    raise SystemExit("CTranslate2 nao encontrou a GPU CUDA")
compute_types = sorted(ctranslate2.get_supported_compute_types("cuda", 0))
if "int8" not in compute_types:
    raise SystemExit(f"CUDA sem int8: {compute_types}")
print(f"cuda_devices={devices}")
print(f"compute_types={','.join(compute_types)}")
PY
