# 2026-10-14 — seleção v4, refino v2 e provider Claude robusto

## Problema
- `claude_binary` apontava para o `claude` do npm/fnm, que ficou como stub sem o
  binário nativo após a atualização de 10/out: toda seleção falhava.
- Hash de cache incluía o config `ai` inteiro (caminho do binário, timeout):
  atualizar o Node invalidava seleções e cobrava de novo.
- Refino ("enxugar") aceitava manter 40% e não conhecia a duração mínima:
  7 dos 40 planos recentes ficaram abaixo de 40 s (mínimo 31,8 s).

## Mudanças
- Provider: binário resolvido em ordem fixa e registrada (configurado, ~/.local/bin,
  alias default do fnm, extensão do editor); template + transcrição no
  `--system-prompt-file` (cacheável); pedido curto na mensagem.
- Seleção v4 (`prompts/clip_selection.v4.schema.json`): 3 manchetes, palavras de
  destaque literais mapeadas para índices de palavra; gancho/payoff calculados
  localmente; muletas iniciais removidas do corte; `piece` na EDL.
- Refino v2: frases só dentro das peças, nunca une peças, recebe manchete e alvo de
  duração; pula sem chamada perto do mínimo; rejeita (com motivo, em cache) se
  cortar demais ou ficar abaixo do mínimo; esforço próprio `ai.refine_effort`.

## Resultados reais (cópia do SQLite, episódio de 76 min, Sonnet 5.5 medium)
| Versão | Válidos | Compostos | Entrada | Saída | Tempo |
| --- | --- | --- | --- | --- | --- |
| v3 (05/out, sessão anterior) | 23/25 | 7 | ~60 mil | ~14 mil | 107 s |
| v4 com `[início\|duração]` | 23/25 | 10 | 66 mil | 29,5 mil | 208 s |
| v4 com `[início]` (final) | 25/25 | 19 | 58 mil | 28,7 mil | 202 s |

Teste de cache (system prompt em arquivo, 32 KB): 2ª chamada leu 12.038 tokens do
cache e criou 882. Muletas iniciais removidas em 8 de 25 cortes.

Refino real (corte composto de 80 s): aceito, 79% da fala mantida, nova abertura no
gancho mais forte, 4 mil tokens de entrada + 1,4 mil de saída, 11,6 s.

## Não verificado aqui
Render ponta a ponta dos cortes desta seleção; reinício do serviço (pedir ao usuário).
