# Plano de conclusao para producao - CorteX

Data-base: 2026-07-13

Atualizacao 2026-09-07: legado PodCLI e frontend inacessivel removidos. O fluxo
atual tem tres telas; processamento ate revisao e persistido. A fila completa de
exportacao ainda depende do navegador. Os gates abaixo descrevem a entrega
historica e pendencias de producao; consulte o ROADMAP para o estado atual.

Este documento organiza o trabalho restante ate um release de producao. Ele nao
substitui `docs/ROADMAP.md`: o roadmap continua sendo o contrato de produto, e
este arquivo define a ordem operacional, os gates e as evidencias exigidas.

## Definicao de pronto

Uma funcao so pode ser marcada como concluida quando:

1. produz artifact real, persistido, versionado e cacheado quando deterministica;
2. registra requested/effective engines, inputs, hashes e provenance;
3. possui teste focado do caminho feliz e das precondicoes fail-closed;
4. esta ligada ao Studio sem controles simulados;
5. passa `git diff --check`, testes focados e builds afetados;
6. recebe validacao de host quando depende de GPU, NVENC, browser ou midia real;
7. atualiza `docs/ROADMAP.md` e o final de `docs/HANDOFF_FABLE_5.md`.

Nao confundir implementacao isolada, mock de UI ou teste sintetico com validacao
de producao.

## Ordem obrigatoria

### Gate 0 - Baseline e checkpoint do worktree

Objetivo: criar uma base recuperavel antes de abrir novas frentes.

Escopo:

- revisar os arquivos modificados e untracked existentes;
- confirmar que nao ha codigo PodCLI restaurado nem dependencias legadas novas;
- executar testes focados atuais, builds web/Remotion e sanity checks;
- executar a suite completa no host;
- criar um commit de checkpoint coerente somente apos revisao do diff.

Aceite:

- `.venv/bin/pytest -q` conclui no host;
- `cd apps/web && npm run build` passa;
- `cd apps/remotion && npm run build` passa;
- `.venv/bin/python -m compileall -q src/cortex` passa;
- `git diff --check` passa;
- nenhuma mudanca do usuario foi descartada.

Risco: o worktree atual contem uma entrega grande ainda sem checkpoint. Este gate
vem antes de refactors ou remocao de arquivos.

### Gate 1 - Fechar crop facial estatico

Objetivo: concluir o item ja iniciado sem tracking ou movimento continuo.

Escopo:

- revisar a cadeia Studio -> speaker -> camera -> identity -> render;
- adicionar testes HTTP/worker para artifacts cross-project, cross-source,
  identidade ambigua e arquivo ausente;
- adicionar regressao de render real com `face_static_crop`;
- verificar por frames que `crop_x` e `crop_y` permanecem constantes dentro de
  cada segmento;
- verificar fallback central auditavel quando a identidade nao aparece no
  intervalo;
- validar preset e override por corte;
- executar E2E em trecho real e inspecionar vertigem/movimento.

Aceite:

- manifesto v7 persiste target, samples, fallback e `temporal_motion=false`;
- nenhuma identidade e escolhida silenciosamente;
- combinacao com `camera_edit_plan` continua fail-closed enquanto nao houver
  indice por fonte/shot;
- MP4 real, manifesto e SRT sao publicados com quality gate aprovado;
- item de crop facial e marcado como concluido no roadmap.

### Gate 2 - Fechar pendencias de Curadoria e selecao

Objetivo: eliminar lacunas funcionais das Fases 2 e 3 antes de editar o motor A/V.

Escopo:

- mostrar filler words removiveis/ocultaveis na Curadoria;
- mostrar loudness, speech density e avisos relevantes por corte;
- garantir limites 15-180 segundos e 1-25 sugestoes no schema, API, provider,
  service e UI;
- exibir hook, contexto, payoff, headline, reasoning e score completo;
- implementar fallback local heuristico somente se explicitamente solicitado ou
  configurado, sempre com provider/effective mode identificados;
- impedir que fallback heuristico seja apresentado como resposta de LLM.

Aceite:

- todos os dados exibidos provem de artifacts persistidos;
- validacoes de limites possuem testes de boundary;
- provenance registra o `codex_cli`; nao existe troca silenciosa de motor editorial;
- Curadoria nao contem waveform, score, filler ou loudness ilustrativos.

### Gate 3 - Regra de diversidade single-source

Objetivo: evitar cortes visualmente monotematicos quando existe reaction segura.

Escopo:

