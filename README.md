# CorteX

Editor local, GPU-first e retomavel da HiTechX para transformar episodios de
podcast em cortes profissionais para Reels e TikTok.

> Estado: P0 de ingestao e transcricao standalone concluido. Upload/YouTube,
> worker local, faster-whisper CUDA, artifacts, SSE e Studio possuem fonte
> propria neste repositorio. A selecao editorial ja possui job real via Codex
> CLI autenticado pela assinatura, fallback Claude, JSON Schema, cache e
> provenance. Analise
> audiovisual, edicao e render ainda estao sendo portados e nao devem ser
> considerados prontos para producao.

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
O render inicial persiste MP4 e manifesto com streams/duracao validados; captions,
headline Remotion e quality gate audiovisual completo ainda nao estao conectados.

Detalhes e diagnostico: [Providers de IA](docs/AI_PROVIDERS.md).

## Frontend

```bash
cd apps/web
npm install
npm run dev
```

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
python3 -m unittest discover -s tests -v
python3 -m compileall -q src
python3 -m json.tool prompts/clip_selection.schema.json >/dev/null
```

Os testes atuais cobrem configuracao, jobs, ausencia explicita de GPU, protecao
de pausas, snapping na waveform, VAD, fronteiras de palavras, headline ASS e um
render FFmpeg curto com audio e video. Ainda faltam gates E2E de CUDA, NVENC,
Remotion, diarizacao, J/L-cut e planejamento de cameras.

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
