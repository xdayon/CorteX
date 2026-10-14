# Arquitetura do CorteX

## Limites

Editor pessoal Dayon News/HiTechX, master único, local e com revisão humana.
Não há runtime PodCLI, multicam ISO ou servidor editorial alternativo.
Interface React/TypeScript; backend Python/FastAPI; SQLite e arquivos locais;
FFmpeg para mídia e legendas (libass); Claude Code CLI para narrativa.

## Mapa para mudanças

| Responsabilidade | Código | Contrato/testes principais |
| --- | --- | --- |
| Configuração e caminhos | `config.py`, `paths.py`, `config/cortex.yaml` | schemas Pydantic, testes de configuração |
| Catálogo/estado | `library.py`, `domain/models.py`, `domain/store.py` | `test_episode_library.py`, `test_workflow_runs.py` |
| Jobs e orquestração | `jobs.py`, `worker.py`, `export_batches.py` | jobs, workflow_runs, export_batches, cancelamento |
| API/arquivos | `api.py`, `review_api.py`, `studio.py` | studio, export_batches, media_previews, render_media_api |
| YouTube/upload/normalização | `ingest/` | ingest, ingest_transcribe_integration |
| Transcrição | `transcribe/` | transcribe, cuda_env |
| Diarização acústica | `diarize/` | speaker_attribution; benchmark real pendente |
| Áudio/cenas/faces/identidade | `analyze/` | analysis, scene_index, face_index, speaker_timeline, identity_index |
| Seleção semântica | `suggest/`, `prompts/` | suggestion_cli, speaker_attribution |
| Fronteiras e EDL | `edit/` | edit_plan, audio_editing, camera_edit_plan |
| MP4/legendas/QA | `render/` (`ass_overlay.py`), `assets/fonts/` | render*, auto_framing, video_transitions |
| UI e drafts | `apps/web/src/`, `editorDraft.ts` | App.test.tsx, api.test.ts |
| Avaliação | `eval/`, `scripts/eval_selection.py`, `gate9_benchmark.py` | eval_runner, gate9_benchmark |
| Operação local | `scripts/studio.sh`, `install_local_service.py`, `open_studio.py` | studio + systemd-analyze + health no host |

Python paths são relativos a `src/cortex/`; testes backend ficam em `tests/`.
Consulte `rg --files tests` para os nomes exatos. Não carregar todos os módulos
para uma alteração de UI ou schema. Mídia/SQLite/cache ficam em `data/`, ignorado
no Git; `.env` e ambientes locais também não são versionados.

## Pipeline e persistência atual

```text
ingest -> probe/normalize -> transcribe -> audio analysis -> suggest
       -> human review -> optional scoped visual analysis -> edit plan
       -> render -> quality gate
```

Diarização opcional antecede a seleção quando Dayon precisa ser identificado.
WorkflowRun persiste até revisão. `ClipRevision` versiona seleção e ajustes no
SQLite; `ExportBatch` fotografa essa revisão e o worker agenda serialmente cada
EDL/render em transações idempotentes. A UI observa e restaura esse estado, sem
ser responsável por manter a fila viva. O draft local continua como fallback.

Artefatos guardam schema, inputs/hashes, engine solicitada/efetiva e resultados.
Retries determinísticos reaproveitam cache compatível. Amostras visuais possuem
cache incremental endereçado pelo hash da fonte, versão do FFmpeg, largura e
timestamp; estágios ainda abrem seus próprios decoders quando faltam amostras.
Proposta de LLM é JSON validado como dados, nunca shell executável.

## Quem decide o quê

- Claude Code CLI (Sonnet, assinatura): janela do corte, headline, nota e motivo;
  passada "enxugar" escolhe as frases mantidas nos cortes exportados.
  Sem API key obrigatória; login de assinatura, rede e limites da conta continuam.
- Boundary/EDL: fronteiras físicas usando palavras/VAD/RMS e clocks A/V separados.
- FFmpeg: normalização, trim, crossfade, J/L-cut, crop, gblur, loudness, compositing,
  encode e verificações técnicas. Input seek usa janela física e filtros relativos;
  manifests/EDL/captions conservam tempos absolutos da fonte.
- libass (`render/ass_overlay.py`): legenda karaoke/pop e headline queimadas no mesmo
  encode, fontes Montserrat versionadas em `assets/fonts/`. Remotion fica só como
  `render.overlay_renderer: remotion` explícito e fonte dos componentes da prévia web.
- Edição: perfil `dynamic` (padrão) faz corte seco em pausas ≥0,25s e remove muletas;
  a passada "enxugar" (Claude) escolhe frases mantidas nos cortes exportados.
- Humano: identidade/voz, cortes escolhidos, texto, framing, intenção e aprovação.

`approximate_edl` orienta as regiões semânticas, mas as fronteiras físicas ainda
são resolvidas deterministicamente. A predominância da voz confirmada é
revalidada sobre o plano de áudio final, após limite, J/L-cut e clamp da fonte.

## Câmeras e áudio

Um master só contém a câmera transmitida em cada instante. `speaker_auto` usa
cenas, faces e atividade visual conservadora nos cortes selecionados + margem;
slots de rosto são locais aos planos. Aparição na tela não comprova voz.
O enquadramento mantém contexto quando não há crop seguro. Correção manual por
cena: esquerda, direita ou ambos; punch-in estático limitado.

Quadro inteiro com Gaussian blur é default e não exige análise facial. Reações
emprestadas de outro instante são opt-in, preservam áudio editorial e exigem
entrevistador confirmado. J/L-cut usa relógios independentes; a cadeia de fonte e
hashes é validada antes do render. Não fabricar câmera ausente do master.

Perfis `balanced`, `dynamic`, `contemplative` regulam pausas e handles. QA técnico
verifica duração/streams/decode/loudness/sync/overlays e gera MP4/SRT/manifests;
naturalidade, semântica e atribuição correta exigem revisão humana.

## Hardware e operação

GTX 1060 6 GB: faster-whisper `large-v3-turbo` CUDA `int8`, batch inicial 8;
H.264 NVENC. `float16` e AV1 NVENC não são defaults válidos nesse hardware.
Visão e diarização atuais usam CPU. NVENC não implica decode NVDEC ou filtros GPU.
Nunca substituir engine silenciosamente. Medir antes de aumentar concorrência.

API-only é o default de `create_app`; `app.studio_dir` habilita apenas o build
estático, montado após as rotas. `studio.sh` força bind loopback, publica a porta
efetiva em arquivo local e supervisiona API/worker. Serviço systemd inicia no login;
atalho inicia o serviço e abre a interface. Vite permanece no desenvolvimento.
Cloudflare ainda é proposta: [local e Cloudflare](LOCAL_AND_CLOUDFLARE.md).

## Evolução sem reescrita

Continuar a extração dos demais routers mantendo contratos; revisão/export já
estão em `review_api.py` e a máquina de estados em `export_batches.py`. Extrair
telas/hooks e pequeno pacote visual.
Não introduzir Redis, banco remoto, segundo renderer ou framework por conveniência.
A motivação de cada mudança e critérios estão em [revisão](REVIEW_DAYON_NEWS.md)
e [ROADMAP](ROADMAP.md). Evidências históricas não provam paridade da versão atual.
