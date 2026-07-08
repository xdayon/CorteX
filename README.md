# CorteX — mods do podcli (transcrição + render na GPU)

Repositório que versiona **todas as modificações** feitas por cima do
[podcli](https://github.com/nmbrthirteen/podcli) nesta máquina, para gerar cortes
de reels com transcrição e legendas em **pt-br**, usando a **GTX 1060** tanto na
transcrição quanto no render.

O podcli em si **não** vive aqui — ele fica instalado em
`~/.local/share/podcli`. Este repo guarda só os arquivos que a gente mexeu
(espelhando os caminhos originais) mais os scripts para reaplicar tudo. Assim os
commits ficam salvos e dá pra reproduzir o setup se o podcli for reinstalado.

## O que foi mudado

### 1. Engine de transcrição na GPU (faster-whisper / CTranslate2)
O install nativo do podcli só traz **whisper.cpp (CPU)**. Adicionamos um engine
novo, `fasterwhisper`, que roda o modelo na GPU (CUDA, `int8` — a 1060 é Pascal
`sm_61`, então `int8` rende melhor que `float16`).

Para não contaminar o python do runtime do podcli com torch/ctranslate2, o modelo
roda num **venv separado** (`~/Projects/transcription/venv`) via subprocess:

- `mods/runtime/backend/services/fasterwhisper_worker.py` — roda **dentro** do
  venv CUDA; só importa `faster_whisper` + stdlib; emite JSON no stdout no formato
  que o podcli espera (segments + timings por palavra).
- `mods/runtime/backend/services/transcription_fasterwhisper.py` — adapter que
  roda no python do podcli; extrai o wav 16k, injeta `LD_LIBRARY_PATH` com as libs
  CUDA do venv (`nvidia/*/lib`) e chama o worker. A injeção do `LD_LIBRARY_PATH`
  **precisa** ser no exec do subprocess — o CTranslate2 faz `dlopen()` de
  libcublas/libcudnn em runtime.
- `services/engines.py` — `normalize_engine()` reconhece `fasterwhisper`/`gpu`.
- `services/transcription.py` — roteamento: quando `PODCLI_ENGINE=fasterwhisper`,
  usa o adapter; se o venv/worker sumir, cai pro whisper.cpp em vez de quebrar.
  Diarização é pulada (sem torch aqui), face analysis (OpenCV) continua.

### 2. Modelo padrão large-v3-turbo / large-v3
- `services/transcription_whispercpp.py` — preset de alinhamento **DTW** escolhido
  pelo modelo (`_dtw_preset_for_model`); o turbo tem 4 camadas de decoder, então
  precisa de `large.v3.turbo`, senão o whisper.cpp quebra.
- `cli.py` — default do `whisper_model` passou pra `large-v3-turbo`.
- `studio/public/assets/index-*.js` e `studio/mcp-server.mjs` — opção
  **Large v3 Turbo** adicionada e marcada como default na UI.
- `presets/Neat.json`, `presets/Neat90.json` — modelo atualizado.

### 3. Legendas menos frenéticas
- `config/caption_styles.py` — `words_per_chunk` do estilo `branded` de 3 → 5.

### 4. Render na GPU (NVENC)
Não é mudança de código: o `.env` aponta `PODCLI_FFMPEG=/usr/bin/ffmpeg` (o ffmpeg
do sistema tem `h264_nvenc`; o embutido é CPU-only). Com isso o `encoder.py`
detecta e usa NVENC. Ver `.env.example`.

## Como aplicar

```bash
./apply.sh            # copia mods/ para ~/.local/share/podcli (faz backup .bak)
cp .env.example ~/.local/share/podcli/.env   # e edite: HF_TOKEN + paths
```

Depois **reinicie o podcli** para carregar o `.env` (encoder passa de "CPU" pra
"nvenc" e o engine novo entra em uso).

## Referência
`reference/` guarda os scripts originais que provaram a transcrição na GPU
(`transcribe.py`, `run_transcribe.sh`), de onde veio a receita de `LD_LIBRARY_PATH`.

## Diarização (depois)
Identificação de quem fala (pyannote) fica pra uma fase 2 — roda na 1060, mas puxa
torch e precisa de mais integração. Hoje está desligada de propósito.
