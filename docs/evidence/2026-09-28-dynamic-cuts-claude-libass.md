# 2026-09-28 — corte seco, IA pela assinatura Claude e legendas libass

## Diagnóstico (dados reais do host)
- 107 de 112 planos de edição eram um trecho contínuo sem cortes: VAD vetava toda
  pausa e uma fronteira insegura derrubava o plano inteiro para o clipe cheio.
- Render de corte de 81s: Remotion 4min25s, FFmpeg+NVENC 29s (~90% em legenda).
- Sugestão Codex: 25 cortes com notas 31–34/35, janelas sobrepostas.
- Top 6 Reels @dayon.news: 92–139s, 2,7–3,0 palavras/s, quase nenhuma pausa >0,35s.

## Mudanças
- Edição (`edit/`): perfil `dynamic` padrão (pausa ≥0,25s, respiro ~0,13s), VAD só
  diagnóstico, corte inseguro descartado individualmente, remoção de muletas ≥0,8 de
  confiança, ranges semânticos unidos, render com corte seco e declick de 8 ms
  (áudio == vídeo).
- IA (`suggest/`): `ClaudeCodeCliProvider` (`claude -p`, Sonnet, esforço baixo, sem
  `--bare`, env sem `ANTHROPIC_API_KEY`); seleção v2 enxuta com janela ajustada aos
  segmentos; passada "enxugar" por frases só em cortes exportados.
- Legenda (`render/ass_overlay.py`): libass no mesmo encode, fontes em `assets/fonts/`.

## Resultados neste sandbox
- Suíte: 509 passaram, 7 puladas, 27 falhas iguais à linha de base (ambiente: ffmpeg
  em `/usr/bin`, CUDA). Nenhuma falha nova.
- Planner em 3 trechos reais: 13, 13 e 7 segmentos; pausa sozinha economiza 1–3%.
- Enxugar real (81s): ~4,7 mil tokens, 76% da fala mantida (muletas e recomeços).
- libass em clipe sintético de 60s: overlay 0,03s; pipeline total 17s (libx264).

## Não verificado aqui
- NVENC, `/usr/bin/ffmpeg` do host com libass, build web (Node quebrado no sandbox),
  seleção completa de episódio com Claude, render real ponta a ponta.
