# CorteX — estado atual para agentes

Atualizado em 2026-11-10. Histórico anterior em
[history](history/HANDOFF_FABLE_5-before-2026-09-10.md) — não é fila de instruções.

## Produto e restrições

Editor pessoal de cortes virais para @dayon.news (Instagram Reels/TikTok) a partir de
entrevistas em que Dayon é convidado: master único do YouTube ou arquivo local,
processamento local, revisão humana, publicação manual. Uso principal no PC (celular só
não pode quebrar). Fluxo do usuário: escolhe vários cortes bons → exporta → guarda no
Drive → posta depois, marcando cada corte como salvo/postado/descartado. Estilo: corte
seco e dinâmico, legenda animada, manchete, rosto grande com o estúdio visível; sem
barra de progresso no vídeo, capa nunca como 1º frame. Modelos, esforços, durações e
caminhos ficam em `config/cortex.yaml` (fonte da verdade).

## Pipeline atual

```text
YouTube/upload → normaliza → transcreve (faster-whisper CUDA int8, hotwords)
→ analisa áudio → voz do protagonista (perfil de voz, rótulos D/O por frase)
→ seleção Claude (1 chamada/episódio, transcrição em cache) → Studio: cortes integrados
→ editor de estúdio (refino Claude por corte, fila de prévias draft) → plano de edição
→ triangulação automática (inserções) → render libass com quadros exatos por segmento
→ QA técnico → MP4/SRT/manifest
```

Depois da seleção, o worker enfileira sozinho o mapa de câmeras do episódio (com perfil
de voz).

## Entregue em 10/nov (auditoria; branch `audit/2026-11` → `main`)

Backup antes de tudo: tag `pre-audit-2026-11-10`. Evidência e medidas em
[auditoria](evidence/2026-11-10-auditoria.md).

- Removidos: registro de publicação + "Aprendizado do perfil" (0 registros), presets de
  render, 16 rotas sem chamador, código morto. "Postado" é estado manual do corte.
- Corrigidos: nomes próprios por episódio agora retranscrevem; refino da IA (o `$schema`
  2020-12 quebrava o CLI); `watchJob` sem fallback; serviço `failed` a cada parada;
  `update_and_verify.sh` (FFmpeg certo, UI trocada só sem job, `try-restart`); notas v1
  na escala 1–10; padrões de duração vindos de `clips.*`.
- Mais leve: tarefas ativas 1,15 MB → 295 B por consulta; episódios 1,29 MB → 140 KB;
  o perfil de voz (~190 KB) não vai mais ao navegador.
- Studio: tokens de design, atalhos (`?`), estado sem conexão, ErrorBoundary, detalhe do
  corte em 2 colunas, miniaturas enquadradas, fila "Para postar".
- Regras que não podem quebrar viraram hooks (`.claude/settings.json`, `scripts/hooks/`).

## Entregas anteriores

- 01/nov: triangulação automática por padrão ([evidência](evidence/2026-11-01-auto-triangulation.md)).
- 23/out: triangulação B–E manual ([evidência](evidence/2026-10-23-scene-suggestions.md)).
- 16–19/out: cortes integrados, Studio novo, voz do protagonista, deriva A/V corrigida.

## Próximo gate

1. Dayon revisa cortes automáticos de 2–3 episódios na aba Cenas (os 8 episódios com
   vídeo já têm mapa de câmeras). Meta: ≥ 80 % das cenas mantidas
   (`GET /episodes/{id}/scenes` → `review`). Depois, avaliar a pergunta no prompt de
   seleção (worktree).
2. Prévias mais rápidas (QA mais barato e prévia progressiva nos drafts). Drive
   automático e acesso remoto ficam parados até o usuário pedir.

## Congelado (não evoluir)

Diarização pyannote completa: só manual, para confirmar a voz de um episódio novo.
(Análise de câmera/identidade/falante e reações emprestadas já foram removidas.)

## Operação

- O `cortex.service` vive com a janela do app (`open_studio.py`); não sobe no login.
  Atualizar: o usuário roda `./scripts/update_and_verify.sh` (recusa com job ativo, troca a
  UI e só reinicia se o serviço estiver ligado). Agentes nunca ligam/desligam o serviço.
- Validação real sem tocar os dados: clone CoW `cp -a --reflink=always data <dir>/data`
  (btrfs, instantâneo) e API/worker com `CORTEX__PATHS__*` apontando para o clone,
  `CORTEX_CONFIG=<repo>/config/cortex.yaml` (sem isso valem os padrões do código, com
  NVENC) e o processo rodando em `<dir>` (caminhos de artefato são relativos).
- Máquina: Dell G7, GTX 1060 Max-Q 6 GB, driver 580 (último para Pascal).

## Verificação

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src/cortex scripts tests
(cd apps/web && npx tsc --noEmit && npm test && npx vite build --outDir /tmp/cortex-web --emptyOutDir)
git diff --check
```

Nunca apagar `data/`, `.env`, autenticação do Claude ou trabalho não commitado.
