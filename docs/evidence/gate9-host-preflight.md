# Gate 9 host preflight

Data: 2026-07-14

Este documento registra o preflight do hardware e a primeira cadeia longa real.
Nao e o relatorio final da matriz Gate 9 e nao substitui os cenarios restantes
local/curto, CPU, framings, lifecycle e comparacao before/after.

## Hardware

Comando executado no host:

```bash
./scripts/check_gpu.sh
```

Resultado:

- NVIDIA GeForce GTX 1060 with Max-Q Design;
- driver 580.159.04;
- 6144 MiB VRAM, compute capability 6.1;
- smoke H.264 NVENC 320x240, `yuv420p`, aprovado;
- CTranslate2 detectou 1 device CUDA;
- compute types: `float32`, `int8`, `int8_float32`.

## Suite completa

Comando executado no host, habilitando os testes que exigem browser real:

```bash
CORTEX_RUN_REMOTION_E2E=1 .venv/bin/pytest -q
```

Resultado inicial: `273 passed in 79.28s`. Apos o bootstrap CUDA, a correcao
blurred/punch-in, o trim pos-loudnorm e o schema 10, a suite foi repetida no
host: `278 passed in 90.18s`.

Os testes Remotion reais cobriram overlay alpha, headline, karaoke, composicao,
crossfade A/V, manifesto persistido e quality gate. Uma falha inicial do script
Node no sandbox revelou processo compositor orfao; `render-overlay.mjs` agora
encerra explicitamente em erro e a regressao portou o antigo teste de transicao
para `RenderService`.

## Verificacoes adicionais

- `cd apps/web && npm run build`: passou;
- `cd apps/remotion && npm run build`: passou;
- `.venv/bin/python -m compileall -q src tests scripts`: passou;
- `git diff --check`: passou.

## Cadeia real YouTube longa

Projeto persistido: `e195cb25e4f34de68bef3c2f08cef3cf`.

- origem oficial YouTube `smZSjkCxK9w`, asset
  `2dd96a85c8434db48c2a0e53e0d954c0`, 5901,03 s, 906800143 bytes e SHA-256
  `f52721f4597e5086fc908e96e67e76342b0f206080cc895d9d5b334f80d4724f`;
- transcript `2f787d38d1bd4ebb91d7e9f131e01c92`, 243 segmentos,
  `large-v3-turbo`, CUDA `int8` solicitada e efetiva, batch 8, sem fallback;
- repeticao identica reutilizou o mesmo transcript com `cached=true`;
- analise local `aec2b578577e4fce90f8b121ced7189e`, VAD Silero, waveform PCM e
  loudness EBU R128;
- sugestao `5079055743e34a8d9f1818396200361d`, 25 cortes validados por schema,
  Codex CLI 0.144.4, `gpt-5.5`, medium, 221119 ms, sem fallback;
- edit plan `3b53441d974a41d3b1477f80f5429993` para o corte rank 1;
- render publicavel `cd6204c5b55e4b77b440a5ea175b144f`, manifesto schema 10,
  `blurred_background`, captions, karaoke, headline, punch-in 1.15x e
  H.264 NVENC solicitado/efetivo;
- MP4 `render-295a21fdb9ed8e6b.mp4`, 112,83 s, 113763970 bytes e SHA-256
  `a7abf3355f9cfe81697c45bc186732542ccc7aa6c124ea2e5800b16159e42155`;
- quality gate: decode integral e faststart aprovados, sync A/V 0,003008 s,
  -13,8 LUFS, true peak -4,0 dBFS, nenhum issue, warning, black interval ou
  freeze interval.

Duas falhas reais foram preservadas nos jobs e corrigidas antes do sucesso:
o entrypoint direto do worker nao preparava cuBLAS/cuDNN, e a composicao
`blurred_background` + punch-in usava crop impossivel. O primeiro MP4 schema 9
ficou bloqueado por cauda do `loudnorm`; o schema 10 limita o audio a duracao do
video e reduziu o delta A/V de 0,066992 s para 0,003008 s.

## Pendencias para aceite Gate 9

- falta executar a matriz real declarada em `eval/gate9-matrix.example.json`;
- faltam medicoes consolidadas para fonte local/trecho curto, 1 e 10 cortes,
  caminho CPU, demais framings, recursos, lifecycle e comparacao
  editorial/tecnica;
- o dataset editorial ainda aguarda revisao humana.
