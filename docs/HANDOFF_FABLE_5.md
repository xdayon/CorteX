# Handoff para Fable-5 - CorteX

Plano operacional ate o release candidate: `docs/PRODUCTION_COMPLETION_PLAN.md`.
Novas sessoes devem escolher um unico gate desse documento e manter
`docs/ROADMAP.md` como contrato de produto.

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

## Atualizacao 2026-07-12 - indice de faces e classificacao de plano (Gate 2)

Gate 2 da Fase 5: deteccao de faces e classificacao heuristica de plano
(close/two_shot/wide/none) como fundacao para tracking de interlocutor e
reaction shots — nenhuma dessas duas capacidades foi implementada aqui.

Decisao de stack (fechada pelo advisor, nao rediscutida): `onnxruntime`
(providers `["CPUExecutionProvider"]`, nunca CUDA — a GPU e reservada para
Whisper/NVENC) rodando o modelo YuNet ONNX (`face_detection_yunet_2023mar.onnx`,
~227 KB, MIT, OpenCV Zoo) versionado em `src/cortex/models/` junto da licenca
(`FACE_DETECTION_YUNET_LICENSE`). Zero dependencias Python novas alem de
`onnxruntime` (ja transitiva do `faster-whisper`, agora declarada explicita no
extra `analysis` do `pyproject.toml`) e `numpy` (ja presente).

Detector (`src/cortex/analyze/face_detect.py`):

- pre/pos-processamento em numpy puro, porta linha-a-linha do decode/NMS de
  `FaceDetectorYNImpl::postProcess` do proprio OpenCV
  (`modules/objdetect/src/face_detect.cpp`) — grade por stride (8/16/32),
  score `sqrt(cls*obj)`, decode de bbox+5 landmarks, NMS por IoU;
- o grafo ONNX do YuNet 2023mar tem entrada **fixa** 640x640 (sem eixos
  dinamicos), diferente do demo `yunet.py` do Zoo (que so funciona porque
  `cv.FaceDetectorYN` reconstroi a rede a cada `setInputSize`). Por isso o
  frame extraido e letterboxed (resize proporcional + padding com zero,
  ancorado no canto superior esquerdo) para o canvas 640x640 antes da
  inferencia, e as coordenadas decodificadas sao divididas pelo mesmo fator de
  escala para voltar ao espaco de pixels do frame original — exatamente o
  "bug de escala que desloca bbox silenciosamente" que a spec pedia cuidado,
  coberto por teste dedicado (`test_letterbox_downscales_tall_frame_uniformly`);
- bboxes/landmarks reportados normalizados (0-1), independentes da resolucao
  de extracao.

Amostragem de frames (`src/cortex/analyze/face_service.py`):

- exige `scene_index` existente (precondicao 404/409 clara, tanto na API
  quanto no `FaceIndexService`, se o artifact ou seu arquivo nao existir);
- amostra: ponto medio de cada `SceneSegment`, `cut_time + 0.3s` apos cada
  corte, e grade uniforme a `analysis.face_sample_fps` (novo knob, default
  1.0, `config/cortex.yaml`);
- extracao via `ffmpeg` do sistema (mesmo padrao de `render/service.py`), com
  `scale=640:-2` (nunca decodifica 4K inteiro) e saida `rgb24` num muxer PPM
  (`-f image2pipe -vcodec ppm`) — o header PPM ja informa a largura/altura
  exatas que o `-2` do swscale escolheu, entao nao ha suposicao sobre
  arredondamento par/impar do ffmpeg.

Classificacao de plano e identidade (`src/cortex/analyze/face_classify.py`),
tudo com thresholds nomeados e testavel sem o modelo (deteccoes sinteticas):

- `close`/`two_shot`/`wide`/`none` por razao area do bbox / area do frame,
  proximidade do centro e comparacao de tamanho entre as duas maiores faces;
- identidade **sem embeddings**: clustering 1-D (split no maior gap) dos
  centroides x de todas as deteccoes do episodio em slots estaveis
  (`person_left`/`person_right`/`person`), assumindo camera fixa; cada
  deteccao herda o slot mais proximo. `FaceDetection.embedding` fica
  reservado (default `None`) para um upgrade futuro com ArcFace;
- agregacao por `SceneSegment`: tipo de plano dominante e slots presentes por
  cena, guardados em `FaceIndexDocument.scenes`.

API e worker: `JobType.FACE_ANALYSIS` com handler real (progresso/cancelamento
identicos ao `SCENE_ANALYSIS`), `POST /api/v1/projects/{id}/faces` (mesmas
validacoes de seguranca/precondicao do `scenes`, mais a exigencia de
`scene_index_artifact_id` valido) e `GET /api/v1/projects/{id}/faces/{artifact_id}`.
`FaceIndexDocument`/`face_index` seguem o mesmo molde de `scene_index`: schema
versionado (v1), hash de input (fonte + `scene_index` + sample_fps + versao do
algoritmo), cache por hash, escrita atomica `.tmp` -> `replace`.

Curadoria (frontend): botao "Detectar rostos/planos" ao lado de "Detectar
cenas" na etapa Curadoria, acompanhando o job por SSE (`watchJob`, mesmo
padrao); ao concluir, badges com o tipo de plano dominante por cena aparecem
abaixo da waveform (`FaceShotBadges` em `apps/web/src/App.tsx`) para as cenas
que caem no intervalo do corte ativo. Sem desenho de bbox sobre o video neste
gate (fica para um gate futuro).

Este gate **nao** integra faces na EDL — sem rodar a deteccao, nada muda no
pipeline existente (`edit_plan` continua ignorando `face_index` por completo).

