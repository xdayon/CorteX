# Enxugamento de corte — @dayon.news

Você recebe um corte já selecionado, dividido em frases numeradas
(`phrases`, cada uma com `id`, `start`, `end`, `text`). Sua tarefa é decidir
quais frases permanecem no corte final, no estilo seco e dinâmico dos reels
de @dayon.news: sem gordura, sem repetição, sem enrolação.

Retorne **somente** o objeto JSON do schema, com `keep_ids` (os `id` das
frases mantidas, em ordem crescente) e `notes` (1 frase curta explicando o
que foi cortado).

## O que remover

- Digressões e tangentes que não servem ao caminho do gancho (`hook`) até a
  conclusão (`payoff`).
- Recomeços de frase e repetições da mesma ideia.
- Frases de preenchimento como "tipo", "né", "então assim", "sabe?", quando
  isoladas em sua própria frase.
- Qualquer trecho que não seja indispensável para o sentido.

## O que preservar

- A abertura (gancho) e a conclusão (payoff) do corte quase sempre devem ser
  mantidas.
- O sentido e a gramática nas junções entre frases mantidas: depois de
  remover frases, o que sobra precisa continuar fazendo sentido ao ser lido
  em sequência.
- Nunca reordene as frases; `keep_ids` deve manter a ordem original.
- Mantenha entre 65% e 90% da duração falada original. Não corte tanto que o
  corte perca contexto ou fique picotado demais.

## Regras de saída

- `keep_ids` não pode estar vazio.
- Não invente frases nem `id`s fora dos fornecidos.
- Escreva `notes` em português brasileiro, com acentos e cedilha corretos.
