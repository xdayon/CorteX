# Guia visual do curso

Este guia vale para todas as aulas. Os exemplos vêm da aula-piloto "i as a quarter-turn", que foi aprovada.

## Identidade
- **Mundo:** cinematografia fotorrealista, geometria sagrada, natureza, arquitetura monumental, cosmos. A câmera é fluida, com transições do micro ao cósmico.
- **Paleta:** azul-meia-noite e preto, luz dourada quente, acentos de cobre e azul-gelo.
  - O Manim usa a mesma paleta: fundo azul-noite, dourado, cobre, gelo e marfim, com a fonte serifada EB Garamond.
  - Por isso Flow e Manim se alternam sem quebrar o visual.
- **Música:** clássica e calma, bem baixa. O Robert gosta da voz em destaque.

## Google Flow

### Limites reais
O modelo é o Omni 1.1 Flash.

| Item | Limite |
|---|---|
| Proporção | 16:9 |
| Resolução | 720p; o CorteX amplia para 1080p |
| Duração | 4, 6, 8 ou 10 s |
| Variações | x1 a x4 por prompt |
| Áudio | o do clipe é descartado |

A duração a pedir segue a tabela das instruções. Pedir mais do que a fala usa gasta crédito à toa. Pedir menos obriga a esticar o clipe, e acima de 1,25× a imagem fica congelada: isso aconteceu no piloto, com um clipe esticado 3,65×.

### Estrutura do prompt
```text
NN Photorealistic cinematic shot, D seconds. <lugar, objeto principal, materiais, luz>. 0-Xs: <uma ação simples>. X-Ds: <uma ação simples>. Camera: <um movimento>. Style: photorealistic, cinematic, large-format camera, shallow depth of field, volumetric light, subtle film grain. Palette: deep midnight blue and black, warm gold light, accents of copper and pale ice-blue. No text, no letters, no numbers, no symbols, no people, no faces.
```
- **NN** é o número da tomada, com dois dígitos (S07 → "07"). O Flow nomeia o arquivo baixado a partir do início do prompt (`07_-_….mp4`), e é assim que o CorteX encaixa o clipe.
- **D** é a duração pedida. As marcações de tempo somam exatamente D.
- **Para cair na palavra certa:** uma palavra na posição k de n, num beat de t segundos, é falada por volta de k/n × t. No clipe, some 0,2 s do dissolve de entrada. No piloto, a esfera da cena 02 sai do lugar logo depois de "line".

### O que funciona e o que falha
| Funciona | Falha |
|---|---|
| Ponto de luz que desliza deixando rastro de luz | Morph geométrico: "feixe que se curva como vidro" virou uma mangueira que entorta e espirra |
| Pêndulo de prumo que oscila e assenta | Mostradores, mapas, livros, relógios, placas: o Flow escreve neles (uma bússola veio com "120" ao lado do W) |
| Escada espiral que gira exatamente um quarto | Números grandes ou contagens exatas acima de 3 |
| Engrenagens que acendem uma a uma | Pessoas, rostos, mãos |
| Câmera lenta: dolly, crane, push-in, órbita | Várias ações no mesmo trecho |

Para evitar letras:
- descreva superfícies como "plain, unmarked, without markings";
- prefira objetos que não têm escrita.

### Metáfora concreta
Cada clipe traduz a ideia do beat num objeto e num movimento, nunca numa metáfora genérica. Exemplos do piloto:

| Ideia | Cena |
|---|---|
| i como movimento, não ponto | esfera que sai da linha e traça um quarto de círculo |
| quarto de volta que fecha em dez | escada espiral com três degraus acesos girando um quarto |
| par recíproco (um número e menos o seu inverso) | balança antiga: esfera grande de ouro num prato, esfera minúscula no outro |
| o espelho x → −1/x | lago parado que reflete um arco de luz |
| 116 verificações | parede de engrenagens acendendo |
| "para ser preciso" | prumo que para em repouso |
| história do conceito | escritório do século XIX à luz de vela, compasso de latão |

### Variedade
- Duas tomadas Flow seguidas nunca no mesmo lugar.
- Evite olhos, galáxias e silhuetas: viram clichê quando se repetem.
- Nunca pessoas no Flow; o Robert aparece só como AVATAR.

### HYBRID
O painel Manim fica por cima do Flow. O prompt precisa deixar espaço vazio e escuro onde o painel entra: "dark negative space at the center of the frame", ou "the right half of the frame stays calm and dark". Movimento suave, sem nada importante atrás do painel.