Validacao: golden test com foto real (retrato oficial de dominio publico,
`tests/fixtures/face_golden_portrait.jpg`, ver `tests/fixtures/README.md` para
proveniencia/licenca) confirma deteccao de 1 rosto com score > 0.8 e bbox
dentro de tolerancia; testes unitarios de decode/NMS com fixtures sinteticas
deterministicas (`tests/test_face_detect.py`); heuristicas de plano/identidade
com deteccoes sinteticas (`tests/test_face_classify.py`); cache hit e
precondicao de `scene_index` ausente, servico e API (`tests/test_face_index.py`).
Suite completa: 126 testes (96 anteriores + 30 novos) passando; Ruff,
`compileall` e build do `apps/web` (`tsc --noEmit` + `vite build`) limpos;
`git diff --check` sem trailing whitespace.

Proximo gate da Fase 5: tracking de interlocutor (continuidade de identidade
alem do slot heuristico por posicao), reaction shots semanticamente coerentes
e J/L-cut editaveis continuam pendentes.

## Atualizacao 2026-07-12 - speaker timeline visual (Gate 3)

Gate 3 da Fase 5: novo stage artifact `speaker_timeline`, separado do
`face_index`, porque faces sao observacoes esparsas e atribuicao de fala e uma
inferencia temporal com cache, confianca e proveniencia proprios.

- inputs obrigatorios e explicitos: fonte, `scene_index`, `face_index` e
  `analysis`/VAD; o servico valida pelo conteudo dos documentos que toda a cadeia
  corresponde a mesma fonte e inclui IDs + input hashes no hash canonico;
- dentro do VAD, frames RGB a 320 px/4 fps sao extraidos em streams PPM por
  chunks coalescidos (padding default 150 ms, gaps ate 2 s), sem um subprocesso
  FFmpeg por frame e com memoria constante; YuNet nao roda novamente;
- bbox/landmarks do `face_index` sao interpolados somente dentro da mesma cena e
  com distancia maxima conservadora; a ROI labial e normalizada em numpy, o
  motion desconta uma ROI facial de controle e usa baseline robusta por
  track/cena nos frames de padding fora do VAD;
- o vencedor exige limiar absoluto e margem; baixa cobertura/motion vira
  `unknown`, scores proximos viram `overlap`, e fora do VAD vira `no_speech`;
  close de camera sozinho nunca e prova de quem fala;
- schema v1 usa microssegundos inteiros, observations por track, segmentos
  contiguos e engine info com requested/effective FPS, largura, decoder/device e
  versao/caminho FFmpeg; persistencia atomica e cache seguem os gates anteriores;
- `JobType.SPEAKER_ANALYSIS`, worker e endpoints `POST /speakers` +
  `GET /speakers/{artifact_id}` estao conectados. O artifact ainda nao participa
  da EDL nem dispara automaticamente no Studio.

Limite deliberado: `person_left`/`person_right` continua sendo slot visual, nao
identidade global confirmada entre cameras. ArcFace/embeddings, diarizacao
acustica, timeline de cameras, reaction shots, listening posture, camera planner
e J/L-cut permanecem para gates futuros. Reaction shot deve recusar `unknown` e
nao pode tratar este artifact sozinho como diarizacao acustica.

Validacao focada: 10 testes speaker sem o endpoint `TestClient` passaram,
incluindo decode FFmpeg streaming real com video sintetico, mouth motion,
persistencia/cache, cadeia upstream e caminho visual positivo. Ruff, compileall,
build Vite e `git diff --check` passaram. O teste endpoint API -> worker -> GET e
a suite completa reproduziram o hang historico de finalizacao do `TestClient` no
sandbox sem reportar assertion; repetir `.venv/bin/pytest -q` no host antes do
merge. Nenhuma validacao CUDA/NVENC foi feita ou necessaria neste gate CPU-only.

## Atualizacao 2026-07-12 - camera timeline conservadora (Gate 4a)

Novo stage artifact `camera_timeline`, derivado exclusivamente de
`scene_index` + `face_index` + `speaker_timeline`. Ele agrega cada cena em
microssegundos com plano dominante, tracks visiveis, speaker dominante,
cobertura de fala, alinhamento e um role de camera conservador:

- `speaker_close` somente quando ha um unico track visivel no close e o speaker
  visual dominante coincide com ele acima do limiar de 55%;
- `two_shot`, `wide` e `no_face` sao roles puramente visuais;
- close com speaker ausente, ambiguo ou incompativel vira `unknown`, nunca
  listener/reaction por inferencia de camera;
- `layout_id` e hash deterministico de shot type + slots visiveis. Ele permite
  agrupar layouts repetidos, mas NAO identifica uma camera fisica nem confirma
  a mesma pessoa entre cameras;
- hash de cache inclui IDs e input hashes dos tres artifacts, versao do
  algoritmo, limiar e escopo de identidade; o servico valida toda a cadeia pelo
  conteudo, persiste JSON atomico e registra `identity_scope=layout_track_only`;
- `JobType.CAMERA_ANALYSIS`, worker e endpoints `POST /cameras` e
  `GET /cameras/{artifact_id}` estao conectados. A EDL e o Studio ainda nao
  consomem automaticamente este artifact.

Validacao focada: 5 testes cobrem speaker close confirmado, mismatch fail-safe,
two-shot/wide, persistencia/cache, cadeia upstream invalida e job processado pelo
worker real ate um artifact persistido. Reaction shots permanecem bloqueados:
identidade cross-camera confirmada, quality index de freeze/blur/oclusao e o
planner que preserva audio continuo ainda nao existem.

Verificacao integrada desta entrega: 62 testes fora dos arquivos que usam
`TestClient` e 15 testes focados camera/speaker passaram; Ruff, compileall,
OpenAPI das rotas de camera, build Vite e `git diff --check` passaram. A suite
integral com `TestClient` continua pendente de repeticao no host pelo hang de
sandbox ja registrado no Gate 3.

