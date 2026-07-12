# CorteX - Handoff da proxima sessao

Data: 2026-07-12
Repositorio: `/var/home/dx/Projects/CorteX`
Branch: `feature/audio-aware-editing`

## Leia somente isto primeiro

1. `AGENTS.md`
2. `docs/HANDOFF_NEXT_SESSION.md`
3. Consulte `docs/HANDOFF_FABLE_5.md` apenas se faltar contexto historico.

Nao reverta arquivos nao commitados. Todo o scaffold standalone atual ainda esta
no worktree e deve ser preservado.

## Estado verificado

- P0 ingestao/upload/YouTube, jobs SQLite/SSE e transcricao faster-whisper estao
  implementados.
- Selecao editorial possui job real, cache por hash, JSON Schema, provenance e
  frontend conectado.
- Provider oficial: Codex CLI `gpt-5.5`, reasoning `medium`.
- Fallback: Claude CLI, sempre registrado como fallback efetivo e com motivo.
- Codex roda efemero, read-only, em diretorio temporario vazio e sem API key.
- O schema editorial completo passou no Codex `gpt-5.5/medium` em cerca de 6 s,
  com fallback desativado.
- P1 `AnalysisArtifact` esta implementado: WAV normalizado reutilizado, Silero
  VAD ONNX, waveform peaks/RMS 512/4096, pausas, speech density, loudness/true
  peak e room tone, com job/cancelamento/cache/endpoints.
- A Curadoria usa transcript, waveform e VAD reais recortados pelo corte ativo.
- A selecao exige o AnalysisArtifact e recebe somente contexto compacto, nunca
  arrays de waveform.
- P2 `EditPlanDocument` (EDL) esta implementado em `src/cortex/edit/`: perfis
  de ritmo (dynamic/balanced/contemplative), `EnergyTrack`/`quiet_boundary`
  derivados do `WaveformResolution` de maior densidade ja persistido no
  AnalysisArtifact (sem redecodificar audio/ffmpeg), planner multi-segmento
  (`plan_safe_segments`/`protect_segment_boundaries`), quality gate corrigido
  (`validate_edit_plan`) e `EditPlanService` com cache por input_hash. Job
  `JobType.EDIT_PLAN` no worker com progresso/cancelamento e endpoints
  `POST/GET /projects/{id}/edit-plans` na API.
- Correcao do bug conhecido do quality gate: uma fronteira que cai dentro de
  palavra nao aborta mais o plano. Ela e re-ancorada para o gap seguro mais
  proximo (fora de palavra e fora de VAD) dentro de 2x `boundary_radius` do
  perfil, registrando `severity=warning code=boundary_snapped`. Se nao houver
  ponto seguro, o plano degrada para o clipe integral sem cortes internos
  (`code=boundary_unsafe_degraded`), sempre com `passed=true` — o job nunca
  falha por causa disso. `validate_render_file` (verificacao pos-render) nao
  entrou nesta fatia.
- Ultima suite completa no host: 62 testes passaram (55 anteriores + 7 novos
  em `tests/test_edit_plan.py`, cobrindo pausa longa, pausa retorica
  preservada, snap de fronteira, degradacao sem ponto seguro, plano
  multi-segmento, cache por hash e o par de endpoints POST/GET).
- Build TypeScript/Vite (nao reexecutado nesta sessao — so tocamos backend
  Python), Ruff (`src/cortex tests`) e `git diff --check` passaram.
- Studio: `http://127.0.0.1:5173`
- Swagger: `http://127.0.0.1:8787/docs`

Arquivos centrais desta fatia:

- `src/cortex/analyze/audio.py`
- `src/cortex/analyze/schemas.py`
- `src/cortex/analyze/service.py`
- `tests/test_analysis.py`
- `apps/web/src/App.tsx`
- `src/cortex/suggest/provider.py`
- `src/cortex/suggest/service.py`
- `prompts/clip_selection.schema.json`
- `config/cortex.yaml`
- `docs/AI_PROVIDERS.md`

