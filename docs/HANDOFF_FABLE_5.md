# Handoff para Fable-5 - CorteX

Data: 2026-07-11
Repositorio: `/var/home/dx/Projects/CorteX`
Branch atual no momento do handoff: `feature/audio-aware-editing`

## Instrucao inicial ao novo orquestrador

Leia integralmente, nesta ordem:

1. `docs/HANDOFF_FABLE_5.md`
2. `README.md`
3. `docs/ARCHITECTURE.md`
4. `docs/EDITING_ENGINE.md`
5. `docs/ROADMAP.md`
6. `config/cortex.yaml`
7. `presets/neat90.yaml`
8. `prompts/clip_selection.pt-BR.md`
9. `prompts/clip_selection.schema.json`

Depois, audite `git status`, rode os testes e revise o codigo atual antes de
editar. Nao presuma que arquivos nao commitados sao descartaveis. Nao reverta
mudancas do usuario nem tente restaurar o frontend do PodCLI.

## Decisao de produto

O produto se chama **CorteX**, da empresa **HiTechX**, e deve viver integralmente
neste repositorio. A proposta antiga de criar outro produto/repo chamado Connex
foi substituida pela decisao atual do usuario. `CONNEX_BRIEF.md` e contexto
historico, nao uma ordem para criar ou editar `/Projects/Connex`.

Objetivo: ferramenta local propria para gerar cortes profissionais de podcasts
para Instagram Reels e TikTok, normalmente entre 50 segundos e 2 minutos, com:

- arquivo local ou link do YouTube;
- faster-whisper GPU com timestamps por palavra e VAD;
- selecao de cortes por IA com prompt editavel;
- revisao humana antes de renderizar;
- cortes conscientes de fala, waveform e semantica;
- pausas retoricas, jump cut, punch-in, J-cut e L-cut reais;
- diversidade de cameras e reaction shots seguros do entrevistador;
- legendas animadas/karaoke totalmente configuraveis;
- headline editorial separada da legenda;
- presets salvos, incluindo Neat90;
- render GPU-first e quality gates;
- progresso real e telemetria CPU/GPU no frontend.

Selecionar arquivo/modelo **nunca** inicia transcricao. Existem botoes separados
para iniciar transcricao, analise e render.

## Hardware validado

Notebook: Dell G7 7588.

- GPU: NVIDIA GeForce GTX 1060 with Max-Q Design
- VRAM: 6144 MiB
- Compute capability: 6.1 (Pascal)
- Driver observado: 580.159.04
- CPU exposta: 6 cores fisicos / 12 logicos
- FFmpeg observado: 8.1.1
- Python atual: 3.14.5
- Node atual: 25.9.0

Validacoes executadas no host, fora do sandbox:

- `nvidia-smi` detectou a GPU;
- H.264 NVENC produziu MP4 valido;
- CTranslate2 detectou 1 device CUDA;
- compute types CUDA suportados: `int8`, `int8_float32`, `float32`;
- `large-v3-turbo` carregou e realizou inferencia curta em CUDA `int8`;
- cuBLAS/cuDNN foram instalados no `.venv` do CorteX.

Decisoes de compatibilidade:

- usar `int8` como default na GTX 1060; nao usar `float16`/`int8_float16`;
- `large-v3-turbo`, batch inicial 8;
- para `large-v3`, comecar batch 4 e medir VRAM;
- H.264/H.265 NVENC sao validos;
- nao oferecer AV1 NVENC para esta GPU, mesmo que outro encoder do sistema liste
  AV1 por causa de outro backend/hardware;
- fallback CUDA -> CPU deve ser explicito, nunca silencioso;
- registrar sempre engine solicitada e engine efetiva.

Diagnostico reproduzivel:

```bash
./scripts/check_gpu.sh
```

`scripts/cuda_env.sh` injeta no `LD_LIBRARY_PATH` as bibliotecas NVIDIA instaladas
dentro do venv antes do Python iniciar. Isso e necessario porque CTranslate2
carrega cuBLAS/cuDNN via `dlopen`.

## Estado implementado

### Backend standalone e P0 concluido

Arquivos principais em `src/cortex/`:

