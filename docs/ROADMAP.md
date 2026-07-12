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

- composicoes Remotion proprias para legenda e headline;
- editor completo de tipografia, karaoke, outline, sombra e animacao;
- template `Editorial Quote` baseado na referencia do usuario;
- 9:16, 1:1 e 16:9; H.264/H.265, CQ, FPS, AAC e SRT;
- presets salvos e overrides por corte.

## Fase 5 - Inteligencia audiovisual

- deteccao de cenas, faces e planos;
- tracking do interlocutor e timeline de cameras;
- reaction shots semanticamente coerentes;
- J-cut, L-cut, punch-in e scene snapping editaveis;
- deteccao de frames pretos, congelados ou borrados.

## Fase 6 - Producao

- E2E com episodio real e benchmark CPU/GPU;
- retomada apos interrupcao e fila de 25 cortes;
- relatorio de QA e comparacao antes/depois;
- remocao definitiva de `mods/`, `reference/` e `apply.sh` apos paridade.

## Fora de escopo

Thumbnail, Content Studio, knowledge base, analytics generico, auto-update do
PodCLI e qualquer pagina que nao participe diretamente da producao dos cortes.
