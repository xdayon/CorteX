# CorteX

Editor local, GPU-first e retomavel da HiTechX para transformar episodios de
podcast em cortes profissionais para Reels e TikTok.

> Estado: MVP funcional para ingestao, transcricao, analise, selecao, EDL e
> exportacao persistida de cortes. O Studio produz MP4/SRT, preview/download e
> presets por projeto. Ainda nao e um release de producao: faltam validacao E2E
> com episodios reais, benchmark GPU/NVENC, retomada de fila e os gates listados
> no roadmap.

## Produto

O fluxo do CorteX e deliberadamente explicito:

1. selecione um arquivo local ou link do YouTube;
2. configure e clique em **Iniciar transcricao**;
3. defina tema, duracoes e quantidade de sugestoes;
4. revise transcript, waveform, narrativa, headline e plano de camera;
5. configure legenda, headline, ritmo e output;
6. clique em **Iniciar renderizacao** e acompanhe a fila.

Selecionar um arquivo nunca inicia a transcricao. Alterar um preset nunca inicia
o render.

## Estrutura atual

```text
apps/web/             Studio React/TypeScript da HiTechX
src/cortex/           configuracao, contratos, jobs, telemetria e API
config/cortex.yaml    defaults do runtime, GPU-first e sem fallback silencioso
presets/neat90.yaml   preset autocontido de conteudo, visual e output
prompts/              prompt editorial PT-BR e JSON Schema da resposta
docs/                 arquitetura, motor de edicao e roadmap
mods/                  implementacao legada usada como referencia de migracao
tests/                 testes da fundacao e do motor legado validado
```

`mods/`, `reference/` e `apply.sh` nao sao mais a arquitetura alvo. Permanecem
temporariamente para portar os ativos testados sem perder o baseline.

## Backend

Requer Python 3.11 a 3.14. Python 3.12 e o alvo recomendado para compatibilidade
com CUDA e bibliotecas de ML.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev,transcription,youtube]'
.venv/bin/cortex-api
```

A API local fica em `http://127.0.0.1:8787`:

- `GET /api/v1/health`
- `GET /api/v1/hardware`
- `POST /api/v1/jobs`
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/{id}/events` (SSE)
- `POST /api/v1/jobs/{id}/cancel`
- `POST /api/v1/projects/{id}/renders`
- `GET /api/v1/projects/{id}/renders/{artifact_id}`
- `GET /api/v1/projects/{id}/renders/{artifact_id}/media`
- `POST /api/v1/projects/{id}/analyze`
- `GET /api/v1/projects/{id}/analysis/{artifact_id}`
- `GET /api/v1/projects/{id}/transcripts/{artifact_id}`

O worker local processa ingestao YouTube, transcricao faster-whisper, analise
Silero/waveform/loudness, selecao editorial via Codex CLI, planejamento de EDL e
render FFmpeg multi-segmento. O caminho padrao usa
a assinatura ChatGPT configurada
no `codex`, sem exigir API key. Claude CLI e o fallback configurado; a troca fica
explicita na provenance do artifact. Limites dos planos continuam valendo.
O render persiste MP4 e manifesto com streams/duracao/loudness validados, gera um
overlay VP9 alpha cacheavel via Remotion para headline e karaoke palavra por palavra,
compoe o overlay no master FFmpeg e publica sidecar SRT. Node, Remotion, hash do
overlay e configuracoes efetivas ficam registrados no manifesto.
As configuracoes suportadas do Studio sao persistidas no job/manifesto, e o gate
visual inicial reprova trechos pretos ou congelados acima dos thresholds configurados.

Detalhes e diagnostico: [Providers de IA](docs/AI_PROVIDERS.md).

## Frontend

```bash
cd apps/web
npm install
npm run dev
```

Instale tambem o compositor com as versoes fixadas no lockfile:

```bash
cd apps/remotion
npm install
npm run build
```

`render.remotion_browser` deve apontar explicitamente para um
`chrome-headless-shell` executavel. O job nao baixa navegador nem retorna
silenciosamente ao renderer legado.

Depois da primeira instalacao, backend e frontend podem ser iniciados juntos:

```bash
./scripts/dev.sh
```

Abra `http://127.0.0.1:5173`. A documentacao interativa da API fica em
`http://127.0.0.1:8787/docs`.

O Vite encaminha `/api` para `127.0.0.1:8787`. O Studio possui seis etapas,
preview limpo/TikTok/Reels, editor de legenda/headline e Compute Deck. Enquanto a
API nao esta ativa, a UI deixa isso visivel como modo local; dados de exemplo nao
devem ser interpretados como telemetria real.

## Validacao

```bash
.venv/bin/pytest -q
.venv/bin/python -m compileall -q src/cortex
cd apps/web && npm run build
cd apps/remotion && npm run build
```

O backend padrao pula apenas os testes que exigem browser real. No host, execute
a mesma suite com Remotion habilitado explicitamente:

```bash
CORTEX_RUN_REMOTION_E2E=1 .venv/bin/pytest -q
```

Os testes cobrem configuracao, jobs, protecao de pausas, waveform/VAD, fronteiras,
J/L-cut, reaction reuse single-source, punch-in, camera planning, render FFmpeg,
overlay alpha Remotion, karaoke, cache e quality gate audiovisual. A matriz E2E
CPU/GPU completa do Gate 9 continua separada da suite automatizada.

## GPU

O default solicita faster-whisper `large-v3-turbo` em CUDA `int8`, decode NVDEC e
encode `h264_nvenc`. `allow_cpu_fallback` e `false`: uma falha de GPU deve aparecer
no job, nao virar processamento em CPU silenciosamente.

A existencia de um encoder na listagem do FFmpeg nao garante que o driver esta
funcional. O CorteX deve registrar sempre engine solicitada e engine efetiva.

Para testar driver e um encode H.264 NVENC real de um segundo:

```bash
./scripts/check_gpu.sh
```

## Documentacao

- [Arquitetura](docs/ARCHITECTURE.md)
- [Motor de edicao](docs/EDITING_ENGINE.md)
- [Roadmap](docs/ROADMAP.md)
