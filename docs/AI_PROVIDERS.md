# Provedor editorial

O único motor editorial configurado é o **Claude Code CLI**, autenticado pelo
login OAuth da assinatura Claude (Pro) do usuário. Não há API key, créditos de API
nem fallback entre provedores: uma falha (inclusive limite de uso da assinatura)
fica visível no job. `ai.provider: codex_cli` continua disponível apenas como
escolha explícita.

```yaml
ai:
  provider: claude_code_cli
  claude_binary: /home/dx/.local/share/fnm/node-versions/v26.8.1/installation/bin/claude
  claude_model: sonnet
  claude_effort: low
  tighten_enabled: true
  timeout_seconds: 300
```

`claude_binary` é absoluto porque o `claude` do PATH interativo aponta para um
diretório temporário do fnm, ausente no serviço systemd.

## Chamada

Cada chamada roda em diretório temporário vazio, com o prompt no stdin:

```text
claude -p --output-format json --json-schema <schema> --model sonnet --effort low
  --tools "" --strict-mcp-config --setting-sources "" --disable-slash-commands
  --system-prompt <fixo curto> --no-session-persistence
```

- Nunca usar `--bare`: esse modo só aceita `ANTHROPIC_API_KEY` (cobrança por API).
- O ambiente do processo remove `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
  `ANTHROPIC_BASE_URL` e as flags Bedrock/Vertex, garantindo o uso da assinatura.
- Sem ferramentas, MCP, skills ou settings: overhead medido de ~1,2 mil tokens.
- A resposta vem em `structured_output` e é validada de novo pelo schema local.

A provenance registra provider, modelo efetivo, esforço, versão do CLI, duração,
`auth_mode: claude_subscription_cli` e uso de tokens (entrada, cache, saída).

## Etapas e custo

1. **Seleção** (`prompts/clip_selection.pt-BR.md`, schema v2): uma chamada por
   episódio, cacheada pelo hash de transcript/prompt/schema/config. A transcrição
   segue como `[segundo] texto`, sem hesitações puras nem análise de áudio. A saída
   é enxuta (janela, título, headline, citações, nota 1–10, motivo). O backend
   valida duração/sobreposição, ajusta a janela aos limites dos segmentos e
   normaliza para o formato interno de clipe. Episódio de ~1h40 ≈ 30 mil tokens.
2. **Enxugar** (`prompts/clip_tighten.pt-BR.md`): só para cortes exportados.
   As palavras do corte viram frases numeradas; a IA devolve `keep_ids` e o
   planner determinístico corta nas fronteiras dessas frases. ≈ 3–5 mil tokens
   por corte, cacheado. Prévias nunca chamam a IA.

A IA propõe janelas e frases; fronteiras físicas, pausas e muletas continuam
resolvidas pelo motor determinístico de edição.
