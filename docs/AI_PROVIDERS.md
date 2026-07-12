# Providers de IA do CorteX

## Decisao atual

O provider editorial oficial e o Codex CLI, autenticado pela assinatura
ChatGPT. Claude CLI e o fallback. Nenhum caminho exige API key ou billing por
chamada de API, mas ambos consomem limites das respectivas assinaturas.

```yaml
ai:
  provider: codex_cli
  fallback_provider: claude_cli
  codex_binary: codex
  codex_model: gpt-5.5
  codex_reasoning_effort: medium
  claude_binary: claude
  claude_model: fable
  claude_effort: low
  timeout_seconds: 300
  max_input_chars: 800000
```

O Codex usa `gpt-5.5/medium`. Analise editorial e uma etapa de qualidade do
produto, portanto nao compartilha o default economico usado por subagentes de
desenvolvimento. Trocar o modelo deve ser uma decisao baseada em evals
editoriais, nao apenas em disponibilidade.

## Execucao Codex

Cada chamada cria um diretorio temporario vazio e executa `codex exec` com:

- `--ignore-user-config` e `--ignore-rules` para evitar instrucoes alheias;
- `--ephemeral` para nao persistir a sessao;
- `--sandbox read-only` e workspace vazio;
- `--output-schema` com o schema editorial versionado;
- `--output-last-message` para separar JSON final dos logs do CLI;
- stdin para o prompt, timeout e cancelamento cooperativo.

O host possui `codex-cli 0.144.1`. `gpt-5.5/medium` aceitou o schema editorial
completo e retornou JSON valido em aproximadamente 6 segundos, com fallback
desativado.

## Fallback Claude

Claude recebe o mesmo prompt e schema apenas quando Codex falha por processo,
timeout, cota, JSON invalido ou incompatibilidade de schema. Cancelamento do
usuario nunca aciona fallback. A provenance registra:

- `requested_provider` e `effective_provider`;
- `fallback_used` e `fallback_reason`;
- provider/modelo efetivos, versao do CLI e duracao;
- sessao e custo equivalente quando o CLI os fornecer.

O host validado possui Claude Code `2.1.204`, login `claude.ai` e assinatura
Pro. O smoke estruturado respondeu em 5,4 segundos. `total_cost_usd` retornado
pelo CLI e telemetria de custo equivalente e nao deve ser interpretado
automaticamente como cobranca de API.

## Contrato de seguranca e qualidade

- nenhum texto produzido pela IA e executado como shell;
- argumentos sao listas, sem interpolacao em comando;
- toda resposta e revalidada por JSON Schema Draft 2020-12;
- prompt, schema, transcript, brief e configuracao entram no hash de cache;
- fallback nunca e silencioso;
- a LLM escolhe intencao e regioes aproximadas, nao frames ou samples finais;
- um artifact existente so e reutilizado quando todos os hashes coincidem.

## Eficiencia de contexto

O provider recebe transcript compactado como `[inicio-fim] texto`, sem JSON de
palavras. Waveform, VAD e metricas densas permanecem locais. Episodios acima de
`max_input_chars` falham explicitamente ate existir map-reduce/chunks com ranking
global; truncamento silencioso nao e permitido.

Ollama continua sendo uma futura opcao offline. Nao deve ser instalado antes de
um benchmark neste hardware de 6 GB VRAM e de um conjunto de evals editoriais.