## Atualizacao 2026-07-12 - indice de qualidade visual (Gate 4b)

Novo stage artifact `visual_quality_index`, derivado explicitamente da fonte +
`scene_index` + `face_index`, sem alterar automaticamente EDL ou render:

- amostragem configuravel (default 2 fps) por cena via FFmpeg software/CPU;
- cada frame registra luma media, proporcao de pixels pretos, variancia do
  Laplaciano para blur e delta normalizado para congelamento;
- faces do indice esparso sao associadas somente dentro de uma janela temporal
  limitada; `face_edge_occlusion` significa exclusivamente bbox tocando a borda
  do frame e nao alega oclusao semantica;
- agregacao por cena persiste shares, issues e `usable`; limiares, FFmpeg,
  decoder/device solicitado e efetivo ficam no engine info do schema v1;
- o hash inclui fonte, IDs/hashes dos dois upstreams, versao do algoritmo,
  thresholds, amostragem, decoder e versao do FFmpeg; persistencia atomica e
  cache seguem os stages anteriores;
- `JobType.VISUAL_QUALITY_ANALYSIS`, worker e endpoints `POST /visual-quality` +
  `GET /visual-quality/{artifact_id}` estao conectados.

Validacao focada: 5 testes de metricas, persistencia/cache, cadeia upstream,
worker ate artifact JSON persistido e contrato OpenAPI passaram; o conjunto
camera/face/qualidade terminou com 35 testes. Ruff, compileall, build Vite e
`git diff --check` passaram. Frames sinteticos deterministas foram injetados no
teste do servico, portanto nao ha alegacao de validacao sobre episodio real,
CUDA ou NVENC. A regressao que incluiu arquivos com `TestClient` repetiu o hang
de finalizacao apos 10 testes; uma tentativa ampla por arquivos sem a string
`TestClient` tambem bloqueou apos 7 testes e foi interrompida sem assertion
reportada. A suite integral continua pendente no host.

Proximo gate: identidade cross-camera com evidencia propria e escopo explicito;
so depois disso o planner pode combinar `camera_timeline` +
`visual_quality_index` para reaction shots e J/L-cuts conservadores.

## Atualizacao 2026-07-12 - identidade SFace entre layouts (Gate 4c)

O `face_index` passou ao schema v2 e agora gera embedding SFace normalizado de
128 dimensoes para cada face detectada. O modelo oficial
`face_recognition_sface_2021dec.onnx` (36.9 MiB, SHA-256
`0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`) e a
licenca Apache do OpenCV Zoo ficam versionados em `src/cortex/models/`.

- alinhamento de cinco landmarks para 112x112 e inferencia ONNX foram
  implementados em numpy + onnxruntime CPU, sem adicionar `opencv-python`;
- o engine do `face_index` registra recognizer, caminho/hash do modelo,
  dimensao, providers e limiar de cosseno; o cache v1 e invalidado pela mudanca
  de schema/algoritmo e nunca e reutilizado como se contivesse embeddings;
- novo artifact `identity_index`, derivado de `face_index` + `camera_timeline`,
  usa complete-link: um novo embedding precisa superar o limiar contra todos os
  membros da identidade, e proximidade de dois grupos dentro da margem vira
  `ambiguous`, sem merge silencioso;
- identidade e `confirmed` apenas com pelo menos duas observacoes SFace em
  `layout_id`s distintos. Singletons/same-layout ficam `single_layout`; o escopo
  registrado e `cross_layout_face_embedding`, nao camera fisica descoberta;
- `JobType.IDENTITY_ANALYSIS`, worker, cache/persistencia atomica e endpoints
  `POST /identities` + `GET /identities/{artifact_id}` estao conectados. A EDL
  e o Studio ainda nao consomem automaticamente o artifact.

Validacao: 8 testes novos inicialmente e depois 19 testes integrados de
SFace/identidade/camera/qualidade passaram. O caminho real YuNet -> SFace foi
executado sobre a fixture de retrato; outros 27 testes de face detect/classify e
servico `face_index` passaram. Ruff, compileall, build Vite e
`git diff --check` estao limpos. Nao houve episodio multicamera real, CUDA ou
NVENC neste gate; continuidade cross-layout foi verificada com embeddings
sinteticos controlados e o ONNX real foi verificado separadamente.

Proximo gate: planner conservador de reaction shots consumindo
`camera_timeline` + `identity_index` + `visual_quality_index`, preservando audio
continuo. J/L-cut e punch-in editaveis continuam posteriores.

## Atualizacao 2026-07-12 - camera edit plan vinculado (Gate 5a)

Novo stage artifact `camera_edit_plan`, derivado explicitamente de `edit_plan` +
`camera_timeline` + `identity_index` + `visual_quality_index`. Este gate nao
fabrica reaction footage: o produto atualmente recebe um master unico, sem ISO
de cameras, e `speaker_timeline` continua visual, nao diarizacao acustica.

- cada shot e a intersecao exata de uma cena com um keep range da EDL e registra
  intent `speaker`, `context` ou `fallback`, layout, role, identidades confirmadas
  e evidencia de qualidade;
- `speaker` exige `speaker_close` + alinhamento confirmado + cena utilizavel;
  `two_shot`/`wide` utilizaveis viram `context`; qualidade ausente/reprovada e
  roles ambiguos viram `fallback`;
- schema v1 exige que `audio_source_*` seja identico a `source_*`, registra
  `audio_continuity_mode=linked_source` e `temporal_reuse_allowed=false`;
- reaction shots ficam explicitamente desabilitados por tres bloqueadores:
  identidade acustica do speaker ausente, listening posture ausente e fonte de
  camera alternativa ausente. Nenhum desses sinais e inferido por posicao;
