# Triangulação nos cortes

Objetivo: o corte situa quem assiste no podcast. A fala do Dayon é a base, e entram
também o fim da pergunta do entrevistador (imagem e voz reais), os entrevistadores
ouvindo calados e o estúdio em plano aberto sem zoom. Quem assiste entende com quem ele
conversa e fica com vontade de ver a entrevista inteira.

Medição que motivou o plano: [camera-map probe](evidence/2026-10-19-camera-map-probe.md)
(a câmera dos entrevistadores fica no ar 46 % do episódio e só 4 % nos cortes).

## Por que as tentativas antigas quebraram

Elas encadeavam sinais frágeis e adivinhados: reconhecimento de rosto, falante pela
boca, identidade, planejador de câmera e candidatos a reação. Cada etapa errava um pouco
e os erros se somavam. Também não havia como saber pela voz quem falava.

## Princípios

- **Um mapa de câmeras por episódio confirmado por uma pessoa**, com um clique por
  grupo de planos, no lugar de reconhecimento facial.
- **Quem fala vem da voz** (perfil + voice match), nunca do rosto ou da boca.
- **Inserções só trocam a imagem.** O áudio do Dayon e a legenda seguem contínuos.
- **Toda inserção é sugestão revisável.** O automático só liga depois dos gates B–D.
- **Análise visual barata**: keyframes do episódio inteiro (~1 min para 2 h) e `scdet`
  só na janela de cada candidato escolhido.

## Tipos de plano

| Tipo | Imagem | Áudio | Condição |
| --- | --- | --- | --- |
| Fala (base) | câmera que estava no ar | Dayon | — |
| Pergunta | câmera que estava no ar | entrevistador, real | fim da pergunta (≤ ~6 s) logo antes da resposta |
| Reação | grupo "entrevistadores", de outro instante | o do Dayon continua | janela inteira com voz D e ≥ 0,4 s de qualquer O |
| Plano aberto | grupo "plano aberto", quadro inteiro sem zoom | o do Dayon continua | janela com voz D (Dayon gesticulando de fala) |

Papéis de grupo: `subject` (só o Dayon), `interviewers` (só entrevistadores),
`wide` (estúdio ou Dayon junto com entrevistador), `ignore` (vinheta, tela, publicidade).

Regras das sugestões (determinísticas):

- a inserção vem depois do gancho (≥ 3 s) e fica ≥ 1 s longe de junções e do teaser;
- dura 1,2–2,5 s, começa logo após o fim de uma frase, com espaçamento ≥ 8 s;
- os candidatos são ordenados por proximidade no tempo do episódio, estabilidade do plano e rosto visível.

## Estado (2026-10-20)

- Gate A pronto no branch `feat/triangulation`. Backend `analyze/camera_map.py`, job
  `camera_map`, endpoints `/episodes/{id}/cameras*`, tela "Câmeras do episódio" no menu
  do episódio. Validado num episódio real (evidência acima) e no Studio em 390 px e
  1440 px. Falta o Dayon confirmar 2–3 episódios.
- Próximo: Gate B (candidatos de reação e plano aberto por corte).

## Gates

- **A — Mapa de câmeras.** Artifact `camera_map` (grupos, planos, rostos, participação
  da voz D, papel sugerido) e papéis confirmados por episódio na tela "Câmeras".
  Validação: 2–3 episódios reais com os grupos corretos.
- **B — Candidatos.** Reações e planos abertos por corte, tocáveis na fonte, sem
  render. Validação: o Dayon aprova ≥ 80 % das sugestões.
- **C — Render.** Inserções no plano de edição e no render (segunda entrada do mesmo
  master, como o teaser), com a prévia draft igual ao export. Testes de pixel e áudio:
  áudio e legenda contínuos, sem deriva A/V.
- **D — Pergunta.** Seleção e refino podem abrir com o fim da pergunta (rótulo O).
  Mudar `prompts/` afeta a produção na hora: avaliar no worktree e só depois fazer o merge.
- **E — Automático.** "Aplicar triangulação" aceita as melhores sugestões. Fica ligado
  por padrão só depois de B–D aprovados.

Fora do escopo: reconhecimento facial, leitura labial, câmeras ISO e inserção sem revisão.