- definir politica versionada de diversidade por duracao e disponibilidade;
- medir tempo por identidade/role no camera plan;
- inserir reaction apenas a partir de `reaction_candidate_index` aprovado;
- preservar aparicoes naturais antes de reutilizar video de outro instante;
- bloquear fabricacao de reaction em baixa confianca;
- expor diagnostico e motivo de fallback na Curadoria/Studio.

Aceite:

- planner evita somente-convidado quando ha alternativa segura;
- sem candidato seguro, mantem a fonte e registra o bloqueador;
- audio editorial permanece continuo e o audio da reaction permanece mutado;
- testes cobrem candidato disponivel, baixa confianca e limite de reutilizacao.

### Gate 4 - Timelines A/V independentes: J-cut e L-cut

Objetivo: implementar J-cut e L-cut reais, nao crossfades simultaneos renomeados.

Escopo:

- versionar EDL com relogios de audio e video independentes;
- resolver leads/tails apenas em boundaries seguros de palavra, VAD e waveform;
- limitar offsets pelo perfil e pela configuracao do usuario;
- preservar cobertura de audio sem gaps, overlap indevido ou troca de fonte;
- renderizar trims independentes no FFmpeg;
- mostrar offsets e transicoes na Curadoria e permitir editar/desabilitar;
- registrar requested/effective transition no manifesto.

Aceite:

- regressao sintetica comprova por pixels e frequencia que audio e video trocam
  em instantes diferentes;
- sync base permanece dentro do limite, exceto offset editorial declarado;
- cancelar J/L-cut restaura hard cut/crossfade deterministico;
- nenhuma fronteira invade palavra ou VAD protegido.

### Gate 5 - Punch-in editavel e seguro

Objetivo: mascarar jump cuts sem introduzir movimento de camera desconfortavel.

Escopo:

- adicionar punch-in estatico por shot/segmento, com escala e ancora persistidas;
- permitir alternancia deterministica em jump cuts;
- integrar com crop central e, quando seguro, crop facial estatico;
- validar safe zones e resolucao efetiva apos scale/crop;
- expor controle global e override por corte.

Aceite:

- escala solicitada/efetiva aparece no manifesto;
- nao ha keyframes, pans ou tracking implicito;
- quality gate detecta crop invalido e violacao de safe zone;
- testes reais verificam dimensoes, pixels e estabilidade temporal.

### Gate 6 - Remover ramo experimental ISO/multicam

Objetivo: manter apenas a arquitetura single-source decidida para o produto.

Escopo:

- portar para testes single-source qualquer regressao util de continuidade A/V;
- remover schemas, services, jobs, endpoints, config e caminhos de render de
  `multicam_sync`, `multicam_visual_index` e `iso_synced`;
- remover tipos e caminhos v2 que existam apenas para ISO;
- atualizar docs e manifests compativeis ou fornecer erro de versao claro.

Aceite:

- `rg` nao encontra rotas/runtime multicam fora de migracao/documentacao historica;
- testes de reaction e continuidade single-source continuam passando;
- nenhum artifact novo anuncia suporte ISO.

Este gate deve ocorrer depois que J/L-cut e reactions tiverem regressao propria,
para nao perder cobertura util durante a remocao.

### Gate 7 - Quality gate audiovisual completo

Objetivo: bloquear publicacao de arquivos tecnicamente defeituosos.

Escopo:

- validar sync A/V e offsets editoriais declarados;
- detectar clicks, saltos de waveform, clipping, audio ausente e canais invalidos;
- verificar discontinuidades de PTS/DTS;
- decodificar o arquivo integralmente;
- verificar `faststart`/localizacao de `moov`;
- consolidar preto, freeze, loudness, true peak, captions e safe zones;
- persistir relatorio de publicacao com severidade, evidencias e thresholds;
- distinguir warning de falha bloqueante.

Aceite:

- fixtures defeituosos falham pelo codigo esperado;
- arquivo aprovado decodifica integralmente e fica `publish_ready=true`;
- UI mostra cada gate e nao apenas um booleano agregado;
- relatorio e servivel/downloadable junto do render.

### Gate 8 - Conjunto de avaliacao editorial

Objetivo: medir qualidade da selecao e evitar regressao de prompts/providers.

Escopo:

- criar dataset versionado com trechos aprovados e reprovados;
- registrar tema, duracao, hook, payoff, speakers e motivo da decisao humana;
- criar runner offline que compare sugestoes com o conjunto esperado;
- medir cobertura, overlap, duracao, completude e seguranca de boundaries;
- nao armazenar midia privada no Git; usar manifests/hashes e fixtures permitidos.

Aceite:

- comando unico produz relatorio JSON persistido;
- baseline e thresholds ficam versionados;
- alteracao de prompt/provider pode ser comparada antes/depois.