- hash/cache incluem IDs e input hashes dos quatro upstreams, algoritmo,
  invariantes de continuidade e bloqueadores; persistencia atomica, worker e
  endpoints `POST /camera-plans` + `GET /camera-plans/{artifact_id}` estao
  conectados. Render e Studio ainda nao consomem automaticamente o artifact.

Validacao focada: 5 testes do planner cobrem classificacao, audio/video
vinculados, quality fail-safe, cache/persistencia, cadeia invalida, worker e
OpenAPI. Regressao integrada com face/SFace, identidade, cameras e qualidade:
24 testes passando. Proximo gate deve escolher entre (a) adicionar diarizacao
acustica local com proveniencia para reaction shots no master ou (b) suportar
fontes ISO sincronizadas; sem um desses inputs, reaction shots permanecem
corretamente bloqueados. J/L-cut e punch-in continuam posteriores.

## Atualizacao 2026-07-12 - sincronizacao de fontes ISO (Gate 5b)

Foi escolhida a rota deterministica de fontes ISO, evitando introduzir um
modelo de diarizacao externo/gated. Novo stage artifact `multicam_sync` recebe
uma fonte primaria e 1-8 alternativas ja ingeridas no mesmo projeto:

- cada fonte reutiliza o cache existente de audio PCM mono 16 kHz;
- o sincronizador reduz o sinal a envelope RMS normalizado de 100 Hz e calcula
  correlacao cruzada por FFT dentro de um offset maximo configuravel;
- o contrato de offset e explicito: `alternate_time = primary_time + offset`;
- cada camera registra offset em microssegundos, correlacao normalizada, margem
  contra o segundo pico, overlap e status `synced`/`rejected` com reason;
- defaults: busca ate 120 s, analise dos primeiros 600 s, correlacao minima
  0.55, margem 0.05 e overlap minimo 5 s. Sinal fraco, pico ambiguo ou overlap
  insuficiente nunca e aceito silenciosamente;
- hash/cache incluem IDs + SHA-256 de todas as fontes, parametros, algoritmo e
  caminhos FFmpeg/FFprobe; persistencia atomica, `JobType.MULTICAM_SYNC`, worker
  e endpoints `POST /multicam-sync` + `GET /multicam-sync/{artifact_id}` estao
  conectados.

Validacao: 5 testes focados, incluindo WAVs reais normalizados pelo FFmpeg,
offset conhecido de +800 ms, rejeicao de audio nao relacionado, cache, worker e
OpenAPI. Regressao integrada multicam/camera plan/identidade/qualidade/SFace: 29
testes passando. O planner ainda nao consome automaticamente o sync: cada ISO
precisa primeiro de `scene_index`, `face_index`, `speaker_timeline`,
`camera_timeline` e `visual_quality_index` proprios ou de um orquestrador que
gere essa cadeia. Reaction shots continuam bloqueados ate esse proximo gate.

## Atualizacao 2026-07-12 - indices visuais automaticos por ISO (Gate 5c)

Novo stage artifact `multicam_visual_index`, consumindo um `multicam_sync`
persistido. Para cada camera com status `synced`, o mesmo job executa em ordem:

1. `scene_index` da fonte ISO;
2. `face_index` v2 com YuNet + embeddings SFace;
3. `visual_quality_index` da mesma cadeia cena/face.

O manifest final registra source ID/SHA, offset em microssegundos e IDs dos tres
artifacts. Cameras rejeitadas pelo sync sao preservadas como `rejected` com o
reason original e nenhum substage e executado. Fonte ausente, SHA divergente ou
projeto incorreto falha explicitamente; cancelamento dos substages propaga para
o job pai. O cache do manifest inclui o sync e as configuracoes efetivas de
cena, face e qualidade; cada substage tambem conserva seu cache proprio.

`JobType.MULTICAM_VISUAL_INDEX`, worker e endpoints `POST /multicam-visual` +
`GET /multicam-visual/{artifact_id}` estao conectados. Speaker timeline nao foi
forjada para a ISO: reutilizar o VAD primario exigiria remapeamento temporal e
proveniencia novos. Para escolha segura de contexto no proximo gate, cena,
faces/identidade visual e quality ja sao suficientes; reaction semantica ainda
exige speaker/listening evidence adicional.

Validacao: 5 testes focados de schema, ordem/encadeamento, skip de rejeitada,
fonte inconsistente, cache, worker e OpenAPI; os substages foram isolados por
fixtures porque ja possuem testes reais proprios. Regressao multicamera completa:
34 testes passando. Proximo gate: `camera_edit_plan` v2 consumir o manifest,
converter `primary_time + offset` para o tempo ISO e selecionar apenas contexto
visual utilizavel no mesmo instante global; depois o render deve suportar video
ISO com audio continuo da fonte primaria.

## Atualizacao 2026-07-12 - camera planner com contexto ISO (Gate 5d)

`camera_edit_plan` passou ao schema v2 e aceita opcionalmente um
`multicam_visual_index`. O planner continua construindo os shots primarios e so
consulta ISOs quando o intent resultante seria `fallback`:

- converte o intervalo global pela regra `iso_time = primary_time + offset`;
- exige que uma unica cena ISO cubra todo o intervalo, evitando cruzar um corte
  interno desconhecido;
- exige `visual_quality.usable` e plano dominante `two_shot` ou `wide`;
- prioriza `two_shot`, depois menor soma de shares de quality e source ID para
  desempate deterministico;
- nunca substitui `speaker` nem `context` primarios e nunca usa close ISO sem
  speaker evidence;
- cada shot v2 registra `video_source_asset_id`, `audio_source_asset_id`, tempos
  separados e `sync_offset_us`. O schema exige duracoes iguais e valida
  `video_time = audio_time + offset`; mesma fonte exige offset zero;
