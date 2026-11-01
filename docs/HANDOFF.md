# CorteX — estado atual para agentes

Atualizado em 2026-11-01. Histórico anterior em
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

## Entregue em 01/nov (branch `feat/triangulation-auto`, pede restart + rebuild)

Triangulação automática por padrão ([plano](TRIANGULATION.md),
[evidência](evidence/2026-11-01-auto-triangulation.md)):

- todo corte sem escolha própria ganha plano aberto + entrevistador, resolvidos pelo
  worker de render (prévia = export);
- imagem emprestada conferida por quadros, porque o mapa por keyframes perde cortes;
- papéis de câmera vindos da voz até alguém confirmar;
- aba Cenas com "Automáticas" e "Voltar ao automático".

## Entregas anteriores

- 23/out: triangulação B–E manual ([evidência](evidence/2026-10-23-scene-suggestions.md)) e
  prévias com fade corrigidas.
- 19/out: legenda com linhas equilibradas e quebra manual; downloads com o nome da manchete.
- 16/out: cortes integrados ao episódio, Studio novo, voz do protagonista
  ([voice-match](evidence/2026-10-16-voice-match.md)), deriva A/V corrigida
  ([av-sync](evidence/2026-10-16-av-sync-drift.md)).

## Próximo gate

1. Merge de `feat/triangulation-auto` e `./scripts/update_and_verify.sh` (testa, compila
   e reinicia com segurança, sem job ativo).
2. Dayon revisa cortes automáticos de 2–3 episódios na aba Cenas. Meta: ≥ 80 % das cenas
   mantidas (`GET /episodes/{id}/scenes` → `review`). Depois, avaliar a pergunta no
   prompt de seleção (worktree).
3. Prévias mais rápidas (QA mais barato e prévia progressiva nos drafts). Drive
   automático e acesso remoto ficam parados até o usuário pedir.

## Congelado (não evoluir)

Diarização pyannote completa: só manual, para confirmar a voz de um episódio novo.
(Análise de câmera/identidade/falante e reações emprestadas já foram removidas.)

## Operação

- `systemctl --user restart cortex.service` só sem job ativo; agentes pedem ao
  usuário. `./scripts/update_and_verify.sh` faz deps, Claude CLI, testes, build e
  reinício seguro.
- Validação real sem tocar os dados: clone CoW `cp -a --reflink=always data <dir>/data`
  (btrfs, instantâneo) e API/worker com `CORTEX__PATHS__*` apontando para o clone,
  `CORTEX_CONFIG=<repo>/config/cortex.yaml` (sem isso valem os padrões do código, com
  NVENC) e o processo rodando em `<dir>` (caminhos de artefato são relativos).
- Máquina: Dell G7, GTX 1060 Max-Q 6 GB, driver 580 (último para Pascal).

## Verificação

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src/cortex scripts tests
(cd apps/web && npm test && npm run build)
git diff --check
```

Nunca apagar `data/`, `.env`, autenticação do Claude ou trabalho não commitado.
