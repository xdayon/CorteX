# Provedor editorial

O CorteX usa um único provider editorial, escolhido em `ai.provider`
(`config/cortex.yaml`, fonte da verdade para modelo e esforço):

- `claude_code_cli` (ativo): Claude Code CLI no login da assinatura Claude.
- `codex_cli` (configurado, desativado): Codex CLI no login ChatGPT.

Trocar de assinatura é trocar esse valor. Não há fallback entre providers, API
key nem créditos de API; falhas (inclusive limite de uso) ficam visíveis no job.

## Chamada ao Claude

```text
claude -p --output-format json --json-schema <schema> --model <ai.claude_model>
  --effort <esforço> --tools "" --strict-mcp-config --setting-sources ""
  --disable-slash-commands --no-session-persistence
  --system-prompt-file <template + transcrição>   # contexto estável, em cache
  (stdin: pedido curto — brief, regras, rodada)
```

- Nunca `--bare` (só aceita API key). O ambiente remove `ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` e flags Bedrock/Vertex; as chamadas
  auxiliares do CLI usam o mesmo modelo (nada de Haiku).
- Binário: `ai.claude_binary` resolvido em ordem fixa (configurado/PATH,
  `~/.local/bin`, alias default do fnm, extensão do editor); o caminho e a versão
  usados ficam na provenance. Atualizações do npm já deixaram o binário do fnm sem
  o executável nativo; a resolução evita esse ponto único de falha.
- Toda resposta é revalidada pelo schema local. Provenance: provider, modelo,
  esforço, versão, resolução do binário, duração e uso de tokens.

## Etapas

1. **Seleção** (`prompts/clip_selection.pt-BR.md`, `clip_selection.v4.schema.json`):
   uma chamada por episódio. A transcrição vai como `#id [início] texto` (rótulo
   D/O só na troca de voz) dentro do system prompt em arquivo, junto com o
   template; o pedido (brief, regras) vai na mensagem. Assim a segunda rodada e
   regerações do mesmo episódio leem a transcrição do cache. Saída: título, 3
   manchetes, peças, palavras de destaque, nota, motivo, avisos. O backend valida
   duração/sobreposição, remove muletas do começo, mapeia destaques para índices
   de palavra e marca `piece` na EDL. Medido (76 min, Sonnet 5.5 medium): 58 mil
   tokens de entrada, 29 mil de saída, ~200 s, 25/25 cortes válidos.
2. **Refino** (`prompts/clip_tighten.pt-BR.md`): uma chamada pequena por corte
   escolhido para exportar (o editor de exportação e o lote compartilham o mesmo
   cache). Frases só dentro das peças, nunca une peças, recebe a manchete da IA e
   o alvo de duração; esforço `ai.refine_effort`. Pula sem chamada quando o corte
   já está no mínimo; rejeita (com motivo, em cache) se cortar demais ou ficar
   abaixo do mínimo. Medido: ~4 mil tokens de entrada + 1,4 mil de saída, 12 s.

As chaves de cache contêm só o que muda o resultado (provider, modelo, esforço,
prompt, schema e entradas): caminho do binário e timeout não invalidam caches.
A IA propõe trechos e textos; fronteiras físicas, pausas e muletas continuam no
motor determinístico de edição.