- `config.py`: Pydantic + YAML + overrides `CORTEX__SECAO__CHAVE`;
- `schemas.py`: tipos de jobs, estados, stages e telemetria;
- `jobs.py`: SQLite WAL, persistencia, progresso monotonico e transicoes;
- `hardware.py`: psutil + NVML, GPU/VRAM/temperatura/NVENC/NVDEC;
- `api.py`: FastAPI, CORS, health, hardware, jobs, cancelamento e SSE.
- `domain/`: Project, SourceAsset, TranscriptArtifact, StageArtifact e store;
- `ingest/`: upload streaming, filenames seguros, ffprobe e YouTube/yt-dlp;
- `transcribe/`: faster-whisper model warm, CUDA fail-hard, cache e schemas;
- `worker.py`: claim atomico, cancelamento e handlers locais.

Endpoints atuais:

- `GET /api/v1/health`
- `GET /api/v1/hardware`
- `POST /api/v1/jobs`
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/{id}`
- `POST /api/v1/jobs/{id}/cancel`
- `GET /api/v1/jobs/{id}/events`
- `POST /api/v1/projects`
- `POST /api/v1/projects/{id}/sources/upload`
- `POST /api/v1/projects/{id}/sources/youtube`
- `POST /api/v1/projects/{id}/transcribe`

Ingestao YouTube e transcricao possuem handlers reais. Selecao e render ainda
nao possuem handlers conectados.

### Frontend proprio

`apps/web` contem React + TypeScript + Vite e nao reutiliza o bundle minificado
do PodCLI. O build de producao passou.

UX implementada:

- wizard de 6 etapas: Fonte, Transcricao, Brief IA, Curadoria, Estudio, Render;
- arquivo/YouTube;
- botoes explicitos de inicio;
- ranges de 1-25 cortes e 15-180 segundos;
- curadoria com waveform/VAD/estrutura narrativa;
- preview Limpo/TikTok/Reels/safe zones sem asset externo;
- controles de legenda, headline, edicao e output;
- Compute Deck ligado ao endpoint real `/api/v1/hardware`;
- standby explicito sem telemetria, sem progresso/GPU falsos.

O frontend cria projeto, envia o arquivo real via XHR, acompanha upload/job via
SSE e avanca ao Brief quando a transcricao termina. O transcript ainda nao possui
uma tela real de revisao e a waveform da curadoria ainda e ilustrativa.

### Preset, prompts e motor editorial

- `presets/neat90.yaml`: preset autocontido com transcricao, selecao, waveform,
  VAD, captions, karaoke, headline, camera, NVENC e loudness.
- Headline `Editorial Quote`: baseada em `headline model.jpeg`, com selo turquesa,
  aspas brancas, Montserrat 900 preta e duas tarjas brancas.
- `prompts/clip_selection.pt-BR.md`: hook -> contexto -> payoff, foco tematico,
  pausas, EDL aproximada e scoring.
- `prompts/clip_selection.schema.json`: JSON Schema Draft 2020-12.
- `docs/EDITING_ENGINE.md`: contrato da EDL com timelines A/V separadas, J/L-cut,
  reaction safety, camera planner, FFmpeg/Remotion e QA.

### Ativos legados reutilizaveis

`mods/` ainda e a implementacao antiga aplicada sobre PodCLI. Nao e arquitetura
alvo. Portar apenas logica comprovada, removendo imports/caminhos PodCLI.

Ativos bons:

- `audio_editing.py`: RMS/waveform + VAD + palavras, perfis e boundary snapping;
- `edit_quality.py`: gates de plano/render;
- `video_cut.py`: crossfade existente (nao confundir com J/L-cut real);
- `hook_overlay.py`: referencia de headline ASS;
- `fasterwhisper_worker.py`: batch, word timing e VAD;
- partes selecionadas de `clip_generator.py`, `video_processor.py`,
  `caption_renderer.py` e `encoder.py`;
- testes legados de waveform e FFmpeg.

Nao copiar cegamente: varios imports/dependencias nao existem no repo, ha nomes
`PODCLI_*`, paths absolutos e codigo-fonte Remotion ausente. O frontend antigo e
bundle minificado e deve ser abandonado.

## Ambiente instalado

Python:

```bash
.venv/bin/python -m pip install -e '.[dev,transcription,youtube]'
```

Instalado e validado:

- FastAPI/Uvicorn/Pydantic/psutil/NVML;
- faster-whisper 1.2.1;
- CTranslate2 4.8.1;
- nvidia-cublas-cu12 12.9.2.10;
- nvidia-cudnn-cu12 9.23.2.1;
- nvidia-cuda-nvrtc-cu12 12.9.86;
- yt-dlp;
- deps de testes.

Frontend: `apps/web/node_modules` instalado, `package-lock.json` criado e
`npm run build` validado.

Subir tudo:

```bash
./scripts/dev.sh
```

URLs:

- Studio: `http://127.0.0.1:5173`
- Swagger: `http://127.0.0.1:8787/docs`

