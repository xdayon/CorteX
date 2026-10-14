# CorteX — estado atual para agentes

Atualizado em 2026-10-14. Histórico anterior em
[history](history/HANDOFF_FABLE_5-before-2026-09-10.md) — não é fila de instruções.

## Produto e restrições

Editor pessoal de cortes virais para @dayon.news (Instagram Reels/TikTok) a partir de
entrevistas em que Dayon é convidado: master único do YouTube ou arquivo local,
processamento local, revisão humana, publicação manual. Estilo: corte seco e
dinâmico, legenda animada ligada, manchete, rosto grande com o estúdio visível;
sem barra de progresso no vídeo, capa nunca como 1º frame. Modelos, esforços,
durações e caminhos ficam em `config/cortex.yaml` (fonte da verdade).

## Pipeline atual

```text
YouTube/upload → normaliza → transcreve (faster-whisper CUDA int8, hotwords)
→ analisa áudio → seleção Claude (1 chamada/episódio, transcrição em cache)
→ revisão humana → editor de exportação (refino Claude por corte, prévia draft)
→ plano de edição determinístico (corte seco, muletas, peças) → render libass
→ QA técnico (loudness, sync, faixas do Reels/TikTok) → MP4/SRT/manifest
```

## Entregue em 14/out (detalhes nas evidências)

- IA: seleção v4, refino v2, provider robusto e cache estável —
  [ai-selection-v4](evidence/2026-10-14-ai-selection-v4.md), [AI_PROVIDERS](AI_PROVIDERS.md).
- Visual: estilos de legenda, destaque por palavra, flash/zoom nas junções, zoom e
  foco no fundo desfocado, zoom alternado padrão, faixas seguras v2, API de prévia
  (`overlay-preview`, `overlay-fonts`, `safe-zones`, `render-defaults`), render draft.
- Studio: editor de exportação com prévia real (draft + JASSUB, idêntica ao export em
  Chrome), revisão de legenda estilo CapCut, abas de estilo/enquadramento/manchete,
  layout de 390 px; Remotion removido.
- Correções: primeiro item do lote sem refino, cortes abaixo do mínimo, hash de
  cache instável, fallback batched silencioso, script de atualização inseguro.
- Host: NVENC indisponível (FFmpeg 8.1.3 × driver 580 da GTX 1060) →
  `render.encoder: libx264`; [host-nvenc](evidence/2026-10-14-host-nvenc.md).
  Render real de 61 s: 43 s com x264, QA aprovado.

## Em andamento / próximo gate

1. Fase 2 do [ROADMAP](ROADMAP.md): foco por rosto entregue e modos congelados
   (`speaker_auto`, `speaker_face`, `face_static_crop`, planos de câmera, reações,
   identidade) removidos; valores de enum e campos antigos seguem legíveis no SQLite
   e o render recusa os modos antigos. Falta decidir com o usuário entre consertar ou
   remover a diarização.
2. Acesso remoto (`cortex.synapses.academy`): código, guarda JWT na origem, script e
   `cloudflared` prontos; faltam os passos do usuário no painel e
   `scripts/setup_remote_access.py` — [guia](LOCAL_AND_CLOUDFLARE.md).

## Congelado (não evoluir)

Diarização pyannote (opcional, CPU): ambiente verificado em 14/out (pyannote 4.0.6 +
torch CPU respondem); as 6 falhas de 12/set foram antes da correção do interpretador
do venv. Custa ~a duração do episódio em CPU. Mantida até a decisão do usuário.
(Análise de câmera/identidade/falante e reações emprestadas já foram removidas.)

## Operação

- `systemctl --user restart cortex.service` só sem job ativo; agentes pedem ao
  usuário. `./scripts/update_and_verify.sh` faz deps, Claude CLI, testes, build e
  reinício seguro.
- Validação real sem tocar o banco: copiar `data/cortex.sqlite3` para o scratchpad e
  sobrescrever `paths.database`/`paths.projects_dir` (ver scripts em evidências).
- Máquina: Dell G7, GTX 1060 Max-Q 6 GB, driver 580 (último para Pascal).

## Verificação

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src/cortex scripts tests
(cd apps/web && npm test && npm run build)
git diff --check
```

Nunca apagar `data/`, `.env`, autenticação do Claude ou trabalho não commitado.