- engine registra `audio_continuity_mode=primary_source_continuous` e
  `temporal_reuse_allowed=false`. Com uma ISO indexada, o bloqueador
  `alternate_camera_source_unavailable` desaparece, mas reaction shots seguem
  desabilitados por falta de identidade acustica e listening posture.

Hash/cache e manifest do artifact incluem o `multicam_visual_index` quando
presente. API `CameraEditPlanRequest` e worker aceitam esse upstream opcional;
sem ele, o comportamento primario anterior permanece, com cache v2 separado.

Validacao focada: 6 testes, incluindo selecao ISO +800 ms, invariantes de
audio/video, preservacao dos shots primarios e worker com manifest. Regressao
multicamera completa: 35 testes passando. Proximo gate: render consumir o camera
plan v2, abrir as fontes de video indicadas por shot e manter apenas o audio da
fonte primaria; ate isso, o artifact e persistido/revisavel mas nao altera o MP4.

## Atualizacao 2026-07-12 - render multicamera real (Gate 5e)

O render passou ao schema v6 e aceita opcionalmente o `camera_edit_plan` v2 pela
API, payload do job e `RenderService`. Antes de executar FFmpeg, valida o vinculo
com o mesmo `edit_plan`, projeto e fonte primaria, exige cobertura contigua e
integral de cada segmento e resolve todos os `SourceAsset` de video sem fallback.

O filtergraph abre a fonte primaria e cada ISO indicada, recorta os shots nos
tempos ISO persistidos e concatena as trocas apenas no ramo de video. O ramo de
audio continua sendo recortado uma unica vez por segmento diretamente da fonte
primaria, portanto nenhuma troca de camera introduz audio ISO ou emenda interna.
Transicoes entre segmentos da EDL, loudness, overlays e quality gates existentes
continuam no mesmo caminho de render.

Cache e manifesto incluem o hash do camera plan e SHA de todas as fontes. O
manifesto registra `camera_edit_plan_artifact_id` e, por fonte, uso efetivo de
video/audio. Teste sintetico produz MP4 real com primario vermelho/440 Hz e ISO
azul/1000 Hz, confirma a troca visual por pixels e confirma audio final em 440 Hz;
o worker reutiliza o mesmo artifact multicamera em cache.

Esta implementacao multicamera ficou fora do caminho de produto apos a correcao
registrada abaixo; nao conectar esses endpoints novamente ao Studio.

## Correcao de produto 2026-07-12 - master unico ja comutado

O usuario nao fornece fontes ISO. O estudio grava no OBS/sistema equivalente e
entrega um unico episodio cuja troca de cameras ja aconteceu ao vivo. Qualquer
fluxo de upload/sincronizacao multicamera no Studio foi resultado de uma falha de
comunicacao e nao pertence ao produto.

Objetivo visual correto para os cortes:

- evitar que o corte mostre somente o convidado quando o episodio contem imagem
  segura do entrevistador;
- primeiro preservar planos do entrevistador/two-shot que ja ocorram naturalmente
  dentro do intervalo selecionado;
- quando necessario, reutilizar apenas o VIDEO de um instante diferente do mesmo
  master em que o entrevistador esteja silencioso e ouvindo, mantendo continuo o
  AUDIO da fala principal do corte;
- exigir identidade distinta, mouth motion baixo, ausencia de fala atribuida,
  qualidade visual aprovada e duracao segura; em baixa confianca, nao inserir;
- registrar no artifact o tempo editorial, o tempo de origem do reaction e toda
  evidencia. Esta e uma excecao controlada a antiga proibicao de reutilizacao
  temporal, que deve ser substituida no schema/planner.

O frontend ISO do Gate 5f foi removido antes da entrega. Os servicos backend
`multicam_sync`/`multicam_visual_index` e o render v6 multicamera permanecem
temporariamente isolados no worktree, sem entrada na interface, e devem ser
removidos depois que os testes uteis de audio continuo forem portados para o
novo planner single-source.

Na mesma correcao, o Brief IA ganhou texto padrao geral para cortes virais de
Reels/TikTok, sem tema religioso fixo. O Studio passou a aceitar legenda desde
12 px, escalar corretamente o tamanho na miniatura, refletir fonte, karaoke,
cores, borda e sombra ao vivo e oferecer seletor visual de cores com hex opcional.

Proximo gate: banco persistido de reaction candidates do entrevistador no master
unico, seguido de planner e render com video emprestado/audio editorial continuo.

## Atualizacao 2026-07-12 - banco single-source de reaction candidates

O primeiro gate apos a correcao de produto foi concluido com o artifact versionado
`reaction_candidate_index`. Ele deriva exclusivamente de `speaker_timeline` +
`camera_timeline` + `identity_index` + `visual_quality_index`, valida a cadeia da
mesma fonte e persiste janelas em microssegundos, identidade/track do entrevistador,
speaker concorrente confirmado e distinto, mouth motion, qualidade, confianca,
rejeicoes e evidencias auditaveis.

O papel do entrevistador nao e inferido por posicao ou frequencia. O job/API exige
`interviewer_identity_id` explicito e recusa qualquer identidade que nao esteja
`confirmed` pelo SFace cross-layout. O artifact registra a politica
`mute_reaction_source_preserve_editorial_audio`, mas ainda nao altera camera plan
nem render.

Arquivos principais:

- `src/cortex/analyze/reaction_candidate_schemas.py`;
- `src/cortex/analyze/reaction_candidate_service.py`;
- `src/cortex/api.py`, `src/cortex/worker.py` e `src/cortex/schemas.py`;
- `tests/test_reaction_candidates.py`.

Validacao focada: 17 testes passaram cobrindo o novo artifact, fail-closed,
cache, worker/API e regressoes de identidade/camera plan.

