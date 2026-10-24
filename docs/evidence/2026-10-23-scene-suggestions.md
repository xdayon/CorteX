# Sugestões de cena e inserções — 2026-10-23

Episódio `wk7PY31TQBc` (9906 s, papéis de câmera confirmados pelo Dayon), cópia CoW de
`data/`, branch `feat/triangulation-scenes`.

## Prévias quebradas (corrigido antes de tudo)

Todas as prévias com transição em fade falhavam: `xfade` recebia uma entrada vinda de
`concat` (base de tempo 1/1000000) e outra do trim (1/30). Aconteciam dois casos: um
corte seco antes de um fade e um trecho dividido pelo foco nos rostos. Agora as duas
entradas passam por `settb=1/fps`. Há regressão com FFmpeg real para os dois casos em
`test_face_focus_render.py`.

## Sugestões (Gate B), 6 cortes reais

- Cálculo determinístico, sem decodificar mídia: cerca de 15 ms por corte.
- Cada corte recebe de 3 a 6 sugestões, no máximo uma a cada 15 s, espaçadas por pelo
  menos 8 s e sempre depois do gancho de 3 s.
- A imagem dos entrevistadores vem de planos em que só a voz do Dayon fala, com margem
  de 0,4 s para a voz dos outros, e nunca de trechos que o próprio corte já mostra.
- Plano aberto: o único grupo de 3 rostos (`g10`) tem pouca voz D, então o mesmo trecho
  volta em vários cortes, às vezes de 40 a 70 min de distância.
- Pergunta do entrevistador: detectada em 2 de 6 cortes, com o fim do turno dele (até
  6 s) logo antes da resposta.

## Render com inserções (Gate C), corte 6363–6475 s

Draft 1080×1920 com 5 inserções (4 reações e 1 plano aberto): 29,5 s no total, dos
quais 19,7 s de encode. Vídeo de 83,27 s e áudio de 83,26 s. A folha de contato
confirma a troca de imagem:

- reação enquadrada no rosto do apresentador (foco por rosto na janela da própria cena);
- plano aberto do estúdio inteiro, sem zoom;
- volta ao plano a dois.

O teste com FFmpeg real compara o áudio com e sem inserções e ele sai igual.

Risco: em 2 das 4 reações o apresentador aparece de boca aberta (riso ou "uhum"). Os
rótulos de voz são por frase, não por palavra, então a revisão por sugestão continua
obrigatória até medir a aprovação (meta: ≥ 80 %, em `scene_reviews` do episódio).

## Papel sugerido com 2 rostos

Em `wk7PY31TQBc` a câmera principal é um plano a dois (Dayon + convidado), confirmada
como "subject", com voz D entre 32 % e 53 %. A regra antiga sugeria "entrevistadores" ou
"plano aberto" para esses grupos. A regra nova:

- ≥ 30 % → subject;
- ≤ 12 % → entrevistadores;
- entre os dois → sem sugestão, a pessoa decide.

Aplicada a esse episódio, ela acerta 5 dos 6 grupos de 2 rostos e deixa o 6º (13,6 %)
em aberto. O papel é recalculado na leitura, sem refazer mapas.

## Studio (Chrome real via Flatpak, API e worker sobre a cópia, porta 8799)

Verificado em 390 px e 1920 px: sem rolagem horizontal e sem erro no console.

- Etapas do episódio: num episódio sem mapa de câmeras, o passo 4 oferece "Mapear
  câmeras". No episódio pronto, a seção fica recolhida em uma linha.
- Aba Cenas: 5 sugestões no corte de 6363 s. "Aplicar triangulação" aceitou as 5 e a
  prévia foi refeita. Aos 0:12 a imagem é a reação, a legenda continua e a trilha
  "Cenas" mostra os blocos aceitos.
- Corrigido na checagem: o botão "Aceita" ficava ilegível (estilo de pressionado sobre
  o primário). `GET /episodes/{id}/cameras` sem mapa respondia 404, o que sujava o
  console; agora responde 200 com campos nulos.
