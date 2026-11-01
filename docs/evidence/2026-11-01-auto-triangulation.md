# Triangulação automática — 2026-11-01

Pedido do Dayon: cortes com as falas dele, o cenário (câmera central) e pelo menos uma
cena do entrevistador, sem precisar aplicar à mão. Branch `feat/triangulation-auto`,
dados reais numa cópia CoW de `data/`.

## Mapas de câmeras novos e papéis pela voz

O serviço mapeou 7 episódios sem mapa (fila normal, ~1–2 min de quadros + reconhecimento
de voz). Papéis sugeridos conferidos pelas miniaturas:

- `zMb1o5QRTqw` (Sem Limites #285):
  - closes do Dayon (voz D 0,94–0,95) → subject ✓;
  - closes do Raul (D 0,03–0,34) → entrevistadores ✓;
  - um close do Raul com D 0,44 fica sem papel (ignorado);
  - o plano a dois na mesa (Dayon + Raul, D 0,08) era sugerido como entrevistadores.
    Mostrado como reação, o recorte por rosto podia cair no Dayon. Agora vira plano aberto.
- `y7pj7E-blLo` (Filhos do Todo): 9 de 9 grupos certos. Dayon com D 0,91–0,98,
  apresentador com 0,03. É um podcast só de closes, sem câmera aberta.
- `wk7PY31TQBc`: papéis já confirmados pelo Dayon; nada muda.
- `Rn_7McUFPF8` (Na Fogueira com Elas): tudo certo. Closes do Dayon (D 0,87–0,97),
  closes da apresentadora como entrevistadora (0,05–0,10), mesa a dois como base (0,76).
- `0feL2iE8Ubw` (Prosa Inversa): closes do Dayon (D 0,82–1,0) e do apresentador
  (0–0,06) certos; planos a dois na mesa (0,22–0,45) ficam como base ou sem papel.
- `EgHjfefLt0w` (FritaMente): Dayon certo (D 0,83–0,90). Os planos com os dois
  apresentadores (2 rostos, D ≤ 0,10) e o estúdio com 3 rostos viram plano aberto. Sem
  close individual de apresentador, a cena do entrevistador vem desses planos inteiros.
- `aKnMQWta8lo` e `kh76Q_cEUKY` (vídeos solo do Dayon News): todo grupo é subject, sem
  entrevistador (correto); só entra o plano aberto ao vivo.

## Composição v2 (61 planos de edição guardados, sem decodificar)

| Episódio | Cortes | Com entrevistador | Com plano aberto | Plano aberto |
| --- | --- | --- | --- | --- |
| wk7PY31TQBc (confirmado) | 34 | 30 | 29 | estúdio real (g10) |
| zMb1o5QRTqw (pela voz) | 18 | 18 | 18 | câmera no ar sem zoom |
| y7pj7E-blLo (pela voz) | 9 | 9 | 9 | câmera no ar sem zoom |

Os 4 cortes do wk7 sem cena têm outra voz em quase todo o trecho (107–176 s de "outros"
em cortes de 59–114 s; episódio com dois convidados). A regra não cobre a voz de outra
pessoa com imagem emprestada.

## Cortes sem keyframe (motivo do `footage_check`)

Renders com só o mapa mostraram, em 2 de 3 episódios, uma "reação" com o próprio Dayon:

- `y7pj7E` 2608,8–2615,8 s: o mapa diz "apresentador", mas o Dayon está no ar desde
  antes de 2611 s;
- `wk7` 8800–8807 s: o corte para o plano a dois em ~8801,2 s não tem keyframe.

O mapa só vê keyframes, e o master do YouTube nem sempre põe keyframe no corte.

Correção: 3–5 quadros de 160×90 da janela escolhida, cada um ligado ao grupo de
aparência mais próxima (miniaturas dos grupos, mesmas features do mapa). Todos precisam
cair num grupo com o papel certo. Nos casos acima, os quadros ruins caíram nos grupos do
Dayon e os bons no apresentador ou no estúdio (g10).

A janela agora começa no início do trecho utilizável, perto do keyframe do plano, para
decodificar pouco. O cache guarda os grupos (não o veredito), então confirmar papéis
depois não exige decodificar de novo.

## Render (draft 540×960), mesmos 3 cortes, com a verificação

Máquina com load ~28 durante a medida: reconhecimento de voz dos mapas + um render de
aulas de outra sessão.

| Corte | Inserções | Render | Vídeo / áudio |
| --- | --- | --- | --- |
| wk7 8398–8490 | 2 estúdio + 2 reações | 140 s | 68,467 / 68,460 s |
| zMb 5638–5695 | 1 ao vivo + 2 reações | 98 s | 46,467 / 46,455 s |
| y7pj 2635–2720 | 2 ao vivo + 2 reações | 130 s | 62,267 / 62,260 s |

As folhas de contato (quadro antes, no meio e depois de cada inserção) mostram as 11
inserções corretas:

- estúdio inteiro (mesa, convidados, apresentador, TV);
- apresentador ouvindo;
- close no ar sem zoom, mostrando o cenário.

Sugestões com a verificação: 8,5–19 s por corte na primeira vez (sob a carga acima) e
0,3–0,5 s com cache. Um quadro avulso custou 1–3 s de busca no master 1440p.

## Studio (Chrome real via Flatpak, API e worker de teste na porta 8799, cópia)

Editor do corte `y7pj7E` 2635–2720, aba Cenas, em 390 px e 1440 px: sem rolagem horizontal
e sem erro no console. A tela mostra:

- "Automáticas", com as 4 cenas aceitas;
- "Câmeras identificadas pela voz · Conferir câmeras";
- a trilha "Cenas" com 4 blocos.

A prévia pedida pela UI saiu do worker com
`triangulation = {mode: auto, status: ok, roles_source: suggested, inserts: 4}` e as 4
inserções aplicadas.

## Riscos conhecidos

- A primeira abertura da aba Cenas (e o primeiro draft) espera a verificação dos quadros.
- Plano aberto ao vivo de um close é um zoom-out do mesmo plano, não outra câmera. Só
  entra quando o episódio não tem imagem de estúdio utilizável.
- Reação ainda pode pegar um "uhum" ou um riso do apresentador (rótulos de voz por frase).
- Corte dominado pela voz de outra pessoa fica sem cena.
