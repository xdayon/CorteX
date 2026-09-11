# Evidência — revisão e consolidação Dayon News

Iniciada em 2026-09-10; integração final em 2026-09-11 (America/Sao_Paulo).
Branch: `codex/cortex-simplificacao`. Base remota: `c838544`.

## Baseline preservado

O worktree já continha simplificação da interface, remoções de PodCLI/mods,
legendas, framing por intervalos, biblioteca e diarização ainda sem commit.
Essas mudanças foram preservadas e integradas, não restauradas a partir de HEAD.
Mídia, cache, SQLite, ambientes e `.env` ficaram fora do Git.

Antes das correções: backend **351 passed, 3 skipped** em 75,31 s. UI **24 failed,
4 passed**; os mocks da nova biblioteca não haviam sido atualizados. Havia um
whitespace em App.tsx. Não era baseline de produção pronto.

## Alterações verificadas

- Render: janela de entrada calculada pelo áudio e vídeo efetivos, seek antes de
  `-i`, trims relativos, EDL absoluta, cache/provenance com versão. Seis regressões
  com vídeo/áudio sintéticos verificam pixels/frequências, J/L, reactions e cache.
- Voz: validação conservadora de envelope + união EDL editorial; não conta turns
  duplicados, overlap nem silêncio como confirmação de Dayon. Métricas persistidas,
  cache invalidado por versão e falhas de referência antes da chamada Codex.
- UI: headline por corte e sugestão quando vazia; autosave versionado, isolamento
  A→B→A, restauração após reload e fixtures de biblioteca atualizadas.
- Biblioteca: canonicalização YouTube, idempotência, persistência de participantes,
  reconciliação de fontes locais e rejeição de URLs inválidas em SQLite temporário.
- Operação: build servido na mesma origem da API; arquivos privados fora da raiz,
  traversal/symlink bloqueados; serviço de usuário e atalho com health/porta efetiva.
- Dependências: manifest frontend fixado às versões já presentes no lock, sem
  atualizar motores. Vite/TypeScript/plugin React classificados como tooling.
- Documentação: handoff antigo de 1.648 linhas preservado em history e substituído
  por estado curto; roadmap atual com limites e gates; revisão/Cloudflare/diarização.

## Verificação final

| Comando | Resultado |
| --- | --- |
| `CORTEX_RUN_REMOTION_E2E=1 .venv/bin/pytest -q` | **394 passed**, 110,73 s, incluindo browser/overlays reais, nenhum skip |
| `(cd apps/web && npm test)` | **34 passed**, incluindo seis novas regressões de drafts/headlines |
| `(cd apps/web && npm run build)` | Passou; build final após ajustes de texto/CSS, JS 240,34 kB e CSS 16,32 kB |
| `(cd apps/remotion && npm run build)` | Passou |
| `.venv/bin/ruff check src/cortex scripts tests` | Passou |
| `.venv/bin/python -m compileall -q src/cortex scripts` | Passou |
| `git diff --check` | Passou |
| `bash -n scripts/studio.sh` | Passou |
| Gerador de unit + `systemd-analyze --user verify` | Passou sem avisos após corrigir quoting de paths |
| Inspeção de padrões de credenciais em arquivos versionáveis | Nenhuma ocorrência; `.env` ignorado, sem imprimir valores |

Ambiente no fechamento: Python 3.14.7; Node 26.8.1. O serviço resolve diretórios
estáveis do Node/Codex em vez de guardar o caminho efêmero de fnm em `/run`.

## GPU no host

`./scripts/check_gpu.sh` executado no host em 2026-09-10:

- NVIDIA GeForce GTX 1060 with Max-Q Design, 6144 MiB, compute capability 6.1.
- Driver 580.173.02.
- Encode real H.264 NVENC, 320×240, yuv420p, concluído.
- CTranslate2: 1 device CUDA; `float32`, `int8`, `int8_float32` suportados.

Isso não comprova decode NVDEC, inferência longa de Whisper ou diarização GPU.
Diarização atual usa CPU. A suíte de render utiliza principalmente fontes curtas
sintéticas; não foi feito novo benchmark de episódio completo nem alegado speedup.

## Serviço e catálogo reais

Instalados `cortex.service` no systemd do usuário e `cortex.desktop` no menu.
Serviço habilitado para login e iniciado, API vinculada a 127.0.0.1.

- `scripts/open_studio.py --no-browser` iniciou/aguardou a API e retornou
  `http://127.0.0.1:8787`.
- `systemctl --user is-active cortex.service` → active.
- `systemctl --user is-enabled cortex.service` → enabled.
- `GET /` → HTTP 200, build React.
- `GET /api/v1/health` → status ok.
- `GET /.env` → HTTP 404.
- Catálogo existente: **9 episódios e 32 renders**, recuperados pela API sem
  apagar/mover mídias. Não havia jobs queued/running antes de iniciar o serviço.

Capturas de QA ficam somente em `.cache/review-2026-09-10/`; não são publicadas
no Git porque mostram o catálogo pessoal. Não representam validação humana da
qualidade de todos os cortes.

Smoke de UI no Chrome Headless real: biblioteca mostrou 9 cards, sem erro de
carregamento; abriu uma seleção existente, navegou a Exportar e exibiu o campo
de headline manual, sem `.simple-error`. Capturas da biblioteca e do editor foram
inspecionadas. Nenhum botão de análise ou render foi acionado nesse smoke.

## Limitações ainda abertas

1. Lote continua agendado pela aba; draft continua localStorage, sem sync backend.
2. Gate de voz está na sugestão, não no plano de áudio final; falta vínculo voz/rosto.
3. `approximate_edl` não é consumida pelo planner atual.
4. Visão ainda repete decodes, cache depende do conjunto exato e identidade tem O(n²).
5. Diarização precisa benchmark real, pesos fixados e melhores timeout/diagnóstico.
6. Não criado Tunnel/Access, DNS, R2, Pages ou Worker; hostname/identidade pendentes.
7. Sem garantia de viralização; avaliação editorial e dados de retenção permanecem humanos.

Próximos passos e critérios: [ROADMAP](../ROADMAP.md). Recomendações e fontes
externas: [revisão](../REVIEW_DAYON_NEWS.md) e [Cloudflare](../LOCAL_AND_CLOUDFLARE.md).
