# 2026-11-02 — NVENC de volta com o FFmpeg portátil do Jellyfin

## Fato
- `jellyfin-ffmpeg_8.1.3-1_portable_linux64-gpl` em `~/.local/opt/jellyfin-ffmpeg/8.1.3-1/` (instalado
  pelo usuário). Mesma versão 8.1.3 do sistema, compilada com cabeçalhos NVENC que abrem no driver 580.
- Smoke test `-c:v h264_nvenc -preset p5` em 1080p: OK. O `/usr/bin/ffmpeg` continua sem NVENC
  (ver 2026-10-14-host-nvenc.md).
- Tem tudo o que o CorteX usa: libass/subtitles, xfade, minterpolate, loudnorm, ebur128, alimiter,
  noise, vignette, drawtext, libx264, h264/hevc_nvenc, aac, prores_ks, qtrle, png, libopus, mp3, vp9, flac.

## Medição (aula 2 da Polymath, 60 s de 1080p30, i7-8750H + GTX 1060 Max-Q)
| Etapa | libx264 | h264_nvenc | Ganho |
|---|---|---|---|
| Final com grão (`noise+vignette`) | 84,3 s (medium, crf 19) | 17,5 s (p5 hq, cq 19) | 4,8x |
| Só os filtros de grão (sem codificar) | 16,3 s | — | piso |
| Peça intermediária | 28,1 s (fast, crf 14) | 6,2 s (p5 hq, cq 14) | 4,5x |

SSIM contra a fonte com grão: x264 0,9595, NVENC 0,9592; recorte ampliado igual a olho. O
arquivo NVENC saiu menor (116 × 152 MB).

## Decisão
- `render.ffmpeg/ffprobe` apontam para o Jellyfin; `lessons.encoder: h264_nvenc` (peças e final).
- `render.encoder` dos cortes de podcast segue `libx264` até o usuário decidir (exige reiniciar o serviço).
- Sem fallback: se o NVENC não abrir, a aula falha com o motivo (`video_encode` em `lessons/common.py`).

## Prévia real da aula 2 (145 s), peças refeitas do zero com `h264_nvenc`
| Etapa | libx264 (17:33) | h264_nvenc (19:38) |
|---|---|---|
| 22 tomadas do zero | não medido (peças em cache) | 8 min 38 s |
| Emenda com dissolves | 1 min 35 s | 36 s |
| Final com grão | 3 min 56 s | 56 s |
Arquivo 386 → 293 MB, −13,9 LUFS nos dois. As tomadas continuam presas à CPU: quase toda tomada do
Flow é desacelerada 1,02–1,1x com `minterpolate` (mci), que não tem versão em GPU.

## Cortes do podcast (mesmo dia)
Trecho vertical de 60 s (fonte VP9 1080p → 1080×1920), SSIM saturado em 0,956 em todas as variantes:
| Variante | Tempo | Tamanho |
|---|---|---|
| libx264 veryfast crf 18 (export atual) | 24,5 s | 34 MB |
| h264_nvenc p4 cq 18 (args antigos, sem `-b:v 0`) | 7,6 s | 90 MB |
| h264_nvenc p5 hq cq 24 `-b:v 0` (escolhido) | 6,3 s | 51 MB |
| h264_nvenc p5 hq cq 26 `-b:v 0` | 6,3 s | 40 MB |
Recorte ampliado do rosto: x264, cq 24 e cq 26 iguais a olho. `render.encoder: h264_nvenc`.

Rejeitados, medidos:
- **Prévia rápida do editor (360×640):** x264 ultrafast 10,9/15,9 s × NVENC p1 9,1/16,1 s. O custo é decodificar a
  fonte; fica na CPU.
- **NVDEC (`-hwaccel cuda`) na análise:** VP9 1080p +15–25 %, VP9 4K +30 %, e AV1 1440p falha (Pascal não decodifica
  AV1; levou 283 s até falhar). A análise extrai quadros soltos, um processo por quadro: o contexto CUDA custaria mais
  do que ganha. Não usar.
