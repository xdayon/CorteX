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

### 3. Legendas menos frenéticas + agrupamento inteligente
- `config/caption_styles.py` — `words_per_chunk` do estilo `branded` de 3 → 5.
- `services/caption_renderer.py` — no estilo `branded`:
  - `_group_words_smart()`: agrupa os chunks quebrando em fim de frase (`. ! ? …`) e
    **nunca deixa uma palavra sozinha** no último chunk (o clássico "é"/"a" solto).
  - `_split_balanced()`: divide o chunk em duas linhas pela **largura equilibrada**
    (minimiza a linha mais larga e a diferença entre elas) em vez do preenchimento
    guloso, que jogava uma palavrinha sozinha na segunda linha.

### 4. Render na GPU (NVENC)
O `.env` aponta `PODCLI_FFMPEG=/usr/bin/ffmpeg` (o ffmpeg do sistema tem `h264_nvenc`;
o embutido em `runtime/ffmpeg` é **CPU-only**). Mas o launcher `podcli` **pré-injeta**
`PODCLI_FFMPEG` apontando pro ffmpeg embutido, e o `python-dotenv` por padrão **não
sobrescreve** variáveis já presentes no ambiente — então o `.env` era ignorado e todo
o export caía no `libx264` (CPU).

- `cli.py` — o carregamento do `.env` passou a usar `load_dotenv(..., override=True)`
  (e o fallback manual força `PODCLI_FFMPEG`/`PODCLI_FFPROBE`). Assim o `.env` vence o
  valor pré-injetado pelo launcher, o `encoder.py` detecta `h264_nvenc` e o render
  volta pra GPU. Ver `.env.example`.

> Se ainda aparecer `libx264`, apague os caches de detecção
> (`data/cache/encoder.json` e `runtime/data/cache/encoder.json`) e reinicie o podcli.

### 5. Render 100% na GPU (NVENC em todos os passes + decode NVDEC)
A rodada anterior só pegava o encode dos passes que já usavam `get_video_encode_flags()`.
Vários caminhos ainda tinham `libx264` **hardcoded** — inclusive o encode final do fluxo
padrão de legendas (composite do Remotion). Agora:

- `services/encoder.py` — flags NVENC com `-rc-lookahead 20 -spatial-aq 1
  -temporal-aq 1 -bf 3` (qualidade visivelmente melhor no mesmo `-cq`, a 1060 suporta
  tudo); nova `get_video_decode_flags()` → `-hwaccel cuda` (decode NVDEC) quando NVENC
  está ativo; fingerprint do cache virou `v2:` para invalidar caches antigos sozinho.
- `services/video_cut.py` — `cut_segment`/`cut_multi_segment` (o **primeiro passe de
  todo corte**) saíram do libx264 hardcoded para NVENC com fallback + NVDEC.
- `services/video_processor.py` — crop dinâmico com tracking de speaker, split-screen
  e as cadeias de xfade saíram do libx264 hardcoded; decode NVDEC injetado nos passes
  de reframe.
- `services/reel.py` — modo reel (era 100% CPU) para NVENC com fallback.
- `services/clip_generator.py` — passe de suavização de transição (gblur) para NVENC.
- `runtime/remotion/render.mjs` — o composite final (overlay ProRes das legendas sobre
  o vídeo) usava `ffmpeg` do PATH com `libx264` hardcoded; agora usa `PODCLI_FFMPEG`,
  tenta `h264_nvenc` + `-hwaccel cuda` e cai para libx264 se falhar.

Todo passe usa `run_ffmpeg_with_fallback`: se o NVENC falhar, cai para libx264 em vez
de quebrar. O overlay ProRes continua em decode de software (NVDEC não decodifica
ProRes) — só o vídeo principal usa NVDEC.

### 6. Transcrição em batch (~3-4x mais rápida)
`fasterwhisper_worker.py` agora usa o `BatchedInferencePipeline` do faster-whisper:
o VAD separa os trechos de fala e a GPU decodifica vários em paralelo.
`PODCLI_FASTERWHISPER_BATCH=8` cabe nos 6 GB da 1060 com `large-v3-turbo` int8;
`0` desliga. Se der OOM ou qualquer erro, cai sozinho para o decode sequencial
(a materialização dos segments acontece dentro do try — o `transcribe()` é lazy e
um OOM só estoura na iteração).

### 7. Legenda `.srt` junto de cada corte
`generate_clip` agora grava um `.srt` ao lado do `.mp4` final (mesmas palavras já
limpas de fillers que aparecem queimadas no vídeo, timestamps relativos ao corte).
Reels/Shorts/TikTok indexam melhor com legenda nativa, e serve de acessibilidade.
A chave `subtitle_path` vem no dict de retorno.

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