Arquivos centrais da fatia de EDL/boundary optimizer (esta sessao):

- `src/cortex/edit/schemas.py` (EditPlanDocument, EditSegment, EditQualityReport)
- `src/cortex/edit/profiles.py` (EditingProfile, PROFILES, resolve_profile)
- `src/cortex/edit/boundary.py` (EnergyTrack, energy_track_from_analysis)
- `src/cortex/edit/planner.py` (plan_safe_segments, protect_segment_boundaries, timeline_duration)
- `src/cortex/edit/quality.py` (validate_edit_plan com a correcao boundary_snapped)
- `src/cortex/edit/service.py` (EditPlanService, cache por input_hash)
- `src/cortex/worker.py` (run_edit_plan_job, JobType.EDIT_PLAN)
- `src/cortex/api.py` (POST/GET /projects/{id}/edit-plans)
- `tests/test_edit_plan.py`

## Estado do gate real

O episodio local `episodio-teste.mp4` produziu o artifact
`ed35de3964374ebab512c01525a5344f` com 9,1022 s, 2 intervalos Silero, 2 pausas,
waveforms 512/4096, 93,691% de speech density, -20,35 LUFS, -1,55 dBFS true peak
e 2 amostras de room tone. O WAV cacheado foi reutilizado.

Ainda falta validar visualmente no navegador um upload real percorrendo o fluxo
completo. Nao alegar gate browser concluido sem essa verificacao host.

## Proxima fatia vertical

1. Integrar a EDL (`EditPlanDocument`) na Curadoria/preview: chamar
   `POST /projects/{id}/edit-plans` a partir do corte selecionado, mostrar os
   segmentos/transitions no PhonePreview e deixar o usuario revisar
   `quality.issues` (inclusive os avisos `boundary_snapped` e
   `boundary_unsafe_degraded`) antes de mandar renderizar.
2. Render curto real: consumir os `segments` da EDL (multi-segmento, com
   `transition.duration` como crossfade) no pipeline de render, validar que a
   duracao final bate com `timeline_duration_seconds` do documento.
3. Fazer o gate browser real do fluxo Curadoria -> EDL -> preview.
4. Reexecutar `.venv/bin/pytest -q` no host apos qualquer mudanca de render.

## Regras de trabalho

- Use `gpt-5.4-mini/low` apenas para subtarefas mecanicas e bem delimitadas.
- O orquestrador faz arquitetura, integracao e revisao final.
- Analise editorial do produto usa `gpt-5.5/medium`; nao reduzir esse modelo.
- Nao enviar waveform bruto ou JSON redundante para LLM. Produzir contexto
  compacto a partir dos artifacts locais.
- Nao chamar render, boundary optimizer ou AnalysisArtifact de prontos sem
  artifact real e teste.
- Nao usar fallback silencioso entre engines.

## Comandos de validacao

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src/cortex tests
cd apps/web && npm run build
git diff --check
```

FastAPI `TestClient` pode travar no sandbox/Python 3.14; nesse caso rode a suite
no host com permissao elevada, como nas sessoes anteriores.

## Prompt para a nova sessao

```text
Continue o CorteX em /var/home/dx/Projects/CorteX. Leia primeiro AGENTS.md e
docs/HANDOFF_NEXT_SESSION.md; use docs/HANDOFF_FABLE_5.md apenas para contexto
historico necessario. Preserve todo o worktree nao commitado. Trabalhe como
orquestrador eficiente e delegue somente subtarefas pequenas com real valor de
paralelismo. Valide primeiro a suite completa no host e o fluxo browser real da
Curadoria. O motor de edicao (EDL) ja esta portado em `src/cortex/edit/` com
job/endpoints/cache/testes; a proxima fatia e integrar essa EDL na
Curadoria/preview (mostrar segments/transitions e quality.issues no
PhonePreview) e ligar um render curto real que consuma os segmentos
multi-segmento da EDL. Mantenha Codex CLI gpt-5.5/medium como provider
editorial primario e Claude CLI como fallback explicito.
```
