# Motor editorial do CorteX

## Decisao atual

O unico motor editorial e o Codex CLI, autenticado pela assinatura ChatGPT.
O CorteX nao usa Claude, heuristica local ou troca automatica de provider. Uma
falha do Codex permanece visivel no job para que o usuario possa corrigir a
causa e tentar novamente, sem degradacao silenciosa de qualidade.

```yaml
ai:
  codex_binary: codex
  codex_model: gpt-5.5
  codex_reasoning_effort: medium
  timeout_seconds: 300
  max_input_chars: 800000
```

O Codex usa `gpt-5.5/medium`. A selecao editorial e uma etapa de qualidade do
produto; trocar o modelo deve ser uma decisao baseada em avaliacoes editoriais,
nao em disponibilidade ocasional.

## Execucao

Cada chamada cria um diretorio temporario vazio e executa `codex exec` com:

- `--ignore-user-config` e `--ignore-rules` para evitar instrucoes alheias;
- `--ephemeral` para nao persistir a sessao;
- `--sandbox read-only` e workspace vazio;
- `--output-schema` com o schema editorial versionado;
- `--output-last-message` para separar o JSON final dos logs;
- prompt via stdin, timeout e cancelamento cooperativo.

A provenance registra `provider: codex_cli`, modelo, nivel de raciocinio,
versao do binario, duracao e modo de autenticacao. Nao ha campos de fallback.

## Contrato de seguranca e qualidade

- nenhum texto produzido pela IA e executado como shell;
- argumentos sao listas, sem interpolacao em comando;
- toda resposta e revalidada por JSON Schema Draft 2020-12;
- prompt, schema, transcript, brief e configuracao entram no hash de cache;
- a IA escolhe intencao e regioes aproximadas, nao frames ou samples finais;
- um artifact existente so e reutilizado quando todos os hashes coincidem;
- erro de processo, timeout, cota, JSON invalido ou schema invalido falha o job
  explicitamente e nunca aciona outro motor.

## Eficiencia de contexto

O Codex recebe transcript compactado como `[inicio-fim] texto`, sem JSON de
palavras. Waveform, VAD e metricas densas permanecem locais. Episodios acima de
`max_input_chars` falham explicitamente ate existir analise em chunks com
ranking global; truncamento silencioso nao e permitido.
