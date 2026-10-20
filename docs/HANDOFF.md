# CorteX — estado atual para agentes

Atualizado em 2026-10-16. Histórico anterior em
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

## Entregue em 19/out (pede restart + rebuild)

- Legenda: linhas equilibradas por página (sem palavra solta na 2ª linha, evita fechar
  linha em artigo/preposição, prefere quebrar após pontuação) e quebra manual por palavra
  (`captions.line_break_word_indices`, revisão `line_breaks`, botão no editor da palavra);
  `ASS_GENERATOR_VERSION` 6 refaz overlays e exports.
- Downloads de MP4/SRT com o nome da manchete do corte (`render_download_name`).

## Entregue em 16/out

- Cortes integrados: `GET /episodes/{id}/clips`, `GET /clips`, `/clips/summary`,
  `PATCH /clip-states` (aprovado, descartado com motivo, salvo); exports e publicação
  ligados ao corte; capa JPG dos exports (`/renders/{id}/poster`).
- Studio novo (`apps/web/src`): design system, rotas reais com fallback SPA, episódios,
  página do episódio em dois painéis com player instantâneo dos trechos na fonte,
  quadro "Cortes", editor de estúdio (linha do tempo, ajustes por corte via
  `clip_settings`, fila de prévias, seleções de várias rodadas, atalhos).
- Voz do protagonista em ~3 min por episódio no lugar da diarização de ~1× a duração
  ([voice-match](evidence/2026-10-16-voice-match.md)).
- Render: deriva A/V em cortes com muitas junções corrigida, cache com
  `timeline_version: 2` ([av-sync](evidence/2026-10-16-av-sync-drift.md)); drafts mais
  rápidos e timings por etapa ([draft-speed](evidence/2026-10-16-draft-speed.md)).
- Entregas de 14/out (IA v4/v5, visual, prévia JASSUB, NVENC → x264) seguem válidas.

## Próximo gate

1. Usuário: `./scripts/update_and_verify.sh` (com uv instala no `.venv` sem pip, testa,
   compila e reinicia com segurança). Perfil de voz cadastrado em 20/out a partir de
   `wk7PY31TQBc` (balanced accuracy 0,986, limiar 0,4395); o menu "Identificar voz do
   protagonista" ainda dispara a diarização pyannote completa (~1× a duração) e não é
   o cadastro.
2. Botão no Studio para cadastrar/refazer o perfil de voz; Drive automático (rclone)
   se o usuário pedir; QA mais barato e prévia progressiva nos drafts.
3. Acesso remoto (`cortex.synapses.academy`): faltam os passos do usuário no painel e
   `scripts/setup_remote_access.py` — [guia](LOCAL_AND_CLOUDFLARE.md).

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
