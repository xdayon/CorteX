# Roadmap de consolidacao

Ordem operacional, gates de aceite e roteiro para novas sessoes:
`docs/PRODUCTION_COMPLETION_PLAN.md`.

## Fase 0 - Fundacao (concluida)

- manifests Python/Node e configuracao central;
- modelos de dominio, API, jobs persistentes e telemetria;
- frontend CorteX sem paginas herdadas do PodCLI;
- presets versionados e documentacao de migracao.

## Fase 1 - Transcricao autonoma (concluida)

- importar o worker faster-whisper para o ambiente unico;
- arquivo/YouTube, probe, audio normalizado e cache por hash;
- CUDA obrigatoria por padrao, fallback somente com confirmacao/configuracao;
- timestamps por palavra, VAD e progresso granular.

## Fase 2 - Analise local e edicao segura (em andamento)

- [x] persistir VAD real, waveform peaks/RMS, pausas, loudness e room tone;
- [x] expor transcript, waveform, VAD e pausas reais para revisao no Studio;
- [x] job retomavel, cache por hash, endpoints e gate antes da selecao editorial;
- [x] portar boundary optimizer, quality gates e crossfades ja testados;
- [x] EDL propria persistida (`edit_plan`), perfis de ritmo (dynamic/balanced/contemplative);
- [x] integrar EDL resolvida na Curadoria, com segmentos, transicoes e avisos do quality gate;
- [x] filler preview (deteccao deterministica PT-BR persistida no analysis v2) e loudness na Curadoria;
- [x] render real curto multi-segmento, crossfades e regressao automatizada;
- [x] loudness final -14 LUFS, true peak, preview e download do artifact renderizado;
- [x] captions/headline FFmpeg reais, sidecar SRT persistido e download seguro;
- [x] configuracao efetiva Studio -> job -> manifesto, sem controles simulados;
- [x] gate deterministico inicial de frames pretos e congelados;
- [x] quality gate audiovisual completo e relatorio de publicacao (decode
  integral, faststart, PTS/DTS, sync A/V, canais, clipping, waveform, fase,
  loudness, true peak, captions, preto/freeze e safe zones; checks persistidos
  com pass/warning/fail, evidencia, thresholds e download no Studio).

## Fase 3 - Selecao e curadoria

- [x] prompts PT-BR versionados e saida JSON estruturada;
- [x] Codex CLI primario e Claude CLI fallback, com timeout, cancelamento e provenance;
- [x] job, cache por hash, endpoint e curadoria alimentada pela resposta real;
- [x] duracao 15-180 s e 1-25 sugestoes (schema minItems=1/minimum=15, config, API, UI clamp, boundary tests);
- [x] hook, contexto, payoff, headline e explicacao do score exibidos com dados reais na Curadoria;
- [x] fallback local identificado como heuristico (LocalHeuristicProvider opt-in, mode=heuristic, badge "SEM LLM");
- [ ] conjunto de avaliacao com cortes aprovados/reprovados (infraestrutura pronta:
  eval/dataset versionado
  sem midia privada, runner offline scripts/eval_selection.py com relatorio JSON,
  baseline/thresholds versionados e modo --check; decisoes humanas ainda
  pending_human_review ate revisao real).

## Fase 4 - Visual e render

- [x] composicoes Remotion proprias para legenda e headline;
- [x] primeira composicao persistida de captions/headline via FFmpeg/libass/drawtext;
- [x] editor completo de tipografia, karaoke, outline, sombra e animacao;
- [x] canvas, fonte, tamanho, outline, headline, encoder e SRT efetivos no Studio;
- [x] template `Editorial Quote` baseado na referencia do usuario;
- [x] 9:16, 1:1 e 16:9; H.264/H.265, CQ, FPS, AAC e SRT;
- [x] enquadramento efetivo sem barras pretas: crop para preencher o canvas ou
  fonte 16:9 sobre background do proprio video com Gaussian blur;
- [x] overrides por corte;
- [x] presets salvos por projeto (nomeados, persistidos e reaplicaveis no Studio);
- [x] editor visual completo com cores/animacoes efetivas, safe zones (validacao por
  pixels do overlay alpha) e relatorio de publicacao.

## Fase 5 - Inteligencia audiovisual

- [x] Gate 1: indice deterministico de cortes de camera (`scene_index`) via
  `scdet` do FFmpeg, cacheado por hash, com job/endpoints proprios;
- [x] Gate 1: scene snapping opcional na EDL (`edit_plan`), sempre subordinado
  a regra de preservar fala/pausa, com nota `scene_snapped` no quality report;
- [x] Gate 2: indice de faces (`face_index`) via YuNet ONNX (onnxruntime CPU,
  zero deps novas), amostragem de frames por cena/corte/grade uniforme
  (`face_sample_fps`), classificacao heuristica de plano (close/two_shot/
  wide/none) e identidade por slot heuristico (clustering 1-D de centroide
  x); no schema v2 os slots continuam locais, enquanto embeddings SFace sao
  persistidos para o Gate 4c; `face_index` ainda NAO participa da EDL;
