# Experiência do Studio — 2026-09-11

## Entrega

A biblioteca é uma área separada. A jornada tem três etapas: Novo episódio,
Escolher cortes e Exportar. O nome do protagonista orienta o pedido editorial;
a interface esclarece que isso não identifica uma voz. A direção editorial tem
exemplo de instrução, e o prompt solicita PT-BR com acentos e grafia do nome.
Textos antigos continuam editáveis; não há corretor ortográfico universal.

A exportação organiza enquadramento, corte em revisão, headline, legenda e
opções de reação antes dos botões finais. A prévia permanece ao lado dos ajustes
no desktop; em telas estreitas aparece acima deles. Estilo vale para o lote;
headline e correções pertencem ao corte selecionado. A prévia ao vivo é uma
simulação sobre um frame, com texto de teste identificado. A prévia curta produz
um vídeo real com o áudio, a composição e os textos efetivos.

`HeadlineLayer.tsx` é compartilhado por Studio e Remotion. O espaço entre linhas
acomoda o fundo de cada linha e os glifos com acentos/descendentes. As fontes
locais têm endpoints explícitos para os pesos 400, 800 e 900. O usuário pode
alterar texto, fonte, escala, duração e entrada da headline.

`framing.position_y` varia de 0 a 1, default 0.5. Desloca somente o primeiro plano
na composição com Gaussian blur; o fundo permanece no lugar. Outros modos
registram a posição efetiva centralizada. A configuração participa do cache.

## Catálogo real

Os oito IDs fornecidos foram registrados e tiveram título, canal e miniatura
consultados via oEmbed público do YouTube, sem baixar os oito vídeos. URLs watch,
live e youtu.be convergem para a mesma identidade; parâmetros de compartilhamento
não criam episódios adicionais.

| ID | Canal retornado |
|---|---|
| zMb1o5QRTqw | Raul Ferreira Netto Sem Limites |
| 17EmKWBZX0k | Raul Ferreira Netto Sem Limites |
| wk7PY31TQBc | Raul Ferreira Netto Sem Limites |
| y7pj7E-blLo | Filhos do Todo |
| EgHjfefLt0w | Canal FritaMente |
| 0feL2iE8Ubw | Prosa Inversa |
| NJFabHAIepM | Portal do Agora (por Ana Paula Soares) |
| Rn_7McUFPF8 | Na Fogueira com Elas |

O catálogo padrão tem oito entradas. Oito entradas legadas foram arquivadas;
“Mostrar arquivados” permite restaurá-las. Fontes, seleções e MP4s antigos foram
preservados. Não é uma limpeza de espaço em disco nem uma associação presumida
entre snippets locais e o master do YouTube. Backups e respostas da importação
ficam em `.cache/library-*-2026-09-11.json` no host, fora do Git.

A listagem não consulta metadata na rede. Refresh é uma operação explícita;
o Studio solicita a primeira consulta durante o cadastro. Downloads continuam
locais. Sem jobs ativos, a biblioteca deixa de consultar a API a cada cinco
segundos; foco/visibilidade retomam a consulta. Respostas obsoletas são ignoradas.

## Enquadramento e processamento

Automático v1.2.0 preserva plano aberto, dupla e intenção de contexto, incluindo
múltiplos rostos visíveis. Closes existentes continuam acompanhados; overrides
manuais têm precedência. O zoom é uma ampliação estática alternada em cortes de
pausa; não é animação contínua e não amplia planos de contexto automaticamente.

O índice real inspecionado tinha 98 frames entre 2164 e 2260s, 207 observações e
um único layout: 8 grupos single_layout, 72 ambiguous, zero confirmed. Isso não
significa 80 pessoas nem ausência de rostos. O contrato exige continuidade entre
ângulos; não reduzimos thresholds nem inventamos identidade. A UI explica o motivo
para bloquear reutilização de reações. A identificação voz/rosto continua separada.

Detalhes de exportação mostram estado, tarefa, processo local, última atualização
e até 40 eventos. Remotion emite frames renderizados/codificados enquanto trabalha;
FFmpeg emite tempo de mídia codificado. Os subprocessos mantêm cancelamento,
timeout e IO limitado. O percentual do lote é uma estimativa por etapas, não uma
previsão de tempo. QA continua necessário depois de codificar.

## Verificação

- Backend completo com Remotion/browser habilitados: **463 passed**, 132.03s.
- Interface: **48 passed**, incluindo navegação, draft, Unicode, posição vertical,
  payload de render, mensagens de tarefas, metadata, arquivo e polling.
- Builds Web e Remotion, Ruff e `git diff --check`: passaram.
- Regressão FFmpeg de posição: pixels confirmam movimento do vídeo e fundo estável,
  manifests diferentes e reutilização do cache com os mesmos ajustes.
- Regressão de automático: dois lados da mesa preservados, closes mantidos e áudio
  inalterado. Teste de identidade de um ângulo persiste zero confirmações falsas.
- Regressão Chromium de headline: texto Unicode intacto e caixas de linhas
  separadas, com captura de imagem. Testes reais de progresso observam atualizações
  antes do término e arquivos de mídia persistidos.
- Browser local em 1440×1100 e 390×844: sem overflow horizontal, navegação e prévia
  lateral inspecionadas. Capturas/métricas em `.cache/review-2026-09-11/`.

## Prévia real pela interface

A ação “Gerar prévia curta” foi executada no navegador contra o serviço local,
com headline “Religiões se preparam para o disclosure” e posição do vídeo 25%.
O artefato `88369647133540bfb53304496e503228` contém MP4 de 10.0s, SRT e manifest;
encoder efetivo `h264_nvenc`, headline Unicode e `framing.position_y=0.25`.
QA passou sem issues/warnings: áudio/vídeo presentes, loudness -14.1 LUFS,
zero frames pretos/congelamentos e sincronismo A/V sem delta. A resposta de mídia
suporta Range (206). O navegador observou 46 atualizações de atividade, incluindo
frames do Remotion e segundos codificados pelo FFmpeg.

O arquivo local é
`data/projects/00aa6fc0a8d64ac1a98ec835cd380b21/renders/render-a1e940cc59efca5d.mp4`.
Não foi gerado um lote de 15 cortes durante o teste: apenas a prévia selecionada.
O catálogo verificado no browser tinha oito cards e oito capas carregadas.

## Limites que permanecem

O navegador ainda agenda o próximo item do lote; mantenha a tela aberta para
encaminhar todos os cortes. Jobs já enviados continuam no worker. Revisão e lote
persistidos no servidor são o próximo P0. Não foi feito novo benchmark de episódio
inteiro, diarização de três vozes ou retenção editorial. Não houve deploy Cloudflare.
