# Treinamento do ChatGPT do Gabriel

Este texto explica ao Gabriel e ao Dayon como ligar o ChatGPT do Gabriel ao estúdio de aulas do CorteX. Os outros três arquivos vão para dentro do ChatGPT.

| Arquivo | Onde entra |
|---|---|
| `1-instrucoes-do-projeto.md` | Instruções do projeto (colar o texto) |
| `2-guia-visual.md` | Arquivos do projeto (upload) |
| `3-formatos-de-entrega.md` | Arquivos do projeto (upload) |

## Quem faz o quê
| Quem | Faz | Entrega |
|---|---|---|
| **Gabriel + ChatGPT** | desenho do curso, tema de cada aula, roteiro fiel ao paper, escolha do tipo de cada tomada, prompts do Flow, briefs do Manim e do avatar | narração (texto) e, depois, o pacote visual |
| **Dayon** | gera a narração na ElevenLabs (voz do Robert), os clipes no Flow e os vídeos do avatar no HeyGen | MP3 e clipes na pasta de ingest |
| **CorteX** (Claude + código) | alinha a narração palavra por palavra, gera a tabela de tempos, valida o pacote, programa e renderiza o Manim, confere a matemática, revisa os clipes e monta a aula | tabela de tempos, folha de revisão, vídeo final 1080p |

## Montar no ChatGPT (uma vez)
1. Crie um **Projeto** no ChatGPT, por exemplo "Estúdio, curso Robert Grant". Se o Gabriel já usa um GPT personalizado, os mesmos textos servem: as instruções vão em "Instruções" e os arquivos em "Conhecimento".
2. Nas **instruções do projeto**, cole o texto de `1-instrucoes-do-projeto.md`.
3. Nos **arquivos do projeto**, suba:
   - `2-guia-visual.md`;
   - `3-formatos-de-entrega.md`;
   - os papers do Robert em PDF;
   - a ementa do curso, quando existir.

## Quantos chats
- **Um chat "Ementa" (opcional):** desenha o curso, com a lista de aulas, o tema e as seções do paper de cada uma. O resultado vira um arquivo do projeto.
- **Um chat por aula, em duas etapas** (roteiro e pacote visual). Não vale a pena separar narração, Flow, Manim e HeyGen em chats diferentes:
  - A escolha do tipo de cada tomada é uma decisão só. Ela depende da proporção entre os tipos, da variedade e de saber o que é equação.
  - O chat da aula já tem o roteiro e a intenção de cada fala.
  - O pacote sai separado por ferramenta mesmo assim, com cada prompt no seu bloco de copiar.
- **A Etapa 2 espera a narração gravada.** A duração de cada fala só existe depois da ElevenLabs: no piloto, o ritmo variou de 1,9 a 3,8 palavras por segundo entre as falas. E o crédito do Flow depende da duração pedida.

## Uma aula, passo a passo
1. **Gabriel:** `ROTEIRO <tema da aula>`. Revisa e aprova, e manda ao Dayon o bloco "Narração".
2. **Dayon:** gera a narração na ElevenLabs e traz o MP3 para o CorteX.
3. **CorteX:** alinha a narração e devolve a tabela de tempos. O Dayon repassa ao Gabriel.
4. **Gabriel:** `TEMPOS` e cola a tabela. Revisa o pacote visual e manda ao Dayon a resposta inteira: o botão de copiar da resposta já sai em markdown, e o texto pode ir num arquivo `.md`.
5. **CorteX:** valida o pacote (cobertura das falas, durações, limites do Flow), recalcula as falas divididas e corta os trechos de áudio do avatar.
6. **Dayon:** gera os clipes do Flow com a duração e as variações indicadas, e os vídeos do HeyGen com os trechos de áudio. O CorteX gera as cenas Manim.
7. **CorteX:** monta a folha de revisão dos clipes (letras inventadas, movimento ruim). Se um clipe falhar, o Gabriel pede `REFAZER S07: <motivo>` e o Dayon gera de novo.
8. **CorteX:** monta a aula, com a música bem baixa e o volume no padrão do YouTube, e exporta em 1080p.

## O que muda em relação a hoje
- **Duração do Flow:** 4, 6, 8 ou 10 s, pela regra das instruções, em vez de 10 s sempre. Gasta menos crédito e evita imagem congelada por clipe esticado.
- **Tempos:** a tabela do CorteX substitui o SRT. Ela traz o tempo exato de cada parágrafo e já sugere a duração do Flow.
- **Manim:** o ChatGPT entrega o brief (o que mostrar, a matemática exata, em que palavra), não código. Ele poderia escrever Python, mas:
  - não vê o resultado renderizado;
  - a identidade visual e a sincronia com a voz estão no CorteX;
  - a matemática é conferida aqui de qualquer jeito.

  Com a biblioteca de modelos, a maior parte das cenas vai sair do brief sem programar nada.
- **Avatar:** a tomada AVATAR é nova. O clone do Robert usa o mesmo áudio da narração, então a voz não muda e o lábio sincroniza com a montagem.

## Limites de cada lado
- **ChatGPT:**
  - não ouve a narração, só lê a tabela;
  - pode errar uma conta de duração (o CorteX confere);
  - pode exagerar o que o paper diz (por isso a regra de fidelidade e o [VERIFICAR]);
  - não vê os clipes.
- **Google Flow:**
  - máximo de 720p e 10 s;
  - escreve letras e números mesmo quando o prompt proíbe;
  - faz mal transformações geométricas precisas.
- **HeyGen:** as opções de fundo, enquadramento e gestos dependem do avatar do Robert e do plano, e ainda vamos confirmar quais são.
- **CorteX:**
  - não escreve o curso;
  - cena Manim nova custa tokens do Claude (os modelos prontos reduzem esse custo);
  - a leitura automática do pacote e a tomada de avatar ainda estão em construção. Até ficarem prontas, o Dayon faz essa parte com o Claude.

## Pendências
- **Dayon:** mandar um print do painel do HeyGen (fundo, resolução, upload de áudio) para fechar as regras do avatar.
- **Combinar com o Gabriel:**
  - a duração-alvo das aulas do curso;
  - quais papers entram no projeto;
  - quem aprova o roteiro antes da gravação.