- [x] Gate 3: `speaker_timeline` visual conservadora, cacheada por hash e
  derivada explicitamente de fonte + `scene_index` + `face_index` + VAD;
  mouth motion CPU em frames FFmpeg streaming/coalescidos, com baseline robusta,
  estados `speaker`/`unknown`/`overlap`/`no_speech` e sem alegar identidade global
  quando o slot heuristico por posicao nao e suficiente;
- [x] Gate 4a: `camera_timeline` deterministica por cena, alinhando plano,
  tracks visiveis e speaker dominante em roles conservadores (`speaker_close`,
  `two_shot`, `wide`, `no_face`, `unknown`), com `layout_id` explicitamente
  limitado ao layout/slot e sem alegar identidade fisica cross-camera;
- [x] Gate 4b: `visual_quality_index` persistido por frame/cena, com preto,
  blur, congelamento e proxy explicita de oclusao facial por contato com borda;
  decoder CPU solicitado/efetivo, cache por hash e cadeia fonte/cenas/faces;
- [x] Gate 4c: continuidade de identidade entre cortes/layouts via embeddings
  SFace ONNX CPU, complete-link conservador, margem de ambiguidade e confirmacao
  somente quando a mesma identidade aparece em layouts distintos;
- [x] indices de cenas, faces, speaker visual, identidade e qualidade para o
  master unico ja comutado pelo estudio/OBS;
- [x] banco persistido de aparicoes seguras do entrevistador no proprio master
  (`reaction_candidate_index`), com identidade do entrevistador explicitamente
  confirmada, speaker concorrente distinto, mouth motion baixo, qualidade visual,
  duracao suficiente, rejeicoes, cache, confianca e tempos de origem auditaveis;
- [x] reactions em silencio adjacente: `speaker_timeline` v2 persiste mouth motion
  tambem nas margens VAD, sem alterar segmentos de fala, e o banco v2 exige uma
  fala proxima de identidade distinta como referencia auditavel;
- [x] planner single-source que preserva aparicoes naturais do entrevistador dentro
  do corte e, apenas em shots de fallback, permite reutilizacao temporal controlada
  de um reaction seguro do mesmo master, com candidate, tempos visual/editorial,
  confianca e audio primario auditaveis;
- [x] render de reaction shot com video emprestado do mesmo master e audio continuo
  do trecho editorial, sem reutilizar o audio do reaction;
- [x] regra de diversidade: evitar cortes mostrando somente o convidado quando
  existir imagem segura do entrevistador, sem fabricar reaction em baixa confianca
  (diversity_policy v1.0.0: piso de confianca 0.55, share max 0.2, espacamento 15s,
  monotonia >=85% em cortes >=20s; bloqueadores tipados por-shot e diagnostics com
  segundos por identidade/role; painel de leitura no Studio);
- [x] J-cut e L-cut reais com relogios A/V independentes (EDL v2 com
  video_/audio_start/end e invariante de cobertura; offsets resolvidos em
  boundaries seguros de palavra/VAD, limitados por perfil; renderer com
  trims A/V independentes e micro-fade anti-click de 20ms; manifesto v8
  com requested/effective transition; toggle e chips J/L na Curadoria;
  regressao sintetica por pixel e frequencia provando troca em instantes
  distintos);
- [x] punch-in editavel e seguro: zoom estatico por segmento, escala limitada a
  1.5x, alternancia deterministica em jump cuts, integracao com crop central,
  fundo desfocado e crop facial estatico, overrides por corte e manifesto com
  geometria requested/effective; regressao FFmpeg por pixels confirma zoom,
  dimensoes e ausencia de movimento temporal;
- [x] crop facial estatico: enquadrar uma identidade confirmada com posicao fixa por
  segmento, sem tracking ou movimento continuo, e fallback central auditavel
  (manifesto v7 com target/samples/fallback/temporal_motion=false; testes
  fail-closed cross-project/source/identidade/arquivo; E2E real no host com
  NVENC e quality gate aprovado — docs/evidence/gate1-face-crop-e2e.md);
- [x] remover o ramo experimental de fontes ISO (`multicam_sync`,
  `multicam_visual_index` e caminhos v2 associados); camera plan v5 aceita
  somente o master single-source, reactions preservam o audio editorial e os
  testes de continuidade A/V permanecem no renderer.

## Fase 6 - Producao

- [ ] E2E com episodio real e benchmark CPU/GPU (harness offline persistente em
  `scripts/gate9_benchmark.py`, template em `eval/gate9-matrix.example.json`;
  cadeia YouTube longa, CUDA int8, 25 sugestoes e primeiro render NVENC
  publicavel registrados em `docs/evidence/gate9-host-preflight.md`; tres
  renders NVENC adicionais de uma fonte local passaram o gate em 2026-07-15,
  incluindo fim fisico da fonte e loudness em duas passadas; matriz restante
  do host ainda pendente);
- [x] retomada apos interrupcao e fila persistida de ate 25 cortes;
- [ ] relatorio de QA e comparacao antes/depois (Gate 7 e runners prontos;
  relatorio consolidado Gate 9 depende da matriz real);
- [ ] remocao definitiva de `mods/`, `reference/` e `apply.sh` apos paridade.

## Fora de escopo

Thumbnail, Content Studio, knowledge base, analytics generico, auto-update do
PodCLI e qualquer pagina que nao participe diretamente da producao dos cortes.