Proximo gate: camera planner single-source consumindo o banco. Ele deve preservar
primeiro aparicoes naturais do entrevistador que ja cruzem a EDL e permitir video
emprestado de outro tempo apenas com limite de reutilizacao, proximidade temporal,
origem editorial/origem visual separadas e audio primario continuamente intacto.

## Atualizacao 2026-07-12 - reactions em silencio adjacente

O banco foi ampliado para episodios em que o master mostra quase sempre quem esta
falando. `speaker_timeline` v2 agora persiste observacoes `no_speech` com mouth
motion nas margens antes/depois do VAD; o padding visual default passou de 0,15 s
para 1,0 s. Essas observacoes sao explicitamente filtradas da construcao dos
segmentos, portanto nao reclassificam fala acustica como silencio.

`reaction_candidate_index` v2 distingue `concurrent_speech` de
`adjacent_silence`. Uma reaction silenciosa so e aceita quando possui identidade
do entrevistador confirmada, track unico, qualidade utilizavel, mouth motion baixo
e uma fala visual proxima de outra identidade confirmada. O artifact registra
identidade, timestamp e distancia dessa fala de referencia; sem essa evidencia,
o candidato e recusado. O audio-fonte da reaction continua sempre proibido.

O proximo gate permanece o camera planner single-source. Ainda nao ha insercao
automatica nem alteracao do render neste incremento.

## Atualizacao 2026-07-12 - planner single-source e contrato de render

`camera_edit_plan` v3 consome opcionalmente `reaction_candidate_index` e registra
por shot a origem visual (`primary_in_place`, `reaction_reuse` ou o caminho ISO
legado isolado), candidate, identidade, score e intervalos de video/audio. O
planner preserva primeiro todos os planos naturais da EDL; somente um shot
`fallback` pode receber video emprestado, se um candidate seguro couber por inteiro,
nao sobrepuser o audio editorial e ainda nao tiver sido reutilizado no corte.

O audio continua sempre na fonte primaria e cobre a EDL sem gaps/sobreposicoes. O
render agora aceita o plano v3 e rejeita uma reaction que tente usar outra fonte ou
audio diferente do master. A composicao FFmpeg ja recorta video e audio em ramos
separados, portanto o video emprestado do mesmo master nao reutiliza seu audio.

O proximo gate e ampliar o render com regressao sintetica dedicada para provar,
por pixels e frequencia, video emprestado do master com audio editorial continuo;
depois disso, remover os caminhos experimentais ISO e portar somente esses testes
de continuidade uteis.

## Atualizacao 2026-07-12 - render de reaction single-source validado

O renderer recebeu regressao de ponta a ponta para o contrato do planner v3. O
fixture cria um unico master com dois trechos de video visualmente distintos e
audio editorial continuo. Um `reaction_reuse` usa o video do segundo trecho
enquanto o intervalo editorial e o audio permanecem no primeiro.

O MP4 publicado foi verificado por pixel central (video emprestado) e frequencia
dominante (audio de 440 Hz do trecho editorial). O manifesto confirma a unica
fonte como `video_used=true` e `audio_used=true`. Assim, o caminho nao reutiliza
o audio da reaction e o contrato fail-closed do render v3 fica coberto por teste
real de FFmpeg.

Validacoes nesta fatia:

- `tests/test_reaction_candidates.py`, `tests/test_camera_edit_plan.py` e
  `tests/test_render.py`: 17 testes passaram;
- Ruff e `git diff --check`: passaram;
- `cd apps/web && npm run build`: passou.

`.venv/bin/pytest -q` continua sem concluir no sandbox, parando apos os dois
primeiros testes de integracao, como nas rodadas anteriores. Repetir a suite no
host antes do merge.

Proximo gate: remover o ramo experimental ISO (`multicam_sync`,
`multicam_visual_index` e o suporte de render associado), preservando somente os
testes de continuidade audio/video que sustentam o planner single-source.

## Atualizacao 2026-07-12 - confirmacao de exportacao no Studio

- A etapa Render agora preserva no estado de cada corte os caminhos retornados
  pelo job (`export_path` e `export_subtitles_path`) e os exibe junto do
  manifesto. Assim, quando o usuario configura um diretorio de exportacao, a UI
  confirma a copia persistida do MP4 e do SRT, alem dos downloads seguros do
  artifact canonico do projeto.
- A copia continua atomica e e repetida tambem em cache; ela nao altera o
  artifact de render nem seu hash, pois o diretorio de destino e uma decisao de
  entrega, nao uma configuracao de composicao.
- Validado no host: `tests/test_render.py` com 7 testes passou em 23,48 s,
  incluindo render Remotion real, overlay alpha/karaoke, SRT, cache e o video
  de reaction com audio editorial continuo. `tests/test_render_captions.py` (2),
  `tests/test_render_settings.py` (6) e `tests/test_render_media_api.py` (2)
  tambem passaram. Builds de `apps/web` e `apps/remotion`, `compileall` e
  `git diff --check` passaram.
- A execucao agregada de `.venv/bin/pytest -q` voltou a nao emitir resumo neste
  ambiente depois de avancar a 38%; nao usar essa saida como evidencia de suite
  completa. Repetir no host interativo antes de merge/release.

## Atualizacao 2026-07-12 - presets persistidos por projeto

- `render_presets` persiste snapshots completos e validados de `RenderSettings`,
  com nome unico por projeto, timestamps e isolamento entre projetos;
- o Studio lista, aplica, cria e atualiza presets sem incluir diretorio de
  exportacao ou overrides por corte, que continuam sendo dados do job;
- a API expoe `GET/POST /projects/{id}/render-presets` e
  `PUT /projects/{id}/render-presets/{preset_id}`;
- validacao focada: tres testes cobriram store, handlers HTTP, round-trip,
  atualizacao e isolamento. `compileall`, build do Studio e `git diff --check`
  passaram. O E2E com `TestClient` permanece um gate de host por causa do
  bloqueio conhecido do sandbox.

