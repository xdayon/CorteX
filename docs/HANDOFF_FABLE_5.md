# CorteX — estado atual para agentes

Atualizado em 2026-09-13. Este arquivo substitui o handoff histórico de 1.648
linhas, preservado em [history](history/HANDOFF_FABLE_5-before-2026-09-10.md).
O histórico não é uma fila de instruções. Não relê-lo inteiro por padrão.

## Produto e restrições

Editor pessoal Dayon News/HiTechX para podcasts em um master único do YouTube
ou arquivo local. Foco nas falas de Dayon, com contexto real dos participantes.
Sem fontes ISO, PodCLI, Claude, API editorial paga ou fallback silencioso.
Claude Code CLI (`claude -p`, Sonnet, esforço baixo) na assinatura Pro é o único editorial;
sem créditos de API. O CLI usa o login OAuth e rede;
transcrição/render e mídia ficam locais. Modelo do agente de desenvolvimento e
modelo editorial são configurações independentes.

## Implementado

- Biblioteca por ID YouTube, título/canal/capa via oEmbed, arquivar/restaurar e busca.
  Oito links Dayon cadastrados no host; entradas antigas arquivadas, mídia preservada.
- Ingestão YouTube/upload, WAV/transcript e
  análise de áudio cacheados, seleção Claude validada por schema, jobs SQLite.
- Workflow encadeia transcrição/análise/sugestão até revisão. `ClipRevision` e
  `ExportBatch` persistem a edição; um POST idempotente mantém o lote no worker,
  com retomada, cancelamento e retry de falhas independentes do navegador.
- Prévia curta real, MP4/SRT/manifests, QA técnico, captions compartilhadas com
  Remotion, correções por palavra, karaoke/pop, headline manual por corte.
- Jornada em três etapas com Biblioteca separada, prévia lateral, headline/legenda
  compartilhadas com export, posição vertical do vídeo e detalhes de tarefas reais.
  Remotion reporta frames; FFmpeg reporta tempo codificado, ambos com IO limitado.
- Revisões têm autosave versionado no servidor e restauração por run; draft no
  navegador permanece como fallback para indisponibilidade temporária da API.
- Quadro inteiro com Gaussian blur é default. Automático visual é opcional e
  analisa apenas intervalos escolhidos + margem. Correção por cena e zoom estático;
  cenas abertas/duplas são preservadas (auto v1.2). Reação de outro instante exige
  opção explícita e entrevistador confirmado; um único ângulo não confirma identidade.
- Render busca janela física antes de decodificar, preserva clocks absolutos da
  EDL e inclui J/L-cut/reaction. Janela única ainda inclui gaps internos.
- Pyannote community-1 CPU opcional em `.venv-diarization`; voz confirmada na
  biblioteca participa da seleção. Voz/rosto ainda não têm identidade unificada.
- Legenda medida em até duas linhas com área fixa; zoom mantém a janela do vídeo.
  Teto do brief validado na seleção e na EDL; finais antecipados ficam explícitos.
  Conclusão visual mantém 100%. Evidência e regressões em
  [export-corrections](evidence/2026-09-11-export-corrections.md).
- Planner consome `approximate_edl`, resolve fronteiras físicas deterministicamente
  e revalida predominância da voz após limite, J/L-cut e clamp físico final.
- Cache visual incremental reaproveita amostras por fonte/FFmpeg/largura/timestamp;
  identidade usa busca indexada, amostragem limitada e cancelamento cooperativo.
- `uv.lock`, telemetria de duração de jobs/render e manutenção segura de backup,
  restauração e cache foram acrescentados. Tunnel/Access não foi implementado.
- Endpoints de revisão/export estão em `review_api.py`; transições duráveis de
  lote estão em `export_batches.py`, fora do API/worker monolíticos.
- Build Studio servido pela API no loopback, serviço Linux e atalho instaláveis.
  Não existe deploy Cloudflare; arquitetura escolhida: local + Tunnel/Access.

## Máquina e evidência

Dell G7, GTX 1060 Max-Q 6 GB/Pascal. Smoke host em 2026-09-10 confirmou
H.264 NVENC e CTranslate2 CUDA com `int8`, `int8_float32`, `float32`.
Não solicitar float16 nem AV1 NVENC. Encode NVENC não prova decode NVDEC.
Amostra real de diarização: 90s → 6 turnos/2 vozes em 84,54s CPU; não comprova
acurácia nem atribui Dayon. Token local habilitado; UI pede confirmação humana.
Adapter 4.0.6 corrigido para batch gerador, contadores NumPy e Python do venv
(não resolver symlink). Gate API/worker/cache em `tests/test_diarization_e2e.py`. Episódio inteiro e
qualidade editorial continuam exigindo benchmark específico; confira
[evidência](evidence/2026-09-10-architecture-review.md).

## Próximo gate

Os ajustes de UX estão em [evidência](evidence/2026-09-11-studio-experience.md).
Trabalhar diretamente na `main`, conforme pedido do usuário. Branches anteriores
devem permanecer integradas; não restaurar versões antigas de frontend/pipeline.
O próximo gate aberto é medir o P1 do [ROADMAP](ROADMAP.md): benchmark comparável
de decoder/cache/overlay e, só com evidência, consolidar decoder compartilhado.
Também seguem abertos corpus real de voz e extração gradual de routers/UI. Antes de editar, leia
`AGENTS.md`, este arquivo e somente o contrato do gate escolhido. O mapa está em
[ARCHITECTURE](ARCHITECTURE.md); justificativas em [REVIEW_DAYON_NEWS](REVIEW_DAYON_NEWS.md).

## Verificação

```bash
.venv/bin/pytest -q
(cd apps/web && npm test && npm run build)
(cd apps/remotion && npm run build)
.venv/bin/ruff check src/cortex scripts tests
git diff --check
```

No host com browser configurado: `CORTEX_RUN_REMOTION_E2E=1 .venv/bin/pytest -q`.
GPU: `./scripts/check_gpu.sh`. Não executar episódio inteiro como smoke test.
Nunca apagar `data/`, `.env`, autenticação Claude ou worktree não commitado.