### Gate 9 - E2E real, benchmark e relatorio antes/depois

Objetivo: validar o produto no hardware alvo com episodio representativo.

Matriz minima:

- arquivo local e YouTube;
- episodio longo e trecho curto;
- `large-v3-turbo` CUDA `int8`;
- 1, 10 e 25 cortes na fila;
- vertical crop, blurred background e face static crop;
- captions/karaoke/headline on/off;
- reaction, J-cut, L-cut e punch-in quando aplicaveis;
- NVENC e caminho CPU explicitamente solicitado;
- cancelamento, retomada e cache hit.

Medidas:

- tempo por stage e tempo total;
- pico de VRAM/RAM, utilizacao GPU/CPU e tamanho dos artifacts;
- velocidade de render relativa a duracao;
- falhas/retries/cache hits;
- comparacao antes/depois editorial e tecnica.

Aceite:

- todos os artifacts e relatorios ficam persistidos;
- `./scripts/check_gpu.sh` passa no host;
- nenhuma troca silenciosa de engine;
- relatorio final lista configuracao, resultados e riscos remanescentes.

### Gate 10 - Limpeza de legado e release candidate

Objetivo: validar release candidate com runtime e testes standalone.

Escopo:

- impedir reintroducao de imports e dependencias do legado removido;
- portar qualquer teste/logica ainda necessaria;
- remover arquivos, imports, docs e dependencias mortas;
- revisar README, arquitetura, editing engine, roadmap e handoff;
- revisar seguranca de paths, subprocessos, cancelamento e IO limitado;
- executar suite e matriz final de release.

Aceite:

- legado removido sem reduzir cobertura;
- instalacao limpa reproduz backend e frontends pelos lockfiles;
- nenhum controle simulado ou claim de engine nao validada;
- worktree limpo e release candidate etiquetavel.

## Sessoes recomendadas

Cada nova sessao deve assumir um unico gate principal:

1. Sessao A: Gate 0, baseline e checkpoint.
2. Sessao B: Gate 1, testes HTTP/worker e render sintetico do crop facial.
3. Sessao C: Gate 1, E2E real do crop facial e fechamento documental.
4. Sessao D: Gate 2, Curadoria e limites editoriais.
5. Sessao E: Gate 2, fallback heuristico e provenance.
6. Sessao F: Gate 3, diversidade e diagnostics.
7. Sessoes G-H: Gate 4, schema/planner e depois renderer/UI.
8. Sessao I: Gate 5, punch-in.
9. Sessao J: Gate 6, remocao ISO.
10. Sessoes K-L: Gate 7, audio/sync e depois container/relatorio/UI.
11. Sessao M: Gate 8, dataset e runner.
12. Sessoes N-O: Gate 9, E2E/benchmark e correcoes encontradas.
13. Sessao P: Gate 10, limpeza e release candidate.

Dividir uma sessao quando o gate envolver arquivos concorrentes ou validacao de
host longa. Nunca iniciar o gate seguinte para contornar uma falha do atual.

## Protocolo para iniciar nova sessao

Use este prompt, substituindo `<GATE>`:

```text
Continue o CorteX em /var/home/dx/Projects/CorteX pelo <GATE> de
docs/PRODUCTION_COMPLETION_PLAN.md. Leia AGENTS.md, o plano desse gate, o final de
docs/HANDOFF_FABLE_5.md e os itens relacionados em docs/ROADMAP.md. Preserve todo
o worktree. Audite git status/diff e rode primeiro os testes focados existentes.
Implemente somente o escopo do gate, com artifact persistido e fail-closed.
Finalize com testes, builds afetados, git diff --check e atualizacao do roadmap e
handoff. Nao marque GPU/NVENC/browser/E2E como validado sem executar no host.
```

## Checklist de encerramento de sessao

- registrar arquivos e contratos alterados;
- registrar comandos executados e resultados reais;
- listar o que nao foi executado e por que;
- atualizar checkbox apenas se o aceite completo passou;
- escrever no handoff o proximo gate exato, sem recontar todo o repositorio;
- confirmar `git status --short` e preservar mudancas nao relacionadas;
- criar checkpoint apenas quando solicitado ou previsto pelo Gate 0/10.

## Comandos padrao

Sandbox/focados:

```bash
.venv/bin/pytest -q <testes-do-gate>
.venv/bin/python -m compileall -q src/cortex
(cd apps/web && npm run build)
(cd apps/remotion && npm run build)
git diff --check
```

Host/release:

```bash
.venv/bin/pytest -q
./scripts/check_gpu.sh
```

Testes de host devem ser usados para qualquer claim sobre CUDA, NVENC, browser,
performance ou episodio real.