## Atualizacao 2026-07-12 - fila persistida e retomada segura

- o Studio enfileira todos os planos de edicao selecionados antes de aguardar
  qualquer render, portanto a fila continua no SQLite depois que a pagina fecha;
- jobs `running` registram o PID do worker que os reivindicou; no startup, o
  worker reenfileira somente jobs sem PID ou cujo processo local nao existe,
  evitando duplicar trabalho de outro worker ativo;
- a recuperacao preserva ID, payload, progresso e FIFO, e adiciona uma mensagem
  auditavel ao job recuperado;
- validacao focada: 14 testes cobriram recovery, PID vivo, FIFO de 25 jobs,
  presets e handlers. Builds web/Remotion, `compileall` e `git diff --check`
  passaram.

## Atualizacao 2026-07-12 - enquadramento vertical e background desfocado

- `RenderSettings.framing` persiste dois modos efetivos no job, preset, hash e
  manifesto: `vertical_crop` e `blurred_background`;
- `vertical_crop` amplia e recorta centralmente a fonte para preencher o canvas,
  sem `pad` ou barras pretas;
- `blurred_background` preserva o quadro 16:9 nítido no centro e preenche o
  canvas com o proprio video ampliado e Gaussian blur em resolucao reduzida;
- o Studio expoe a escolha antes do editor, e overrides por corte agora usam a
  mesma chave com que foram salvos;
- E2E real aprovado no episodio de 98 minutos: artifact
  `6377de0342874e009f842cb5cee51f87`, MP4 de 76 MB, SRT, NVENC, quality gate e
  export em `data/output/e2e-framing-blur`;
- validacao focada: 12 testes passaram; builds web/Remotion, `compileall` e
  `git diff --check` passaram.

Proximo gate: `face_static_crop` usando `face_index` + `identity_index`
persistidos. O enquadramento deve calcular uma unica posicao horizontal robusta
para cada segmento editorial e mante-la fixa durante todo o segmento, sem pan,
tracking, EMA ou movimento continuo de camera. O alvo deve ser uma identidade
explicitamente confirmada; sem evidencia suficiente, usar crop central com
fallback registrado no manifesto.

## Handoff da sessao - proximo orquestrador

Data: 2026-07-12. Nao ha worker, pytest ou render Remotion ativo no encerramento.
O worktree possui uma fatia grande e coerente ainda nao commitada; preservar tudo,
rodar os gates abaixo e criar um checkpoint antes de abrir outra frente.

### Estado validado nesta sessao

- export real funciona com MP4, SRT, preview/download, copia atomica e quality gate;
- presets nomeados por projeto estao persistidos e reaplicaveis no Studio;
- fila de ate 25 renders e persistida antes do acompanhamento; jobs abandonados
  sao recuperados por PID sem reenfileirar worker vivo;
- safe zone da legenda foi corrigida sem reduzir a fonte: stroke/sombra agora
  possuem inset inferior de 20 px no overlay Remotion;
- `vertical_crop` preenche o canvas sem `pad`/barras pretas;
- `blurred_background` mantem o 16:9 nitido sobre o proprio video ampliado e
  desfocado; o blur roda em proxy 270x480 e volta a 1080x1920 antes da composicao;
- o Studio expoe os dois modos e presets/overrides persistem a escolha;
- E2E real aprovado: job `284a25477fb44609988c6118056e845a`, artifact
  `6377de0342874e009f842cb5cee51f87`, MP4/SRT em
  `data/output/e2e-framing-blur/`, NVENC e quality gate aprovados;
- o job `08cbadc6c9884542b88049433476e74b` falhou intencionalmente porque o E2E
  full-resolution foi interrompido para testar a versao otimizada; nao e regressao.

### Proximo gate exato - crop facial estatico

O usuario rejeitou tracking continuo por causar movimento/vertigem. Nao
implementar pan, keyframes, EMA ou camera seguindo o rosto.

Fundacao pronta:

- `src/cortex/render/face_crop.py` resolve um unico crop por intervalo usando
  mediana ponderada por area/confianca, clamp e fallback central;
- o resolvedor aceita `identity_index` + `target_identity_id` confirmado, registra
  provenance e retorna `temporal_motion=false`;
- `tests/test_face_crop.py`: 7 testes passaram.

Ainda falta integrar, nesta ordem:

1. adicionar `face_static_crop` a `RenderFramingSettings` e ao tipo web;
2. estender `RenderRequest` com `face_index_artifact_id`,
   `identity_index_artifact_id` e `target_identity_id` explicitos;
3. validar no API/worker que artifacts, fonte, hashes e identidade confirmada
   pertencem ao mesmo projeto; nunca escolher pessoa silenciosamente;
4. no `RenderService`, resolver um crop fixo por segmento antes do input hash,
   incluir a trajetoria estatica/provenance no cache e manifesto, e aplicar
   `scale=...:force_original_aspect_ratio=increase,crop=w:h:x:y` com x/y constantes;
5. primeira entrega deve rejeitar `camera_edit_plan` + `face_static_crop` ate haver
   FaceIndex por fonte/shot, evitando usar coordenadas da fonte errada;
6. no Studio, expor uma escolha visual e explicita "este sou eu" para identidade
   confirmada. O frontend ainda nao orquestra camera/identity/reaction, embora os
   endpoints backend existam; nao usar apenas o maior rosto como substituto;
7. persistir target, crop por segmento, amostras, fallback e requested/effective
   mode no manifesto; adicionar E2E real sem movimento entre frames.

### Comandos de retomada

