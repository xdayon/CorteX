# CorteX — estado atual para agentes

Atualizado em 2026-09-11. Este arquivo substitui o handoff histórico de 1.648
linhas, preservado em [history](history/HANDOFF_FABLE_5-before-2026-09-10.md).
O histórico não é uma fila de instruções. Não relê-lo inteiro por padrão.

## Produto e restrições

Editor pessoal Dayon News/HiTechX para podcasts em um master único do YouTube
ou arquivo local. Foco nas falas de Dayon, com contexto real dos participantes.
Sem fontes ISO, PodCLI, Claude, API editorial paga ou fallback silencioso.
Codex CLI `gpt-5.5`, medium, é o único editorial. O CLI usa login ChatGPT e rede;
transcrição/render e mídia ficam locais. Modelo do agente de desenvolvimento e
modelo editorial são configurações independentes.

## Implementado

- Biblioteca por ID YouTube, título/canal/capa via oEmbed, arquivar/restaurar e busca.
  Oito links Dayon cadastrados no host; entradas antigas arquivadas, mídia preservada.
- Ingestão YouTube/upload, WAV/transcript e
  análise de áudio cacheados, seleção Codex validada por schema, jobs SQLite.
- Workflow encadeia transcrição/análise/sugestão até revisão; exportação ainda
  agenda cada próximo corte no navegador. **Lote durável não está concluído.**
- Prévia curta real, MP4/SRT/manifests, QA técnico, captions compartilhadas com
  Remotion, correções por palavra, karaoke/pop, headline manual por corte.
- Jornada em três etapas com Biblioteca separada, prévia lateral, headline/legenda
  compartilhadas com export, posição vertical do vídeo e detalhes de tarefas reais.
  Remotion reporta frames; FFmpeg reporta tempo codificado, ambos com IO limitado.
- Draft versionado com autosave no navegador e restauração isolada por run.
  Isso não é persistência de edição no backend ou sincronização entre navegadores.
- Quadro inteiro com Gaussian blur é default. Automático visual é opcional e
  analisa apenas intervalos escolhidos + margem. Correção por cena e zoom estático;
  cenas abertas/duplas são preservadas (auto v1.2). Reação de outro instante exige
  opção explícita e entrevistador confirmado; um único ângulo não confirma identidade.
- Render busca janela física antes de decodificar, preserva clocks absolutos da
  EDL e inclui J/L-cut/reaction. Janela única ainda inclui gaps internos.
- Pyannote community-1 CPU opcional em `.venv-diarization`; voz confirmada na
  biblioteca participa da seleção. Voz/rosto ainda não têm identidade unificada.
- Validação de voz no envelope + união da EDL editorial; **planner ainda não
  consome approximate_edl e não revalida predominância no áudio final**.
- Build Studio servido pela API no loopback, serviço Linux e atalho instaláveis.
  Não existe deploy Cloudflare; arquitetura escolhida: local + Tunnel/Access.

## Máquina e evidência

Dell G7, GTX 1060 Max-Q 6 GB/Pascal. Smoke host em 2026-09-10 confirmou
H.264 NVENC e CTranslate2 CUDA com `int8`, `int8_float32`, `float32`.
Não solicitar float16 nem AV1 NVENC. Encode NVENC não prova decode NVDEC.
Episódio longo, diarização real e qualidade editorial continuam exigindo benchmark
específico; confira [evidência](evidence/2026-09-10-architecture-review.md).

## Próximo gate

Os ajustes de UX estão em [evidência](evidence/2026-09-11-studio-experience.md).
Executar P0 do [ROADMAP](ROADMAP.md): export batch + revisão persistidos no
servidor, retomada independente do navegador e transição de workflow idempotente.
Separar extração de módulos de mudança de comportamento. Antes de editar, leia
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
Nunca apagar `data/`, `.env`, autenticação Codex ou worktree não commitado.