No encerramento desta sessao, os servidores foram iniciados e validados. Nao
presuma que continuarao ativos numa nova sessao; teste as portas e rode o script
se necessario.

## Validacoes que passaram

```bash
.venv/bin/pytest -q
cd apps/web && npm run build
./scripts/check_gpu.sh
```

- 45 testes passaram na entrega P0;
- TypeScript estrito + Vite build passaram;
- JSON Schema/YAML e `git diff --check` passaram;
- API health/hardware/jobs responderam;
- frontend retornou HTTP 200;
- NVENC e inferencia faster-whisper CUDA passaram no host.

O sandbox pode ocultar a GPU e bloquear bind de portas. Para testes de
`nvidia-smi`, NVENC, CTranslate2 ou servidores locais, solicitar execucao no host
com permissao elevada; nao interpretar falha no sandbox como falha do hardware.

## Proxima tarefa P1 - Analysis Artifact e revisao real

Antes de chamar a LLM, transformar audio+transcript em um pacote deterministico,
local, cacheavel e revisavel. O transcript atual possui palavras/segmentos, mas a
timeline bruta do Silero VAD nao e persistida e a waveform do Studio e estatica.

Escopo recomendado:

1. Criar `AnalysisArtifact`/schema versionado, hash de inputs e cache.
2. Persistir intervalos reais do Silero VAD no transcript ou analysis artifact.
3. Calcular uma vez waveform peaks e RMS em resolucoes adequadas para UI/edicao.
4. Calcular pausas, speech density, loudness, true peak e amostras de room tone.
5. Nao duplicar decode: reutilizar o WAV normalizado cacheado pelo P0.
6. Criar job `analysis` e handler no worker com progresso/cancelamento reais.
7. Expor endpoints de transcript/analysis por projeto e artifact id.
8. Substituir waveform/textos mockados da Curadoria por dados reais.
9. Adicionar editor/revisao do transcript sem alterar o artifact original:
   correcoes devem virar revision/override auditavel.
10. Portar o boundary optimizer legado para um pacote proprio, sem imports PodCLI.
11. Testar snapping em toda fronteira, inclusive planos multi-segmento.
12. Gate: episodio real mostra transcript + waveform + VAD sincronizados e um
    preview de fronteira reproduzivel antes de qualquer chamada de LLM.

Arquitetura sugerida para esta fase:

```text
src/cortex/analyze/         waveform, VAD, loudness, pauses, schemas
src/cortex/edit/            boundary resolver e EDL (porta o legado validado)
data/projects/*/analysis/   artifacts versionados ignorados pelo Git
```

Depois desse gate, integrar uma LLM headless como job separado, consumindo apenas
artifacts persistidos e emitindo JSON validado por schema. A indisponibilidade de
rede nunca deve invalidar transcript/analysis locais.

## Atualizacao 2026-07-12 - EDL na Curadoria

A Curadoria passou a consumir o `EditPlanDocument` persistido pelo backend:

- a criacao do plano continua explicita, por corte ou ao preparar os selecionados;
- o frontend acompanha o job `edit_plan`, busca o artifact pelo endpoint GET e
  guarda o documento associado ao corte;
- a timeline mostra segmentos resolvidos, overlaps de crossfade, duracao final,
  segundos removidos e quantidade de jump cuts;
- issues do quality gate, inclusive `boundary_snapped` e degradacao fail-safe,
  aparecem na revisao antes do Estudio;
- troca de fonte invalida selecao e EDLs mantidas em memoria;
- o build TypeScript/Vite e Ruff passaram. Os seis testes unitarios de
  `tests/test_edit_plan.py` passaram; o teste de integracao API/worker ficou
  bloqueado no teardown dentro do sandbox com os servicos de desenvolvimento
  ativos e deve ser repetido no host junto da suite completa.

Proximo gate: render FFmpeg curto real consumindo uma EDL multi-segmento, com
crossfades de audio/video e artifact de saida persistido. Nao ligar o botao de
render a dados simulados.

## Atualizacao 2026-07-12 - render curto multi-segmento

O primeiro renderer proprio foi conectado em `src/cortex/render/`:

- consome somente `EditPlanArtifact` aprovado e a fonte persistida;
- aplica trims, scale/pad, `xfade` e `acrossfade` encadeados em uma unica
  passagem FFmpeg para um ou varios segmentos;
- aceita `h264_nvenc` ou `libx264` de forma explicita, sem fallback silencioso;
- registra encoder solicitado/efetivo, dimensoes, FPS, hashes e quality gate;
- valida streams de audio/video e tolerancia de duracao antes de publicar o MP4;
- persiste MP4, manifesto `render` e cache por hash de fonte+EDL+configuracao;
- expoe `POST /projects/{id}/renders` e `GET /projects/{id}/renders/{artifact_id}`;
- `JobType.RENDER` possui handler real, progresso, cancelamento e erro persistido;
- o frontend envia os `EditPlanArtifact` selecionados para uma fila real e nao
  cria mais o job de render com ids simulados.

Validacao automatizada: fonte sintetica de quatro segundos, dois segmentos nao
contiguos e crossfade de 60 ms produziram MP4 `libx264` com audio+video e duracao
de aproximadamente 2,94 s; cache e handler do worker tambem foram verificados.
O gate focado terminou com 8 testes, Ruff/build/compileall/diff-check limpos.
NVENC nao foi validado nesta fatia dentro do sandbox; repetir no host com a GTX
1060 e `./scripts/check_gpu.sh` antes de afirmar o caminho de hardware.

Proximo gate: download/preview do artifact renderizado, loudness final no alvo e
quality gate audiovisual ampliado. Headline/legendas Remotion continuam fora
deste renderer inicial e nao devem aparecer como concluidas na interface.

## Atualizacao 2026-07-12 - preview, download e loudness final

- render schema v2 aplica `loudnorm` no filtergraph, mede EBU R128 no MP4 e
  bloqueia artifacts fora de -14 LUFS +/- 1 LU ou acima de -1 dBFS true peak;
- a politica de loudness participa do input hash e manifests v1 continuam
  legiveis pelos campos opcionais;
- `GET /projects/{id}/renders/{artifact_id}/media` serve somente MP4 dentro do
  diretorio de renders do projeto, bloqueando traversal, symlink escape e
  vazamento cross-project;
- a etapa Render guarda resultado/status por EditPlanArtifact e mostra manifesto,
  encoder efetivo, duracao, LUFS, true peak, preview HTML5 e download;
- outputs antigos ou de outra selecao nao contaminam o quality gate atual;
- validacao final no host: 65 testes passaram. Build Vite, Ruff, compileall e
  `git diff --check` tambem passaram.

Proximo gate de produto: captions/headline Remotion reais, sidecar SRT e quality
gate visual/audio ampliado. O warning de deprecacao do `fastapi.testclient`
indica migracao futura para `httpx2`, mas nao bloqueia a suite atual.

## Atualizacao 2026-07-12 - captions/headline FFmpeg e sidecar SRT

- render schema v3 carrega o `TranscriptArtifact` referenciado pela EDL e remapeia
  palavras da timeline fonte para a timeline editada, inclusive overlaps de crossfade;
- captions reais sao queimadas no MP4 por FFmpeg/libass e a headline editorial e
  aplicada por `drawtext`; o manifesto registra explicitamente
  `renderer=ffmpeg-libass-drawtext`, sem alegar Remotion;
- o SRT usa o mesmo mapa temporal, e persistido com SHA-256 e servido pelo endpoint
  seguro `GET /projects/{id}/renders/{artifact_id}/subtitles`;
- o hash do render inclui transcript, configuracao de overlays e headline, impedindo
  cache incorreto entre textos ou estilos distintos;
- o quality gate exige cues e sidecar quando captions estao habilitadas e registra
  contagem de cues, captions/headline efetivas, loudness e true peak;
- o frontend envia o titulo editorial como headline, mostra a contagem de legendas
  e oferece download do SRT junto ao MP4.

Validacao focada: render sintetico multi-segmento produziu MP4 com libass/drawtext,
dois cues remapeados, sidecar persistido, loudness aprovado e cache reutilizado.
Ruff, build Vite e `git diff --check` passaram. A suite completa continua travando
nos testes que usam `TestClient` dentro do sandbox; repetir `.venv/bin/pytest -q`
no host antes do merge.

