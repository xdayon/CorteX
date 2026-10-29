# Formatos de entrega

O CorteX lê estas respostas automaticamente. Mantenha os cabeçalhos, os ids, a ordem e os blocos de código exatamente como abaixo.

## Etapa 1: roteiro (comando ROTEIRO)

### Modelo
````
# ETAPA 1 · <slug-em-inglês> · <título em inglês>
Fonte: <paper(s) e seções>
Duração-alvo: <min> · Palavras: <n> (≈ <n ÷ 2,7> s)

## Narração
```text
<parágrafo 1>

<parágrafo 2>
```

## Beats
| beat | palavras | tipo previsto | fonte | ideia visual |
|---|---|---|---|---|
| S01 | … | … | … | … |
````

### Regras
- **O bloco "Narração" vai inteiro para a ElevenLabs e para o CorteX.**
  - Parágrafos separados por uma linha em branco, sem ids, títulos, marcações ou indicações de pausa.
  - Cada parágrafo é um beat, e o CorteX numera os beats S01, S02… na ordem.
- **Tabela de beats:** uma linha por parágrafo, na mesma ordem.
  - "Tipo previsto" e "ideia visual" são o rascunho da Etapa 2.
  - "Fonte" é a seção do paper, ou "—" em beats sem afirmação.

### Exemplo (início do piloto)
````
# ETAPA 1 · i-quarter-turn · i as a quarter-turn
Fonte: A Real Operator Realization of the Imaginary Unit in a Reciprocal-Polarity Plane (Grant, 2026)
Duração-alvo: 3 min · Palavras: 446 (≈ 165 s)

## Narração
```text
We were all taught that i is a number whose square is minus one. No real number can do that. So mathematics gave it an axis of its own, and called it imaginary.

But what if i was never meant to be a point on a line? What if it is a motion?
```

## Beats
| beat | palavras | tipo previsto | fonte | ideia visual |
|---|---|---|---|---|
| S01 | 33 | AVATAR | — | Robert abre a aula, olhando para a câmera |
| S02 | 20 | FLOW | — | ponto de luz parado numa linha que sai e traça um arco |
````

## Etapa 2: pacote visual (comando TEMPOS)

### Modelo
````
# ETAPA 2 · <slug>
Cartão final: "<título>" / "<autor>" / "<subtítulo>"
Trilha: <sugestão: peça clássica calma, com licença livre a confirmar>

## Tomadas
| tomada | beats | tipo | duração (s) | necessário (s) | Flow |
|---|---|---|---|---|---|
| S01 | S01 | AVATAR | 10.90 | — | — |

## Divisões
- <beat> → <beat>a + <beat>b · corte depois de: "<últimas palavras da parte a>"

## <tomada> · <TIPO> · beats <beats> [· pedir <D> s · x<N>] [· v<versão>]
<bloco(s) da tomada>
````

### Regras
- **Cobertura:** cada beat pertence a exatamente uma tomada, na ordem da narração.
  - Uma tomada pode juntar beats seguidos (`beats S10+S11`).
  - O id da tomada é o do seu primeiro beat.
- **Duração:** soma das durações da tabela de tempos. "Necessário" e "Flow" seguem a regra das instruções.
  - Linhas sem Flow levam "—".
  - Uma parte de beat dividido leva "~" na duração, porque é estimada pela proporção de palavras. O CorteX recalcula o tempo exato antes de o Dayon gerar.
- **Divisões:** a seção só aparece se houver divisão.
- **Blocos de cada tipo:**
  - FLOW: um bloco `text` com o prompt.
  - MANIM: um bloco `yaml` com o brief.
  - HYBRID: o bloco `text` do Flow e, em seguida, o `yaml` do painel (layout `overlay`).
  - AVATAR: um bloco `yaml`; o campo `painel` é opcional.
- **Versão:** em REFAZER, o cabeçalho ganha `· v2` (v3, …), e só esse bloco é reenviado.
- **Ordem:** os blocos seguem a ordem das tomadas. O cartão final não entra na lista: é só a linha do cabeçalho.

### Campos dos briefs
```yaml
# MANIM (ou o painel de um HYBRID / AVATAR)
modelo: matrix_product        # id do guia visual, ou "nova"
layout: tela_cheia            # tela_cheia | overlay | lado
objetivo: "<uma frase, em inglês, do que a cena prova ou mostra>"
conteudo:                     # LaTeX exato, na ordem em que aparece
  - '<equação>'
sincronia:                    # palavra da narração: o que acontece
  - "<palavra>": <passo>
texto_na_tela: "<opcional, até 6 palavras, em inglês>"
fonte: "<paper, seção>"
```
```yaml
# AVATAR
enquadramento: "<plano médio / close; Robert ao centro ou deslocado>"
fundo: "<cenário que combina com a paleta>"
energia: "<tom e expressão>"
gestos: "<se o avatar permitir>"
painel: nenhum                # ou um brief MANIM completo com layout: lado
```

