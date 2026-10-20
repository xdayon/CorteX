# Mapa de câmeras por keyframes — sondagem 2026-10-19

Episódio `EgHjfefLt0w` ("ALIENÍGENAS…", 8613 s, AV1 2560×1440, master do YouTube).
O YouTube põe um keyframe a cada 5 s e um em cada troca de câmera (15,97 s, 16,37 s,
17,1 s…). Decodificar só keyframes (`-skip_frame nokey`) dá um mapa das câmeras do
episódio inteiro.

## Método
- 1779 keyframes em 160×90; aparência = miniatura 32×18 em cinza normalizada +
  histograma de cor 4×4×4; agrupamento por líder (0,75) refinado com 10 iterações de
  k-médias; tempo no ar = até o próximo keyframe.
- YuNet em 5 quadros por grupo. Cruzamento com os 40 trechos dos 25 cortes sugeridos.
- Tempo total: 62 s (2 min 51 s de CPU), com `nice`.

## Resultado
11 grupos, separados corretamente em três papéis (folha de contato conferida à mão):

| Papel | Grupos | No episódio | Nos cortes (26,7 min) |
| --- | --- | --- | --- |
| Entrevistadores (Wayne + Dan, 2 rostos) | C1, C2, C7, C9 | 46,5 %, 210 planos, mediana 5–10 s | 69 s (4 %) |
| Dayon (2 enquadramentos da mesma câmera) | C3–C6 | 39 % | 1460 s (91 %) |
| Plano aberto (3–5 rostos) | C8, C10, C11 | 6,5 % | 5 s |

O agrupamento separa demais (o mesmo papel aparece em vários grupos), o que se resolve
com um papel por grupo confirmado por uma pessoa.

## Conclusões
- Há imagem dos entrevistadores de sobra. Os cortes ficam 91 % no Dayon porque a seleção
  pega as falas dele, o diretor mostra quem fala, e o prompt manda pular o entrevistador
  ("pular a interrupção do entrevistador", "pergunta só quando indispensável").
- No plano aberto, o foco por rosto com zoom 1,45 escolhe o maior rosto e corta os
  entrevistadores para fora.
- Para saber se os entrevistadores estão calados é preciso a voz. Perfil de voz
  cadastrado em 2026-10-20 (`wk7PY31TQBc`, balanced accuracy 0,986).

## Implementação (Gate A, backend) no mesmo episódio — 2026-10-20
Cópia CoW dos dados, `analyze/camera_map.py`, perfil de voz cadastrado:
- Voice match do episódio inteiro na 1a vez: ~3 min (CPU, em cache depois). Mapa: 99 s
  (27,6 s de keyframes, 71 s de rostos), 1779 keyframes, 519 planos, 49 grupos.
- Os 11 grupos com ≥ 1 % do tempo no ar saíram com o papel sugerido certo: voz D
  cobre 84–90 % dos grupos do Dayon e 0–13 % dos entrevistadores; plano aberto = 3 rostos.
  Os 38 grupos raros (vinhetas, transições, < 1 %) ficam "ignorar".
- Erros corrigidos no caminho: fundir grupos raros no vizinho misturou vinhetas no grupo
  dos entrevistadores; YuNet a 640 px com altura mínima de 8 % não via os rostos do
  plano aberto (4–9 % da altura) e somar rostos de amostras distantes contava a mesma
  pessoa duas vezes. Agora: grupos não se fundem, rostos a 1280 px com altura ≥ 3,5 %,
  mediana por quadro.
