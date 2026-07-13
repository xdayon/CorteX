# Dataset de avaliação editorial (Gate 8)

Conjunto versionado de decisões humanas (aprovado/reprovado) sobre trechos
sugeridos pelo pipeline de seleção, usado pelo runner offline
(`scripts/eval_selection.py`) para medir cobertura, overlap indevido,
distribuição de duração, completude de campos e segurança de boundaries —
e para comparar mudanças de prompt/provider antes/depois.

## Formato

Cada arquivo `.json` neste diretório (exceto `schema.json`) é **uma entrada
de dataset por episódio/trecho**, validada por `schema.json`
(JSON Schema draft 2020-12, `dataset_schema_version: 1`):

```json
{
  "dataset_schema_version": 1,
  "episode": {
    "title": "string",
    "source_sha256": "hash sha256 do arquivo de mídia fonte",
    "duration_seconds": 5901.03,
    "transcript_artifact_hash": "audio_sha256 ou id do artefato de transcrição"
  },
  "decisions": [
    {
      "clip_ref": { "start_second": 5041.26, "end_second": 5111.83 },
      "verdict": "approved",
      "theme": "string",
      "duration_seconds": 70.57,
      "hook_summary": "string",
      "payoff_summary": "string",
      "speakers": ["Dayan"],
      "reason": "motivo da decisão humana (por que aprovar/reprovar)",
      "decided_by": "identificador de quem decidiu, ou \"pending_human_review\"",
      "decided_at": "timestamp ISO-8601"
    }
  ]
}
```

## Regra de privacidade: nenhuma mídia no Git

Este diretório **nunca** contém arquivos de mídia, caminhos de arquivo local,
nem qualquer campo que aponte para um arquivo fora do repositório. Apenas:

- hashes (`source_sha256`, `transcript_artifact_hash`);
- timestamps e metadados textuais (título, tema, resumos, motivos);
- intervalos de tempo em segundos (`start_second`/`end_second`).

`schema.json` reforça isso com `additionalProperties: false` em todos os
objetos — nenhum campo de path pode ser adicionado sem alterar o schema
explicitamente, e nenhuma propriedade do schema aceita um caminho de arquivo.
Ao adicionar uma nova entrada, confirme visualmente que nenhum valor é um
caminho local (`/`, `~`, letra de unidade, etc.) antes de commitar.

## Decisões pendentes de revisão humana

Quando uma entrada é derivada de um artifact de sugestão existente e ainda
não passou por revisão humana real, use `decided_by: "pending_human_review"`
para deixar isso explícito. O runner (`scripts/eval_selection.py`) trata
essas decisões como dados de referência "honestos, mas não confirmados" —
elas ainda participam das métricas de forma e de completude, mas o baseline
de cobertura (Gate 8) só é comparado contra a fração de decisões com
`decided_by` diferente de `pending_human_review`, quando houver alguma.

## Adicionando uma nova entrada

1. Rode o pipeline até o artifact `suggestion` do episódio.
2. Preencha `episode` com hash do arquivo fonte e da transcrição (nunca o
   caminho).
3. Para cada clip sugerido (ou trecho relevante), registre uma `decision`
   com o veredito humano real, ou `pending_human_review` se ainda não houve
   revisão.
4. Valide contra `schema.json` (o runner e os testes fazem isso
   automaticamente).
