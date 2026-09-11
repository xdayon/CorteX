# Revisão por corte e análise visual limitada

## Entrega

- Editor de palavras por corte, carregado ao abrir o painel. Mantém timestamps,
  áudio e transcrição original. Corrige nomes/texto; campo vazio oculta palavra.
  Alterações por índice + texto original são validadas; versão incompatível falha
  explicitamente. Correções ficam no draft local e no settings persistido do
  render/job; mudam MP4/karaoke/SRT e a chave de cache.
- Prévia real usa a mesma cadeia EDL/câmeras/Remotion/NVENC, apenas para o corte
  em revisão. Solicita ~10 s, ajustáveis pelo fechamento seguro da fala. O vídeo
  de prévia fica separado das exportações finais e some ao mudar os ajustes.
- UI pede scene_index somente da união dos cortes selecionados com margem de
  3 s. Serviço normaliza/mescla intervalos, faz seek de cenas e conserva tempos
  absolutos. source_ranges persistido e incluído no hash; face sampling respeita
  essas cenas; speaker chunks intersectam os intervalos; qualidade segue cenas.
  Não reduz a frequência do detector nem altera os modelos. Reações reaproveitadas
  ficam limitadas ao conjunto de trechos analisados. Mudança de escopo invalida
  correções manuais de câmera antigas, com mensagem pedindo revisão.

## Evidência real no host

Projeto `00aa6fc0a8d64ac1a98ec835cd380b21`, análise somente
1617,88–1633,88 s, 16 s no meio do episódio. 19 amostras de rostos, todas dentro
do intervalo. Cadeia visual completa: 106,70 s na primeira execução, 2,07 s na
repetição com cache. O segundo render reutilizou o mesmo artifact.

Render `fbbd783daf47401fb4d95bb58ef23a6d`, arquivo
`render-b677d6469ba0e73e.mp4`, 11,41 s após ajuste dos limites da fala,
1080×1920, NVENC, publish_ready=true. Settings persistiram correção de caixa
`da` → `Da`, word_index4473. Parte ambígua preserva quadro inteiro; câmera
fechada usa rosto. Captura de vídeo conferida visualmente.

Arquivos locais ignorados: `data/qa/clip-review-20260909/first-run.json`,
`result.json`, `render.jpg`, `studio.png`. Chrome real: editor com 295 palavras
no corte selecionado, botão de prévia presente, sem overflow horizontal.

## Verificação e limites

353 testes backend passaram com Remotion E2E habilitado; 28 frontend. Teste novo
faz API→job→FFmpeg→scene artifact em dois intervalos separados, verifica tempos,
cache e amostragem. E2E de render verifica correção no SRT, settings persistidos,
transcrição original intacta e cache do job. Builds Web/Remotion e Ruff passaram.

Primeira análise ainda tem custo CPU. Prévia em resolução final também não é
instantânea; não confundir redução do escopo com render interativo. Um experimento
isolado de input seek no render com copyts mudou o áudio decodificado na fixture:
não foi integrado. O render ainda pode decodificar mídia anterior ao corte.
Benchmark dos dois modelos com duas threads mostrou ganho modesto; sem alteração
de runtime. Fila durável e retomada de lote após fechar a aba continuam pendentes.
