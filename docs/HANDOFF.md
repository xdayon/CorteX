# CorteX — estado atual para agentes

Atualizado em 2026-10-23. Histórico anterior em
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
→ render libass com quadros exatos por segmento → QA técnico → MP4/SRT/manifest
```

## Entregue em 23/out (branch `feat/triangulation-scenes`, pede restart + rebuild)

- Prévias quebradas com fade depois de corte seco ou foco por rosto (`xfade` com bases de
  tempo diferentes) corrigidas.
- Triangulação B–E ([plano](TRIANGULATION.md),
  [evidência](evidence/2026-10-23-scene-suggestions.md)):
  - sugestões de cena por corte (reação calada, plano aberto);
  - inserções que trocam só a imagem;
  - abertura com o fim da pergunta;
  - "Aplicar triangulação";
  - etapas do episódio no Studio e aba Cenas no editor.

## Entregue em 19/out

- Legenda: linhas equilibradas por página (sem palavra solta na 2ª linha, evita fechar
  linha em artigo/preposição, prefere quebrar após pontuação) e quebra manual por palavra
  (`captions.line_break_word_indices`, revisão `line_breaks`, botão no editor da palavra);
  `ASS_GENERATOR_VERSION` 6 refaz overlays e exports.
- Downloads de MP4/SRT com o nome da manchete do corte (`render_download_name`).

## Entregue em 16/out

Cortes integrados ao episódio (estados aprovado/descartado/salvo, exports ligados ao
corte), Studio novo (episódios, editor de estúdio com fila de prévias e ajustes por
corte), voz do protagonista em ~3 min ([voice-match](evidence/2026-10-16-voice-match.md)),
correção de deriva A/V ([av-sync](evidence/2026-10-16-av-sync-drift.md)).

## Próximo gate

1. Usuário: merge do branch e `./scripts/update_and_verify.sh` (testa, compila e reinicia
   com segurança).
2. Dayon revisa as sugestões da aba Cenas em 2–3 episódios. Meta: aprovação ≥ 80 %
   (`GET /episodes/{id}/scenes` → `review`). Só depois avaliar o automático ligado por
   padrão e a pergunta no prompt de seleção.
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
