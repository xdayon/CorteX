# 2026-10-14 — NVENC indisponível no host (driver × FFmpeg)

## Fato
- GPU: GTX 1060 Max-Q (Pascal). Driver 580.178.04, a última série com suporte a
  Pascal; não há driver ≥ 590 para esta placa.
- Bazzite 44.20261002 trouxe `ffmpeg-8.1.3-1.fc44` (instalado em 02/out/2026),
  compilado para a API NVENC 13.1, que exige driver ≥ 610.
- Teste: `ffmpeg -f lavfi -i color=...:s=1280x720 -c:v h264_nvenc -f null -` →
  `Driver does not support the required nvenc API version. Required: 13.1 Found: 13.0`.
- CUDA para a transcrição (CTranslate2 + bibliotecas pip) não depende do FFmpeg.

## Decisão
- `render.encoder: libx264` no config (explícito, sem fallback silencioso).
- `RenderService` testa o NVENC uma vez por processo e recusa com mensagem clara
  quando `h264_nvenc` é pedido e não abre.
- A interface lê o encoder de `GET /api/v1/render-defaults`; nunca o fixa no código.

## Caminhos para voltar ao NVENC (decisão do usuário)
1. FFmpeg portátil compilado com cabeçalhos NVENC ≤ 13.0 e libass (ex.: build do
   Jellyfin), apontado em `render.ffmpeg`; validar com `scripts/check_gpu.sh`.
2. Fixar o pacote FFmpeg anterior na imagem do sistema (rpm-ostree), se disponível.
Medir antes: o x264 `veryfast` pode bastar para clipes de 40–120 s.