### Exemplo (trecho do piloto)
Durações reais da aula aprovada, com S01 como avatar. O piloto não teve avatar; numa aula nova, a S01 seria uma boa candidata.

````
# ETAPA 2 · i-quarter-turn
Cartão final: "The imaginary unit as a quarter-turn" / "Robert Edward Grant" / "A Real Operator Realization of the Imaginary Unit in a Reciprocal-Polarity Plane"
Trilha: Goldberg Variations, Var. 25, Kimiko Ishizaka, Open Goldberg (CC0)

## Tomadas
| tomada | beats | tipo | duração (s) | necessário (s) | Flow |
|---|---|---|---|---|---|
| S01 | S01 | AVATAR | 10.90 | — | — |
| S02 | S02 | FLOW | 5.24 | 5.64 | 6 s · x1 |
| S04 | S04 | MANIM | 10.66 | — | — |
| S05a | S05a | HYBRID | ~10.07 | ~10.47 | 10 s · x1 |
| S05b | S05b | FLOW | ~3.10 | ~3.50 | 4 s · x1 |
| S15 | S15 | HYBRID | 3.55 | 3.95 | 4 s · x1 |

## Divisões
- S05 → S05a + S05b · corte depois de: "and minus its inverse."

## S01 · AVATAR · beats S01
```yaml
enquadramento: "plano médio, Robert ao centro, olhando para a câmera"
fundo: "biblioteca escura com estantes de madeira e luz dourada quente lateral"
energia: "calmo e curioso, como quem vai contar um segredo simples"
gestos: "mãos em repouso; um gesto aberto em 'called it imaginary'"
painel: nenhum
```

## S02 · FLOW · beats S02 · pedir 6 s · x1
```text
02 Photorealistic cinematic shot, 6 seconds. A vast plain of polished black obsidian at night … (prompt completo como no guia visual)
```

## S04 · MANIM · beats S04
```yaml
modelo: matrix_product
layout: tela_cheia
objetivo: "The quarter-turn written as a matrix J; J times J is exactly minus the identity."
conteudo:
  - 'J = \begin{pmatrix} 0 & 1 \\ -1 & 0 \end{pmatrix}'
  - 'J \cdot J = \begin{pmatrix} -1 & 0 \\ 0 & -1 \end{pmatrix} = -I'
sincronia:
  - "matrix": a matriz J aparece
  - "twice": o produto J·J é montado
  - "minus one": o resultado −I acende em dourado
texto_na_tela: "built from real numbers alone"
fonte: "A Real Operator Realization of the Imaginary Unit, § (seção que define J)"
```

## S05a · HYBRID · beats S05a · pedir 10 s · x1
```text
05 Photorealistic cinematic shot, 10 seconds. A monumental antique balance scale of dark bronze and brass … (cena com espaço escuro à direita para o painel)
```
```yaml
modelo: glass_equations
layout: overlay
objetivo: "An exchange squares to minus one only when its coefficients are a reciprocal pair."
conteudo:
  - 'K = \begin{pmatrix} 0 & a \\ b & 0 \end{pmatrix}'
  - 'K^2 = -I \iff b = -\tfrac{1}{a}'
sincronia:
  - "exchange": aparece K
  - "forced": aparece a condição
  - "minus its inverse": −1/a acende em dourado
fonte: "A Real Operator Realization of the Imaginary Unit, § (seção do par recíproco)"
```

## S15 · HYBRID · beats S15 · pedir 4 s · x1
```text
15 Photorealistic cinematic shot, 4 seconds. A monumental wall of ancient interlocking bronze and brass gears … (prompt completo como no guia visual)
```
```yaml
modelo: check_counter
layout: overlay
objetivo: "All one hundred and sixteen exact and high-precision checks pass."
conteudo:
  - '79 + 29 + 8 = 116'
sincronia:
  - "One hundred and sixteen": o contador chega a 116
  - "pass": o total acende em dourado
fonte: "A Real Operator Realization of the Imaginary Unit, § (seção das verificações)"
```
````

No pacote real, escreva todos os prompts por inteiro. Os "…" acima só encurtam o exemplo.
