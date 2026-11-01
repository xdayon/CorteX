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

- **Um mapa de câmeras por episódio**, feito sozinho depois da seleção da IA, no lugar de
  reconhecimento facial. Os papéis vêm da voz (participação D) e dos rostos; uma pessoa
  pode conferir com um clique por grupo, e o que ela confirma vale.
- **Quem fala vem da voz** (perfil + voice match), nunca do rosto ou da boca.
- **Inserções só trocam a imagem.** O áudio do Dayon e a legenda seguem contínuos.
- **Automático e revisável.** Desde 01/nov o corte sem escolha própria
  (`RenderSettings.inserts = None`) recebe todas as sugestões; qualquer ajuste na aba
  Cenas vira a lista do corte, e "Voltar ao automático" desfaz.
- **Análise visual barata**: keyframes do episódio inteiro (~1 min para 2 h) e 3–5
  quadros de 160×90 só da imagem emprestada escolhida (`footage_check`). O mapa por
  keyframes perde cortes sem keyframe, comuns no master do YouTube.

## Tipos de plano

| Tipo | Imagem | Áudio | Condição |
| --- | --- | --- | --- |
| Fala (base) | câmera que estava no ar | Dayon | — |
| Pergunta | câmera que estava no ar | entrevistador, real | fim da pergunta (≤ ~6 s) logo antes da resposta |
| Reação | grupo "entrevistadores", de outro instante | o do Dayon continua | janela inteira com voz D e ≥ 0,4 s de qualquer O |
| Plano aberto | grupo "plano aberto", quadro inteiro sem zoom | o do Dayon continua | janela com voz D (Dayon gesticulando de fala) |
| Plano aberto ao vivo | a câmera no ar naquele instante, quadro inteiro sem zoom | Dayon, em sincronia | quando não há imagem de estúdio utilizável |

Papéis de grupo: `subject` (só o Dayon), `interviewers` (só entrevistadores),
`wide` (estúdio ou Dayon junto com entrevistador), `ignore` (vinheta, tela, publicidade).
Grupo de 2 rostos com pouca voz D (≤ 12 %) é sugerido como `wide`: mostrado inteiro,
serve tanto para dois apresentadores quanto para o Dayon com o apresentador à mesa,
enquanto uma reação enquadrada num rosto poderia cair no próprio Dayon.

Regras das sugestões (determinísticas):

- a inserção vem depois do gancho (≥ 3 s) e fica ≥ 1 s longe de junções e do teaser;
- dura 1,2–2,5 s, começa logo após o fim de uma frase, com espaçamento ≥ 8 s;
- no mínimo 2 e no máximo uma a cada 15 s; a primeira é plano aberto (situa quem
  assiste), depois reação, reação, plano aberto…; as últimas vagas vão para o tipo que
  faltar, o entrevistador primeiro; corte com uma vaga só mostra o entrevistador;
- os candidatos são ordenados por proximidade no tempo do episódio, estabilidade do plano
  e rosto visível; reação de grupo com 2 rostos vale 0,7; o plano aberto ao vivo vale
  0,35 (plano a dois) ou 0,2 (close), abaixo da imagem real de estúdio;
- a imagem emprestada começa no início do trecho utilizável do plano (decodificar é
  barato perto do keyframe) e só entra se todos os quadros conferidos caírem num grupo
  com o papel certo.

## Estado (2026-10-23)

- Gate A no `main`. Papel sugerido para grupos de 2 rostos refeito pela voz D:
  - ≥ 30 % → plano a dois com o Dayon (subject);
  - ≤ 12 % → entrevistadores (desde 01/nov: plano aberto);
  - entre os dois → a pessoa decide.
  O papel é recalculado na leitura, sem refazer mapas.
- Branch `feat/triangulation-scenes`
  ([evidência](evidence/2026-10-23-scene-suggestions.md)):
  - **B**: `analyze/scene_suggestions.py` + `GET /episodes/{id}/scenes`. As decisões
    ficam em `PUT /episodes/{id}/scene-reviews`, que mede a aprovação.
  - **C**: `RenderSettings.inserts` por corte. O render troca só a imagem, com uma
    entrada própria por inserção, e grava o manifesto em `RenderDocument.inserts`.
  - **D, versão determinística**: `transitions.question` por corte →
    `question_start/end` no plano (segmentos `question`). O prompt não muda.
  - **E**: botão "Aplicar triangulação" na aba Cenas, desligado por padrão.
  - **UI**:
    - etapas do episódio: vídeo, cortes, voz, câmeras;
    - aba Cenas e trilha "Cenas" no editor;
    - perfil de voz no diálogo de voz.

## Estado (2026-11-01): automático por padrão

Pedido do Dayon: todo corte com as falas dele, o cenário e pelo menos uma cena do
entrevistador. Branch `feat/triangulation-auto`
([evidência](evidence/2026-11-01-auto-triangulation.md)):

- composição v2 em `scene_suggestions.py` (regras acima) e plano aberto ao vivo;
- `triangulation.py`: a API e o worker de render calculam as mesmas sugestões; o render
  resolve `inserts = None` antes da chave de cache e grava `triangulation` no resultado;
- `footage_check.py`: confere a imagem emprestada, com cache por mapa;
- o mapa de câmeras entra na fila sozinho depois da seleção (com perfil de voz);
- aba Cenas: "Automáticas", ajuste à mão, "Voltar ao automático" e "Conferir câmeras";
  o mapa começa sozinho quando falta.

Falta: o Dayon revisar cortes automáticos (meta ≥ 80 % das cenas mantidas) e a seleção
da IA preferir cortes com pergunta (prompt avaliado em worktree).

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
