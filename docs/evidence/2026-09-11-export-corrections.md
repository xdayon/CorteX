# Validação: legenda, limite de duração e vozes

Revisão no host em 2026-09-11, a partir dos prints e do lote de 25 cortes do
projeto `7bd131035ca54c61a6841e7cdb3f5f12`, run `30be7c0969664586a4f884375cbe5c37`.

## Causas e alterações

- O zoom ampliava a janela inteira do foreground; a prévia não o simulava.
  Agora recorta/amplia o conteúdo dentro da mesma janela. A posição vertical do
  vídeo fica estável e a prévia oferece simulação explícita do trecho com zoom.
- O bloco de legenda crescia para cima ao quebrar mais linhas. `CaptionLayer` é
  compartilhada, mede glifos e pagina em até duas linhas, preservando palavras e
  tempos. `position_y` ancora uma área fixa de duas linhas; fontes carregam antes
  da medição. Palavras individuais muito longas reduzem a fonte daquele bloco.
- A barra após análise visual lia o progresso de render (zero). A conclusão
  visual agora mantém 100%, check e mensagem de que é possível continuar.
- O máximo estava só no pedido editorial. A seleção agora rejeita envelopes e
  estimativas acima do teto. A API/worker/planner carregam o limite até a EDL,
  encerrando fora de palavras/VAD protegidos. Se não existe final seguro, falha
  antes de renderizar. Sugestões antigas são limitadas na montagem e a UI avisa
  quando o final foi antecipado. A prévia solicita ~10s, permitindo fechar a fala,
  sempre sujeita ao teto configurado do episódio.
- Cache de composição versionado; mudanças no código de overlays também mudam
  seu hash. MP4s antigos não foram alterados; precisam ser renderizados novamente.

## Evidências reais

- Prévia pelo Studio/API/worker: artifact `dc12351767fb444f902b4f6b531fcd22`,
  `data/projects/7bd131035ca54c61a6841e7cdb3f5f12/renders/render-87ca2fb06cc4abe1.mp4`.
  11,53s, 1080×1920, NVENC, zoom 1,15, vídeo y=0,35, legenda Montserrat900/56px/
  6 palavras/y=0,61 e headline acentuada. QA passou sem issues. Frame aos 2s
  inspecionado: duas linhas abaixo do vídeo. Prévia ao vivo: topo da legenda
  9,22px abaixo da janela do vídeo no tamanho exibido.
- Chromium automatizado: 18 amostras entre 1080×1920 e escala 0,25; texto longo
  paginado, palavra ativa preservada, máximo duas linhas e topo fixo.
- FFmpeg automatizado com padrão de cores prova que zoom remove a faixa lateral
  sem mover bordas do vídeo nem mudar relógios ou áudio decodificado.
- Dois planos reais antes acima do teto: 175,69s → 95,83s
  (`adf0115c087e4a7586aa39339b7f99aa`) e 130,49s → 118,9719s
  (`44a8f4d8c04e43ee9a4dda386e9e86ac`). São novas EDLs persistidas; os dois
  cortes completos não foram renderizados novamente. Fechamento editorial exige
  revisão humana, especialmente no primeiro, que precisou terminar bem antes.

## Identificação de voz

O usuário configurou HF_TOKEN no `.env` ignorado. Acesso ao modelo confirmado,
serviço reiniciado e readiness da API pronta. O teste revelou dois bugs reais
no adapter: `Pipeline.__call__` de pyannote.audio 4.0.6 é um gerador, e seus hooks
usam inteiros NumPy que JSON não serializa diretamente. O runner usa o batch
explícito de um arquivo e converte contadores para int; testes cobrem ambos.

Amostra do episódio entre 560–650s: JSON persistido em
`.cache/review-2026-09-11-fixes/voices.json`, 6 turnos/2 vozes, CPU, 84,54s de
inferência e carregamento com pesos já baixados. Isto valida execução, não
acurácia nem reconhecimento de Dayon. Não foi feita diarização integral do
podcast nem atribuída uma voz ao usuário automaticamente. Na biblioteca, separar
as vozes do episódio, ouvir amostras e confirmar a própria voz continua necessário.

Prontidão e instruções de habilitação estão na UI; amostras aparecem ao terminar
o job. Nova seleção usa a referência confirmada por episódio. Atribuição editável
por palavra e vínculo voz↔rosto permanecem no P0 do roadmap.

## Tempo do lote anterior

Lote: 4h09m46s entre criação do run e último render. Análise visual ~72m13s:
qualidade 37m22s/5.063 amostras, faces 26m01s/2.945 amostras; visão cobria 21
intervalos escolhidos/218 cenas, não o episódio inteiro. Os 25 renders somaram
155m04s para 40m39s de mídia. Criação dos jobs até persistência dos overlays:
129m08s (~83%); inclui preparo/fila, não isola apenas Remotion. Depois dos
overlays: ~25m56s incluindo FFmpeg e QA. Não há benchmark comparável de speedup.

Quadro inteiro com blur dispensa análise visual. Voz confirmada melhora seleção,
mas não identifica sozinha quem a transmissão mostra. Próxima otimização deve
medir composição tipográfica e decodificação visual compartilhada, preservando
paridade de prévia/export e expondo engines; nenhuma troca de renderizador foi feita.

## Verificação

- `CORTEX_RUN_REMOTION_E2E=1 .venv/bin/pytest -q`: 482 passaram.
- Frontend: 58 testes passaram. Builds web/Remotion, Ruff e `git diff --check` passaram.
- Gitleaks: commits desde origin/main e patch preparado, sem credenciais reais.
- Evidências locais adicionais em `.cache/review-2026-09-11-fixes/` (ignoradas).
