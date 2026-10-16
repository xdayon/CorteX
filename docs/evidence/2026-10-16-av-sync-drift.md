# Deriva de sincronia em cortes com muitas junções — 2026-10-16

## Achado
- Revisão da velocidade do draft: drafts e exports com 10–17 segmentos saíam com o vídeo
  mais curto que o áudio. Nos manifests existentes, 43 de 163 renders finais estavam acima
  da tolerância de 0,04 s (`av_sync_out_of_tolerance`, até 0,135 s) e por isso não
  ficavam `publish_ready`.
- Causa: cada segmento era aparado sozinho (`trim` + `fps=30` sobre fonte 29,97 fps) e
  perdia uma fração de quadro, enquanto o áudio mantinha precisão de amostra. Vídeo e
  áudio são concatenados em cadeias separadas, então a perda acumulava junção a junção:
  no fim do corte a imagem ficava ~0,1 s à frente da voz.
- Reprodução sintética (16 segmentos, testsrc2 29,97 fps): 1599 quadros (53,300 s) para
  53,409 s de áudio.

## Correção
- `render/service.py::_filtergraph`: todo segmento (simples, com várias cenas de rosto ou
  teaser) recebe uma contagem exata de quadros na grade acumulada da linha do tempo
  (`tpad=stop_mode=clone` + `trim=end_frame=N`, N = round(fim acumulado × fps) − quadros
  já emitidos). Erro máximo em qualquer ponto: meio quadro.
- Chave de cache do render ganhou `timeline_version: 2`: renders antigos com deriva não
  são reaproveitados; reexportar gera a versão corrigida.

## Validação
- Teste `tests/test_render_av_sync.py`: grade acumulada (30/31/31 quadros) e render real com
  16 cortes — quadros = round(total × fps), |vídeo − áudio| < 1 quadro.
- Dado real (cópia do banco, plano de 16 segmentos do projeto 7b1f…): draft e export
  passaram de 0,132 s para 0,0013 s de diferença; o export agora sai `publish_ready`.
- Suíte completa: 568 testes passando.