Proximo gate: tornar as configuracoes do Studio persistidas e efetivas no job,
implementar karaoke/estilos por palavra no renderer Remotion proprio e ampliar o
quality gate visual com frames pretos/congelados e verificacao de safe zones.

## Atualizacao 2026-07-12 - Studio efetivo e quality gate visual inicial

- `RenderSettings` v1 valida encoder, canvas/FPS, captions, headline e sidecar;
  o objeto solicitado e persistido no job e o solicitado/efetivo entra no
  manifesto schema v4 e no hash de cache;
- o Studio deixou de manter controles inertes: canvas 9:16/1:1/16:9, encoder,
  captions, fontes instaladas, tamanho, palavras por cue, outline, headline e
  SRT agora alteram o render real;
- fontes sao resolvidas por `fc-match` e qualquer substituicao implicita reprova
  o job, evitando fallback visual silencioso;
- `blackdetect` e `freezedetect` analisam o MP4 final com thresholds versionados;
  contagem, duracao total/maxima e thresholds entram no quality report;
- trechos pretos >= 0,5 s ou congelados >= 1,5 s geram issues explicitas e
  reprovam o artifact antes da publicacao;
- previews TikTok/Reels e safe zones ficaram rotulados como preview-only; recursos
  de camera, J/L-cut, reactions e karaoke nao aparecem como controles efetivos.

Validacao focada: 11 testes passaram, incluindo dois videos sinteticos para os
detectores visuais e render real com settings customizados. Ruff, compileall,
build Vite e `git diff --check` passaram. Testes baseados em `TestClient` ainda
devem ser repetidos no host por causa do travamento conhecido no sandbox.

Proximo gate: criar `apps/remotion/` isolado e versionado, gerar overlay alpha
persistido/cacheavel com headline e karaoke por palavra, compor esse overlay no
master FFmpeg e registrar provenance completa de Node/Remotion. Nao instalar
dependencias `latest` nem fazer fallback silencioso para libass.

## Atualizacao 2026-07-12 - overlay alpha Remotion e karaoke

- `apps/remotion/` usa Remotion `4.0.489`, React `19.2.7` e TypeScript `5.9.3`
  fixados no lockfile; o CLI aceita somente payload v1 validado e argv conhecido;
- a composicao `CortexOverlay` gera WebM VP9 `yuva420p` com template Editorial
  Quote, sombra/outline efetivos e karaoke palavra por palavra na timeline pos-EDL;
- o browser e obrigatorio e explicito (`render.remotion_browser`); o job nao baixa
  Chromium nem retorna silenciosamente a libass/drawtext;
- o overlay e um `StageArtifact` `render_overlay` persistido, com cache baseado no
  payload, lockfile, CLI e fontes da composicao; o manifesto registra SHA-256,
  tamanho, canvas, FPS, duracao e versoes/caminhos efetivos de Node e Remotion;
- o backend confirma VP9 `alpha_mode=1`, dimensoes, duracao, hash e contagem de
  palavras antes de compor o overlay sobre o master com FFmpeg/libvpx;
- o Studio envia karaoke e sombra como configuracoes efetivas, e o manifesto de
  render schema v5 referencia integralmente o artifact alpha;
- um teste real extrai o plano alpha em instantes distintos, prova mudanca temporal
  entre palavras e transparencia apos a ultima palavra, alem de validar composicao,
  sidecar, quality gate e cache dos dois stages.

Validacao no host: 66 testes fora dos sete endpoints `TestClient` passaram em
19,94 s; os builds TypeScript de `apps/web` e `apps/remotion`, Ruff, compileall e
`git diff --check` passaram. O teste Remotion requer host porque abre um servidor
local para o browser. NVENC nao foi repetido neste gate.

Proximo gate: completar o editor visual com cores/animacoes e overrides por corte,
validar safe zones no artifact final e concluir o relatorio de publicacao do
quality gate audiovisual. Depois, iniciar cenas/faces/cameras e J/L-cut reais.

## Fases seguintes

Depois de uma transcricao real revisavel:

1. precomputar waveform/VAD/loudness/room tone;
2. integrar selecao IA com provider proprio, prompt versionado e schema;
3. fallback heuristico identificado, nunca apresentado como LLM;
4. portar boundary optimizer para toda fronteira, inclusive multi-segmento;
5. EDL com relogios de audio/video separados;
6. Remotion proprio para caption/headline;
7. render FFmpeg com NVDEC/NVENC e passes reduzidos;
8. shot/face/speaker/mouth-motion index para reactions seguras;
9. QA completo e E2E de episodio real.