### Exemplos aprovados
8 s (beat de 8,24 s, necessário 8,64):
```text
12 Photorealistic cinematic shot, 8 seconds. Inside a monumental tower of pale carved stone, a wide spiral staircase rises into darkness around a central column. Exactly three broad steps glow from within: the first warm gold, the second copper, the third pale ice-blue, together turning precisely one quarter of the way around the column. 0-8s: the camera rises smoothly over the three glowing steps, and at the top a tall doorway opens onto a soft field of light. Camera: slow upward crane combined with a quarter-turn orbit around the column. Style: photorealistic, cinematic, large-format camera, shallow depth of field, volumetric light, subtle film grain. Palette: deep midnight blue and black, warm gold light, accents of copper and pale ice-blue. No text, no letters, no numbers, no symbols, no people, no faces.
```
6 s (beat de 5,24 s, necessário 5,64; a saída acompanha "What if it is a motion?"):
```text
02 Photorealistic cinematic shot, 6 seconds. A vast plain of polished black obsidian at night under a deep midnight-blue sky, crossed by a thin, perfectly straight horizontal line of warm golden light that runs to the horizon. 0-3s: a single small sphere of pale ice-blue light rests perfectly still on the golden line, just right of the center of the frame, mirrored in the obsidian. 3-5s: the sphere lifts off and glides smoothly along a perfect quarter-circle arc, rising up and to the left, leaving behind a continuous thin trail of pale light, like a long-exposure light painting. 5-6s: the sphere hovers motionless at the top of the arc. Camera: locked-off tripod, perfectly still, eye-level wide shot, the whole arc inside the frame. Style: photorealistic, cinematic, large-format camera, shallow depth of field, volumetric light, subtle film grain. Palette: deep midnight blue and black, warm gold light, accents of copper and pale ice-blue. No text, no letters, no numbers, no symbols, no people, no faces.
```
4 s, HYBRID (beat de 3,55 s, necessário 3,95; o contador do Manim entra no centro):
```text
15 Photorealistic cinematic shot, 4 seconds. A monumental wall of ancient interlocking bronze and brass gears inside a dark, cathedral-like hall, every gear turning in perfect synchrony, with clean precise teeth and completely plain surfaces without dials or markings. 0-4s: one by one, small points of warm golden light ignite at the gear axles until the whole mechanism glows softly. Camera: slow steady dolly back, dark negative space at the center of the frame. Style: photorealistic, cinematic, large-format camera, shallow depth of field, volumetric light, subtle film grain. Palette: deep midnight blue and black, warm gold light, accents of copper and pale ice-blue. No text, no letters, no numbers, no symbols, no people, no faces.
```

## Manim

### Para que serve
Para tudo que precisa estar exato: equações, matrizes, gráficos, ângulos, escalas, contagens. Você descreve o que mostrar, e o CorteX programa, renderiza e confere a matemática.

### Modelos prontos
Use o id do modelo no brief. "nova" só quando nenhum serve; nesse caso, descreva a cena por completo.

| modelo | mostra | no piloto |
|---|---|---|
| `rotating_plane` | pontos num plano girando passo a passo ((x, y) → (y, −x); quatro passos voltam ao início) | S03 |
| `matrix_product` | a matriz aparece e o produto é montado na tela (J·J = −I) | S04 |
| `glass_equations` | painel de vidro com 1 a 3 equações sobre o Flow (overlay) | S05, S14 |
| `reciprocal_lines` | reta com rótulos recíprocos e o espelho x → −1/x | S06 |
| `function_graph` | gráfico de função com um parâmetro variando (overlay) | S07 |
| `complex_map` | a + bi vira aI + bJ; círculo e e^{θJ} | S08 |
| `angle_ladder` | passos de ângulo que somam, com tabela de valores | S10 |
| `log_scale` | escala logarítmica com dígitos em destaque | S13 |
| `check_counter` | contador que soma verificações até um total (overlay) | S15 |
| `end_card` | cartão final: título, autor e subtítulo | S18 |

### Regras da tela
- Texto em inglês e curto: rótulos de até 6 palavras e no máximo 3 equações ao mesmo tempo.
- Mostre exatamente o que a narração diz, nem mais nem menos. Cada passo entra numa palavra da narração (a sincronia do brief).
- **Layout:**
  - `tela_cheia`: fundo azul-noite do Manim;
  - `overlay`: painel de vidro sobre o Flow da mesma tomada;
  - `lado`: ao lado do Robert numa tomada de avatar.
- A matemática do brief precisa estar certa: o CorteX confere com SymPy e devolve o que não fechar. Se não tiver certeza de um sinal ou valor, escreva [VERIFICAR].

## Avatar (HeyGen, clone do Robert)

### Quando usar
- Na abertura (o gancho), no fechamento e nas falas em primeira pessoa ("So I built…").
- No máximo 3 por aula, de 4 a 15 s cada: tomadas longas de avatar parecem artificiais.
- Nunca num beat de matemática densa: ali o espectador precisa ver a equação.

### Áudio
É o trecho da própria narração da ElevenLabs. O CorteX corta o trecho com uma pequena folga e o Dayon sobe no HeyGen. Assim a voz é idêntica ao resto da aula e os lábios ficam sincronizados com a montagem. Nunca reescreva a fala para o avatar. Se a fala precisa mudar, mude o roteiro.

### Brief
Enquadramento, fundo, energia e expressão, gestos (se o avatar permitir) e um painel Manim opcional ao lado (layout `lado`).
- O fundo combina com a paleta, por exemplo uma biblioteca escura com luz dourada.
- Se houver painel, o Robert fica deslocado para um lado.
- As opções reais do HeyGen dependem do avatar e do plano. O brief descreve a intenção, e o Dayon aplica o que o painel permitir.

### Nome do vídeo no HeyGen
Começa com o número da tomada ("01 abertura"), como no Flow.

## Cartão final
O CorteX sempre coloca um cartão final de 4 s (`end_card`). Você informa no cabeçalho da Etapa 2 o título, a linha de autor e o subtítulo, em inglês. O cartão não precisa de beat nem de narração.
