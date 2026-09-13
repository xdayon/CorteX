# Confiabilidade e otimização — 2026-09-13

Escopo: revisão persistida, export batch durável, integração de EDL semântica,
gate final de voz, índices/cache, telemetria, lock e manutenção de dados.
Tunnel/Access ficou explicitamente fora deste trabalho.

## Evidência automatizada

Executado no workspace após as mudanças:

```text
.venv/bin/pytest -q
491 passed, 7 skipped in 122.38s

cd apps/web && npm test -- --run
4 files passed; 60 tests passed

cd apps/web && npm run build
TypeScript + Vite concluídos; bundle JS 259.91 kB (81.07 kB gzip)

cd apps/remotion && npm run build
TypeScript concluído

.venv/bin/ruff check src/cortex scripts tests
All checks passed

git diff --check
sem erros

uv sync --locked --dry-run
lock resolvido (66 pacotes); nenhuma alteração aplicada ao ambiente
```

Cobertura focada adicionada: lote de três itens continua após descarte do cliente
e recriação dos stores, reconciliação entre job terminal e próximo job, POST
idempotente, revisões monotônicas, EDL semântica descontínua, rejeição por voz no
plano final, backup/restauração e prune com prévia.

## Limites da evidência

Os sete skips dependem de browser/Remotion ou recurso de host. Não foi executado
episódio real, benchmark frio/quente, GPU/NVENC nem render curto do lote no host.
O cache reduz decodificação repetida de timestamps já vistos, mas não comprova o
ganho antes de um benchmark comparável e ainda não compartilha um único processo
decoder entre todos os estágios visuais.
