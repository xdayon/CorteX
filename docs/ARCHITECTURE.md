# Arquitetura do CorteX

## Limites

Editor pessoal Dayon News, master único, local e com revisão humana. Interface
React/TypeScript; backend Python/FastAPI; SQLite e arquivos locais; FFmpeg/libass
para mídia e legendas; Claude Code CLI (assinatura) para decisões editoriais.
Não há runtime PodCLI, multicam ISO, servidor editorial alternativo ou cloud GPU.

## Mapa para mudanças

| Responsabilidade | Código | Testes principais |
| --- | --- | --- |
| Configuração e caminhos | `config.py`, `paths.py`, `config/cortex.yaml` | configuração |
| Catálogo/estado | `library.py`, `domain/models.py`, `domain/store.py` | episode_library, workflow_runs |
| Jobs e orquestração | `jobs.py`, `worker.py`, `export_batches.py` | jobs, workflow_runs, export_batches |
| API | `api.py`, `review_api.py` (revisão/lote), `overlay_api.py` (prévia) | studio, export_batches, visual_engine |
| YouTube/upload | `ingest/` | ingest |
| Transcrição | `transcribe/` (hotwords, modo batched registrado) | transcribe_worker, cuda_recovery |
| Seleção e refino (IA) | `suggest/` (`provider.py`, `service.py`, `tighten.py`), `prompts/` | suggestion_cli, composed_selection, tighten, claude_provider |
| Plano de edição | `edit/` (corte seco, muletas, peças, J/L) | edit_plan, dynamic_editing, audio_editing |
| Render e QA | `render/service.py`, `render/ass_overlay.py`, `render/safe_zones.py` | render*, ass_overlay, visual_engine |
| UI | `apps/web/src/` | App.test.tsx, api.test.ts |
| Avaliação | `eval/`, `scripts/eval_selection.py` | eval_runner |
| Operação local | `scripts/studio.sh`, `install_local_service.py`, `update_and_verify.sh` | — |
| Estado dos cortes | `clips.py`, `clips_api.py` | test_clips_api |
| Foco por rostos | `render/face_focus.py`, `analyze/face_detect.py` (YuNet), `analyze/frames.py`, `analyze/scene_*` | face_focus, face_detect, face_focus_render, scene_index |

Python paths são relativos a `src/cortex/`; testes em `tests/`. Mídia/SQLite/cache
ficam em `data/` (fora do Git).

## Quem decide o quê

- **Claude (seleção)**: quais falas viram cortes, peças de cada corte, 3 manchetes,
  palavras de destaque, nota e motivo — uma chamada por episódio.
- **Claude (refino)**: quais frases ficam em cada corte exportado (gancho mais forte,
  sem gordura), nunca atravessando peças nem ficando abaixo do mínimo.
- **Código determinístico**: fronteiras físicas por palavras/energia, cortes de pausa
  ≥ 0,25 s, muletas, muletas iniciais, `piece_index`, teaser de abertura (`teaser`, decodificado de uma segunda entrada), limite de duração, J/L-cut.
- **Render**: enquadramento (fundo desfocado com zoom/foco ou tela cheia), zoom
  alternado nos cortes, flash/zoom nas junções entre peças, legenda e manchete
  libass no mesmo encode, loudness −14 LUFS/−1 dBTP, QA com faixas do Reels/TikTok.
- **Humano**: cortes escolhidos, texto da legenda, destaques, manchete, estilo,
  enquadramento e aprovação final.

## Prévia = export

`render.service.build_plan_overlay` gera o `.ass` para o render e para a prévia (mesma
função). O editor mostra um render `purpose: "draft"` (meia resolução, sem tipografia)
com o `.ass` desenhado no navegador por JASSUB (libass em WebAssembly), com as mesmas
fontes servidas por `/overlay-fonts`. Nunca reimplementar layout de legenda em CSS.

## Hardware

GTX 1060 6 GB (driver 580, último para Pascal): faster-whisper `large-v3-turbo` CUDA
`int8`, batch 8. Encode em `libx264` (NVENC incompatível com o FFmpeg 8.1.3 do host;
pré-checagem explícita). Visão e diarização em CPU. Nunca trocar engine em silêncio.

## Operação

API-only é o default de `create_app`; `app.studio_dir` serve o build estático.
`studio.sh` força loopback e supervisiona API + worker; o serviço systemd de usuário
sobe no login. Acesso remoto (Tailscale ou Cloudflare Tunnel + Access) é Fase 3.

## Evolução sem reescrita

A dinâmica de câmera antiga (análise de rosto/falante/identidade, planos de câmera, reações emprestadas)
foi removida: o foco por rostos a substitui; jobs, modos e campos antigos seguem legíveis no SQLite.
Extrair routers do
`api.py` por domínio mantendo contratos. Não introduzir Redis, banco remoto, segundo
renderer ou framework por conveniência. Prioridades em [ROADMAP](ROADMAP.md).