## Regras tecnicas importantes

- FFmpeg edita midia; Remotion compoe tipografia/overlays.
- LLM propoe intencao e timestamps aproximados; nunca define fronteira fisica
  diretamente.
- Todas as fronteiras passam por palavra + VAD + waveform.
- J/L-cut exige audio e video independentes; `xfade+acrossfade` simultaneos nao e
  J/L-cut.
- Reaction shot deve ser interlocutor silencioso/ouvindo, com mouth motion baixo,
  audio da reacao mutado e audio principal continuo.
- Em baixa confianca, preservar fala/pausa.
- Jobs e artefatos devem ser retomaveis, idempotentes e cacheados por hash.
- Registrar requested/effective device, decoder e encoder em cada stage.
- Nao mascarar erro CUDA com CPU.
- Nao reintroduzir thumbnails, Content Studio, knowledge base ou outras paginas
  sem relacao direta com cortes.

## Estado do Git e cuidado com arquivos

Na ultima verificacao havia varias mudancas ainda nao commitadas, incluindo todo
o scaffold novo. `CONNEX_BRIEF.md` e `headline model.jpeg` ja estavam untracked e
devem ser preservados. Antes de trabalhar:

```bash
git status --short
git diff --check
```

Nao apagar `mods/`, `reference/` ou `apply.sh` ainda. Remover somente depois de
paridade real e migracao verificavel. Nao fazer reset/checkout destrutivo.

## Prompt sugerido para iniciar a nova sessao

```text
Continue a implementacao do CorteX em /var/home/dx/Projects/CorteX. Leia primeiro
docs/HANDOFF_FABLE_5.md integralmente e depois os documentos indicados nele.
Audite o git status e valide o estado atual. Trabalhe como orquestrador e delegue
subtarefas quando isso trouxer paralelismo real. O P0 de ingestao/transcricao ja
foi concluido. Comece pelo P1 de Analysis Artifact: VAD persistido, waveform/RMS,
pausas/loudness/room tone, endpoints e revisao real no frontend. Preserve as
mudancas existentes, nao use o frontend/runtime do PodCLI e nao chame selecao ou
render de prontos sem teste real na GTX 1060 Max-Q.
```

## Atualizacao 2026-07-11 - selecao via assinatura

Depois deste handoff, foi implementado o primeiro caminho real de selecao:

- provider `codex_cli` primario, efemero/read-only e sem API key obrigatoria;
- fallback `claude_cli` tool-free, sempre identificado na provenance;
- timeout, cancelamento, JSON Schema, provenance e cache por hash;
- `POST /api/v1/projects/{id}/suggest` e handler `suggestion` no worker;
- frontend dispara o job e usa os cortes retornados na Curadoria;
- 48 testes passaram no host e o build Vite passou;
- Codex usa `gpt-5.5/medium` para selecao editorial; o default mini permanece
  restrito a subagentes de desenvolvimento. Ainda falta E2E com um transcript
  real de episodio.

O P1 de Analysis Artifact continua sendo a proxima dependencia. A waveform da
Curadoria ainda e ilustrativa e as fronteiras exibidas sao apenas regioes
semanticas aproximadas, agora rotuladas dessa forma na interface.

## Atualizacao 2026-07-12 - editor visual completo, overrides por corte e relatório

- o payload v1 do overlay Remotion agora carrega cores efetivas, animacoes de
  caption/karaoke e headline, com validacao estrita no backend e no CLI;
- o render aceita override por corte via merge parcial documentado, persiste o
  settings solicitado/override/efetivo e usa cache por settings resolvido;
- safe zones foram extraidas para definicao versionada compartilhada por canvas
  e o quality gate do render passou a consolidar um relatorio de publicacao com
  loudness, visual, captions, safe zones, encoder e provenance;
- o Studio ganhou affordance de customizacao por corte na etapa Render, sem
  duplicar o painel global.

Revisao do orquestrador (2026-07-12, apos esgotar creditos do codex): a rodada
foi entregue com regressoes que precisaram de correcao no host antes de fechar:

- bundle Remotion estava em cache stale (`apps/remotion/.cache`), reproduzindo o
  erro `interpolate [0,0]`; limpar o cache e rebuildar resolveu. O `safeInterpolate`
  ja guardava ranges iguais;
