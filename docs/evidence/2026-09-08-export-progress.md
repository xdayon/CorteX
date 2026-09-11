# Exportação após relato de espera longa

O job persistido `1bba26802d7845b89fac9f45fb894295` tinha 36,88% no
servidor, posição 2076 s, após cerca de 25 minutos. O Studio exibia o percentual
do render (ainda 0), ignorando o progresso recebido da preparação visual.
Na inspeção os servidores já estavam encerrados; o job foi cancelado pelo
JobStore antes de reiniciar o app, evitando retomar trabalho abandonado.

Correções: mostrar percentual da etapa visual, informar amostras/total e tornar
quadro inteiro desfocado o padrão sem análise de rostos. Automático permanece
uma escolha explícita e explica que sua primeira análise abrange todo o episódio.
Não é uma otimização concluída do reenquadramento automático.

Benchmark exploratório YuNet, cinco repetições no retrato real da fixture:
threads padrão 0,223 s/frame; duas threads 0,176 s/frame. Insuficiente para
justificar mudança de runtime. Nenhum modelo ou dependência foi alterado.
Resultados locais: `data/qa/auto-framing-20260908/thread-benchmark.json`.

Verificação frontend: 23 testes passaram; build e diff-check passaram.
Dois testes novos garantem exportação padrão sem jobs visuais e exibição de
37% recebido do servidor enquanto a preparação ainda está pendente.

Limitações: análise automática ainda integral; exportação ainda depende da aba
para agendar o lote. O render usa trim em uma entrada aberta desde o início do
arquivo: cortes tardios também têm custo de decodificação anterior ao trecho.
Otimizar seek exige verificar timestamps, reações e sincronização, não apenas
inserir um parâmetro FFmpeg sem teste.

Backend: 348 passed, 3 skipped (Remotion/browser opt-in). Render real do corte
1620,78–1669,90 s do episódio do usuário: 49,12 s, NVENC, quadro inteiro,
sem legendas neste smoke, 144,99 s totais com testes concorrentes,
`publish_ready=true`. Não extrapolar esse tempo para render com karaoke.
Manifesto: `render-4e4959c53e7da83e.json`; medição em
`data/qa/auto-framing-20260908/fast-export.json`. API reiniciada e health ok.
