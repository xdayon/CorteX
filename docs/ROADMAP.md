# CorteX — contrato de entrega

Atualizado em 2026-09-11. Objetivo: editor pessoal confiável para Dayon News,
master único, processamento local, revisão humana e publicação manual.
Gates anteriores estão em [history](history/ROADMAP-before-2026-09-10.md).

## Entregue nesta consolidação

- [x] preservar limpeza anterior de PodCLI e motores standalone;
- [x] headline manual por corte, fallback para sugestão IA e mesmo texto na prévia/export;
- [x] autosave versionado de ajustes no navegador e isolamento entre episódios;
- [x] seek de entrada no render com clocks A/V, reactions, cache e regressão por pixels/áudio;
- [x] verificar voz no envelope e nos trechos editoriais sem duplicar tempos;
- [x] versões frontend fixadas, dependências de build fora das dependências runtime;
- [x] Studio compilado na API local, serviço Linux e atalho;
- [x] mapa de arquitetura, handoff curto, diagnóstico e proposta Cloudflare;
- [x] preservar Gaussian blur real, captions karaoke/pop, correção textual e QA existentes.

## P0 — confiabilidade antes de mais efeitos

### 1. Edição e export batch no servidor

- [ ] `ClipRevision` salva seleção, headline, correções, framing e settings no SQLite.
- [ ] `ExportBatch`/itens com snapshot de revisão, estado e resultados persistidos.
- [ ] Um POST idempotente agenda o lote completo; worker serial cria EDL/render.
- [ ] UI apenas observa; GET restaura estado em outro navegador.
- [ ] Job seguinte + avanço de workflow na mesma transação ou reconciliados por chave.
- [ ] Cancelar item/lote, repetir somente falhas e preservar outputs concluídos.

Aceite: enviar três cortes, fechar a aba no primeiro, comprovar os três artefatos;
reiniciar worker durante o segundo sem duplicar resultados nem perder seleção.
Reabrir em outra sessão restaura revisão, falhas e downloads. Teste determinístico
com interrupção entre job e avanço; um teste host de render curto por lote.

### 2. Foco confiável em Dayon

- [ ] Benchmark CPU de diarização com 2 e 3 vozes em amostra curta revisada.
- [ ] Modelo/pesos com revisão fixa no hash; timeout configurável e erro limitado útil.
- [ ] Transcript atribuído por palavra com unknown/overlap explícitos; correção manual.
- [ ] Confirmar vínculo voz ↔ rosto por episódio, sem inferir falante pelo rosto visível.
- [ ] Integrar intenção de `approximate_edl` ao planner com fronteiras determinísticas.
- [ ] Revalidar predominância no plano de áudio final; mostrar motivo quando insuficiente.

Aceite: corpus com Dayon, convidado e entrevistador; revisar atribuição manualmente;
montagem em que envelope favorece Dayon mas EDL não favorece deve ser rejeitada.
Nenhuma associação automática entre vozes de episódios diferentes.

## P1 — custo previsível e revisão profissional

- [ ] Decoder compartilhado por janela para faces/qualidade/speaker, frames reduzidos.
- [ ] Cache por janelas reutilizáveis; acrescentar corte reaproveita a cobertura existente.
- [ ] Limitar amostras de identidade, eliminar materialização O(n²) e permitir cancelamento.
- [ ] Timeout/cancelamento para todas as leituras de decoder e subprocessos de QA.
- [ ] Medir retenção de VRAM do Whisper; descarregar antes de render se o benefício for real.
- [ ] Preview rápido usando fonte real e headline; manter prévia final fiel opcional.
- [ ] Galeria paginada com thumbnails, estado, filtros por tema e indicador de QA.
- [ ] Catálogo resumido sem reconciliar todos os projetos/manifests a cada polling.
- [ ] Exportar presets Dayon News; ajustes por corte e globais com alcance evidente.

Aceite de desempenho: mesmo trecho no começo e no fim de um episódio, mediana de
três runs, cache frio/quente, tempo por estágio, amostras, RAM/VRAM e engines.
Separar transcrição integral (necessária para seleção global) de visão restrita.
Não afirmar speedup sem benchmark comparável persistido.

## P2 — organização e acesso privado

- [ ] Extrair routers de API por domínio preservando contratos; handlers de worker separados.
- [ ] Extrair telas/hooks UI e pacote visual compartilhado, com paridade de preview/export.
- [ ] Lock backend reproduzível + matriz Python, sem atualização simultânea da stack CUDA.
- [ ] Backup/restauração de SQLite e artefatos; retenção de cache com prévia do que será removido.
- [ ] Tunnel/Access após definir hostname/identidade; autenticação cobre API/mídia/SSE.
- [ ] E2E de acesso autorizado/negado, expiração, reconexão e upload/Range no proxy.

## Avaliação editorial contínua

- [ ] Curar exemplos aprovados/reprovados do perfil; qualidade técnica e editorial separadas.
- [ ] Comparar contexto, começo/final de fala, lipsync, naturalidade de câmera e legibilidade.
- [ ] Acompanhar retenção/compartilhamentos reais; score Codex não é promessa de viralização.
- [ ] Matriz com estúdios distintos, fontes YouTube/local, 1/10/25 cortes e pausa/retomada.

## Fora de escopo atual

Câmeras ISO ausentes do master, multicam sintético, clonagem de voz, editor genérico
com dezenas de tracks, plataformas de conteúdo/analytics, cloud GPU paga, APIs
editoriais alternativas, auto-publicação em redes sociais e reconstruir PodCLI.