- `RemotionOverlayManifest` nao declarava os campos de cor emitidos pelo CLI
  (`captionTextColor`, `captionKaraokeColor`, `headlineBurstColor`,
  `headlineStripColor`); adicionados com alias;
- a validacao hex so existia em `RenderColorSettings`; foi estendida para
  `RenderCaptionSettings`, `RenderHeadlineSettings` e as duas classes `Patch`
  (cores agora normalizam para uppercase canonico e rejeitam nao-hex).

Estado validado no host: `.venv/bin/pytest -q` = 78 testes passando (inclusive os
TestClient, que rodaram fora do sandbox), Ruff limpo, `compileall`, builds de
`apps/web` e `apps/remotion` e `git diff --check` OK. Nada commitado.

Safe zones - validacao real por pixels (implementada nesta revisao): a checagem
estatica anterior (tautologica) foi substituida. `RenderService._safe_zone_issues`
agora amostra os pixels nao-transparentes do overlay WebM alpha via FFmpeg
(`alphaextract,bbox`), separando a metade superior (headline) da inferior
(legenda) e comparando as bounding boxes com as margens da safe zone em pixels.
Detalhes que importam para quem for mexer:

- o decoder nativo `vp9` do FFmpeg DESCARTA o plano alpha ("Requested planes not
  available"); e obrigatorio forcar `-c:v libvpx-vp9` ANTES do `-i`, senao a
  analise quebra. O compositor ja usava libvpx; a analise passou a usar tambem;
- a taxa de amostragem e `render.safe_zone_sample_fps` (default 4.0);
- tolerancia de `_SAFE_ZONE_TOLERANCE_PX` (2 px) absorve ruido de subamostragem;
- `tests/test_render_quality_report.py` gera WebMs VP9 alpha reais (alpha escrito
  por `geq`, pois `drawbox` NAO altera o plano alpha) e prova deteccao dentro/fora
  para caption e headline; o teste de render completo exercita o overlay real.

Pendente (nao critico): distinguir headline/legenda hoje assume ancoragem
topo/base via corte ao meio do canvas; se um template futuro posicionar overlays
fora dessas metades, a atribuicao por regiao precisa evoluir.

## Atualizacao 2026-07-12 - indice de cenas e scene snapping

Gate 1 da Fase 5: fundacao deterministica de cortes de camera, usada agora no
scene snapping da EDL e reutilizavel depois por faces/cameras/reactions.

Indice de cenas (`scene_index`):

- deteccao via filtro `scdet` do proprio FFmpeg — zero dependencias Python
  novas (nada de PySceneDetect/OpenCV), decisao de arquitetura explicita;
- `scdet` loga cada corte detectado em stderr como
  `lavfi.scd.score: <score>, lavfi.scd.time: <time>` quando roda com `-v info`;
  `src/cortex/analyze/scene_detect.py` faz o parse por regex (mesmo padrao de
  `blackdetect`/`freezedetect` ja usado em `cortex.render.service`), sem
  precisar do filtro `metadata=print`;
- threshold versionado em `analysis.scene_threshold` (default 10.0, escala
  nativa do scdet, 0-100) em `config/cortex.yaml`, com override por job;
- `SceneIndexDocument` (`src/cortex/analyze/scene_schemas.py`) segue o mesmo
  padrao dos outros stage artifacts: schema versionado (v1), hash de inputs
  (fonte + threshold + versao do algoritmo), cache por hash, JSON persistido em
  `data/projects/<id>/scenes/`. Guarda cortes com score, cenas contiguas
  derivadas dos cortes, contagem, duracao da fonte e provenance (caminho e
  versao efetiva do ffmpeg, filtro e threshold efetivos);
- `SceneIndexService` (`src/cortex/analyze/scene_service.py`) roda o `scdet`
  direto sobre o video fonte (nao sobre o WAV normalizado, que nao tem canal de
  video), com claim/progresso/cancelamento cooperativos identicos ao padrao
  de `AnalysisService` — o subprocesso e monitorado por polling e pode ser
  terminado a meio caminho;
- `JobType.SCENE_ANALYSIS` com handler real em `worker.py`;
  `POST /api/v1/projects/{id}/scenes` cria o job (404/400/409 claros se
  projeto/fonte nao existem ou o arquivo da fonte nao esta disponivel) e
  `GET /api/v1/projects/{id}/scenes/{artifact_id}` retorna o documento.

Scene snapping na EDL:

- `edit_plan` aceita opcionalmente `scene_index_artifact_id`. Quando presente,
  apos o quality gate existente (que ja repara fronteiras dentro de palavra —
  `boundary_snapped`), `snap_segments_to_scene_cuts`
  (`src/cortex/edit/boundary.py`) ajusta toda fronteira de VIDEO da EDL (start
  e end de cada segmento) para o corte de cena mais proximo, DESDE QUE o corte
  esteja a ate `edit.scene_snap_tolerance_seconds` (default 0.5 s,
  `config/cortex.yaml`) E o ponto de destino nao caia dentro de uma palavra
  nem de um intervalo VAD protegido. A funcao reutiliza o mesmo resolvedor de
  fronteiras do modulo (nao ha um resolver paralelo) e a prioridade continua
  sendo preservar fala/pausa, como no `EDITING_ENGINE.md`;
- cada snap aplicado gera uma issue `scene_snapped` (severity `info`, para
  distinguir de reparo de algo inseguro) no `EditQualityReport`, com
  `snapped_from`/`snapped_to` em segundos e `delta_ms`;
  `EditPlanDiagnostics.scene_snap_count` soma os snaps aplicados;
- o hash de cache do `edit_plan` so inclui o `scene_index_artifact_id`, o
  sha256 do documento de cenas e a tolerancia quando um `scene_index` e
  passado — sem ele, o payload do hash e byte-identico ao anterior a este
  gate, entao planos existentes continuam validos e o comportamento sem
  scene_index nao muda;
- `EditPlanDocument.scene_index_artifact_id` (opcional, default `None`) grava
  a proveniencia; `EDIT_PLAN_SCHEMA_VERSION` continua 1 porque o campo novo e
  aditivo e tem default, entao documentos antigos continuam validando.

Curadoria (frontend):

- botao explicito "Detectar cenas" na etapa Curadoria (nunca automatico);
  acompanha o job `scene_analysis` por SSE como os demais e, ao concluir,
  desenha os cortes de cena como marcadores reais sobre a waveform do corte
  ativo (`SceneMarkers` em `apps/web/src/App.tsx`), rotulados como dado real
  de `scdet`, nao preview;
- ao preparar os planos de edicao dos cortes selecionados, o `scene_index`
  detectado (se houver) e enviado automaticamente para ativar o snapping.

Validacao: video sintetico de quatro trechos concatenados (cores solidas +
`testsrc`, cortes conhecidos em ~2.0s/3.5s/6.0s) provou deteccao dentro de
0.2s de tolerancia e cache hit na segunda chamada do `SceneIndexService`
(`tests/test_scene_index.py`). Testes unitarios de `snap_segments_to_scene_cuts`
cobrem snap dentro da tolerancia, recusa por violacao de palavra, recusa por
violacao de VAD, fronteira multi-segmento e no-op sem cortes/tolerancia
(`tests/test_scene_snapping.py`), alem de testes de servico/API provando que um
plano sem `scene_index` fica byte-identico ao comportamento anterior e que um
plano com `scene_index` aplica o snap e registra a issue. `.venv/bin/pytest -q`
= 95 testes (79 anteriores + 16 novos, TestClient incluso) passaram no sandbox
desta vez, sem o travamento historico; Ruff, `compileall`, build de `apps/web`
e `git diff --check` tambem passaram. Nao foi necessario repetir no host desta
vez, mas como o travamento de `TestClient` no sandbox ja foi visto antes,
reconfirmar `.venv/bin/pytest -q` no host antes do merge por seguranca.

Fora do escopo deste gate (fica para os proximos gates da Fase 5): deteccao de
faces, tracking de interlocutor, reaction shots, camera planner e J/L-cut
reais. O `scene_index` aqui e apenas a fundacao determinista de cortes de
camera — nenhuma dessas capacidades foi implementada ou deve ser declarada
como pronta.

Revisao do orquestrador (mesma data): aprovado com uma correcao aplicada apos a
entrega da lane implementer — `snap_segments_to_scene_cuts` podia inverter ou
esvaziar um segmento curto quando start e end eram puxados por cortes de cena
opostos; adicionado guard de comprimento minimo (`_SCENE_SNAP_MIN_SEGMENT_SECONDS`
= 0.2s) com teste dedicado. Validacao final no host: 96 testes, Ruff, compileall,
build Vite e `git diff --check` limpos. Proximo gate da Fase 5: shot/face index
(deteccao de faces e planos por amostragem) como fundacao para tracking de
interlocutor e reaction shots.
