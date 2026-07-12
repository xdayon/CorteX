# Roadmap de consolidacao

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
- filler preview e loudness na Curadoria;
- [x] render real curto multi-segmento, crossfades e regressao automatizada;
- [x] loudness final -14 LUFS, true peak, preview e download do artifact renderizado;
- [x] captions/headline FFmpeg reais, sidecar SRT persistido e download seguro;
- [x] configuracao efetiva Studio -> job -> manifesto, sem controles simulados;
- [x] gate deterministico inicial de frames pretos e congelados;
- quality gate audiovisual completo e relatorio de publicacao.

## Fase 3 - Selecao e curadoria

- [x] prompts PT-BR versionados e saida JSON estruturada;
- [x] Codex CLI primario e Claude CLI fallback, com timeout, cancelamento e provenance;
- [x] job, cache por hash, endpoint e curadoria alimentada pela resposta real;
- duracao 15-180 s e 1-25 sugestoes;
- hook, contexto, payoff, headline e explicacao do score;
- fallback local identificado como heuristico;
- conjunto de avaliacao com cortes aprovados/reprovados.

## Fase 4 - Visual e render

- [x] composicoes Remotion proprias para legenda e headline;
- [x] primeira composicao persistida de captions/headline via FFmpeg/libass/drawtext;
- [x] editor completo de tipografia, karaoke, outline, sombra e animacao;
- [x] canvas, fonte, tamanho, outline, headline, encoder e SRT efetivos no Studio;
- [x] template `Editorial Quote` baseado na referencia do usuario;
- [x] 9:16, 1:1 e 16:9; H.264/H.265, CQ, FPS, AAC e SRT;
- [x] overrides por corte;
- presets salvos (nomeados, persistidos e reaplicaveis) - ainda nao implementado;
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
  x, sem embeddings - campo `embedding` reservado para upgrade ArcFace
  futuro); `face_index` ainda NAO participa da EDL (gate futuro);
- [x] Gate 3: `speaker_timeline` visual conservadora, cacheada por hash e
  derivada explicitamente de fonte + `scene_index` + `face_index` + VAD;
  mouth motion CPU em frames FFmpeg streaming/coalescidos, com baseline robusta,
  estados `speaker`/`unknown`/`overlap`/`no_speech` e sem alegar identidade global
  quando o slot heuristico por posicao nao e suficiente;
- [x] Gate 4a: `camera_timeline` deterministica por cena, alinhando plano,
  tracks visiveis e speaker dominante em roles conservadores (`speaker_close`,
  `two_shot`, `wide`, `no_face`, `unknown`), com `layout_id` explicitamente
  limitado ao layout/slot e sem alegar identidade fisica cross-camera;
- continuidade de identidade cross-camera confirmada;
- reaction shots semanticamente coerentes;
- J-cut, L-cut e punch-in editaveis;
- deteccao de frames pretos, congelados ou borrados (alem do gate visual ja
  existente no render).

## Fase 6 - Producao

- E2E com episodio real e benchmark CPU/GPU;
- retomada apos interrupcao e fila de 25 cortes;
- relatorio de QA e comparacao antes/depois;
- remocao definitiva de `mods/`, `reference/` e `apply.sh` apos paridade.

## Fora de escopo

Thumbnail, Content Studio, knowledge base, analytics generico, auto-update do
PodCLI e qualquer pagina que nao participe diretamente da producao dos cortes.