```bash
git status --short
git diff --check
.venv/bin/pytest -q tests/test_face_crop.py
.venv/bin/pytest -q tests/test_render.py -k 'camera_plan_v2 or camera_plan_v3 or camera_plan_filtergraph'
.venv/bin/pytest -q tests/test_render_settings.py tests/test_render_quality_report.py
(cd apps/web && npm run build)
(cd apps/remotion && npm run build)
```

Antes de merge/release, executar no host:

```bash
.venv/bin/pytest -q
./scripts/check_gpu.sh
```

### Riscos conhecidos

- a suite agregada pode travar no sandbox em `fastapi.testclient`; nao interpretar
  ausencia de resumo como sucesso. Os testes focados acima concluem normalmente;
- `track_id` do FaceIndex e posicional/local; para garantir "meu rosto", cruzar
  observacoes exatas com `identity_index` confirmado;
- o modo blur ainda custa varios minutos para 70 s em 1080x1920, apesar da
  otimizacao do background. Medir antes de otimizar novamente;
- existem mudancas nao commitadas em backend, frontend, docs e testes. Nao
  restaurar arquivos nem separar partes sem entender a dependencia da fatia.

## Sessao 2026-07-13 - Gates 0 e 2 do PRODUCTION_COMPLETION_PLAN

Orquestracao com lanes implementer/Explore. Resultados validados pelo
orquestrador (suite + builds reexecutados antes de cada commit).

- Gate 0 concluido: suite 201 passed no host, builds web/remotion, diff
  auditado (sem PodCLI/dependencias novas), checkpoint `360f8f5`.
- Gate 1 parcial: cobertura fail-closed + regressao sintetica commitadas
  (`c7e7606`, tests/test_face_static_crop_gate1.py, 10 testes). As
  validacoes cross-project/cross-source/identidade/arquivo ja existiam
  no codigo (o final anterior deste handoff estava desatualizado).
  E2E real no host em andamento (projeto gate1-e2e-face-crop, trecho do
  episodio Prosa Inversa 20; evidencia esperada em
  docs/evidence/gate1-face-crop-e2e.md).
- Gate 2 concluido (`dcbcc19` sessao D, `4dd178e` sessao E): fillers
  deterministicos (analysis v2, lexicon 1.0.0, cache invalidado via
  hash), Curadoria com hook/contexto/payoff/headline/subscores/warnings/
  density/loudness/fillers reais, provenance visivel (incl. fix da
  resposta cacheada sem provenance), limites 15-180/1-25 com boundary
  tests, LocalHeuristicProvider opt-in (request.provider ou
  ai.enable_local_heuristic_fallback) com provenance mode=heuristic.
- Proximo: fechar Gate 1 (E2E + docs) e iniciar Gate 3 com o gap report
  ja levantado (falta: tempo por identidade/role no camera plan, politica
  versionada de diversidade, piso de confianca para reaction, bloqueador
  por-shot e diagnostico na UI; audio editorial ja correto por construcao).

### Gate 3 concluido (mesma sessao)

Diversidade single-source implementada e testada (236 passed): politica
versionada em src/cortex/edit/diversity_policy.py (v1.0.0, no input_hash),
camera plan schema v4 + algorithm 4.0.0 com seconds_by_identity/role e
dominant_identity_share, gatilho de monotonia (>=85%, >=20s) alem do
fallback, piso de confianca 0.55, share max 0.2, espacamento 15s,
bloqueadores tipados por-shot (REACTION_BLOCKER_*), preservacao de
aparicoes naturais, e CameraPlanDiagnosticsPanel (leitura) no Studio.
Render aceita schema v4 (whitelist {2,3,4}). Pendente para sessao futura
do Studio: orquestrar criacao do camera plan pela UI.

### Gate 1 concluido (mesma sessao)

E2E real no host executado com API+worker de producao sobre trecho de 4 min
do episodio Prosa Inversa 20 (projeto gate1-e2e-face-crop): cadeia completa
succeeded, 3 identidades confirmed, render NVENC 1080x1920 com manifesto v7
(static_face_crops com samples/fallback/temporal_motion=false), SRT, quality
gate passed/publish_ready, frames comprovando ausencia de movimento, e
rejeicao fail-closed de camera_edit_plan+face_static_crop observada ao vivo.
Evidencia: docs/evidence/gate1-face-crop-e2e.md. Risco documentado: crop e
extrapolado em segmentos que cruzam cortes de cena sem amostras da identidade
(mitigado por fail-closed com camera_edit_plan; indice por fonte/shot fica
para depois). Roadmap atualizado.

### Gate 8 concluido (mesma sessao)

Dataset versionado em eval/dataset (schema draft 2020-12, sem midia privada,
1a entrada real prosa-inversa-20.json com 15 clips pending_human_review),
runner offline deterministico em src/cortex/eval/runner.py + CLI
scripts/eval_selection.py (--check com exit code), baseline/thresholds em
eval/baseline.json, antes/depois identificavel por input_hash+prompt_sha256+
provider do provenance. 7 testes. Pendente: veredito humano real para ativar
os gates de cobertura/rejected no baseline.

### Gate 4 concluido (mesma sessao)

J/L-cut reais em duas entregas: sessao G (EDL v2, resolve_jl_cuts com
boundaries seguros e invariante A/V fail-closed, opt-in via request/config,
commit 102efa2) e sessao H (renderer com trims independentes video_/audio_,
micro-crossfade fixo 20ms anti-click na juncao j/l, RENDER_SCHEMA_VERSION 8
com RenderTransitionInfo requested/effective, toggle J/L + chips com offset
na Curadoria e regressao tests/test_render_jl_cut.py provando por pixel e
frequencia que audio e video trocam em instantes distintos; cancelar J/L
restaura filtergraph byte-identico ao pre-Gate 4). Lacuna consciente: teste
de encadeamento job->job com jl_cut=true via worker (cobertura funcional
equivalente existe via service direto).
