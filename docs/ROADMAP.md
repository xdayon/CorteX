# CorteX — contrato de entrega

Atualizado em 2026-10-14 (auditoria completa). Objetivo: o melhor editor pessoal de
cortes virais para @dayon.news — master único, processamento local, revisão humana
e publicação manual. Itens entregues antes de 14/out estão em
[history](history/ROADMAP-before-2026-09-10.md) e no git log.

## Fase 0 — base confiável (entregue em 14/out)

- [x] Provider Claude robusto: binário resolvido em ordem fixa, cache só com o que
  muda o resultado, Codex mantido como alternativa explícita.
- [x] Seleção v4: transcrição no system prompt cacheável, 3 manchetes, destaques,
  muletas iniciais removidas, `piece` na EDL; medido em episódio real.
- [x] Refino v2 para todos os cortes exportados (inclusive compostos), com piso de
  duração; primeiro item do lote voltou a ser refinado (payload único).
- [x] Transcrição registra modo batched efetivo; hotwords (config + por episódio).
- [x] `update_and_verify.sh` seguro; instalador do serviço sem Codex obrigatório.
- [x] NVENC: pré-checagem explícita; host em libx264 (ver evidência host-nvenc).

## Fase 1 — edição viral e prévia fiel (entregue em 14/out)

- [x] Estilos de legenda clássico/palavra a palavra/pop/caixa e destaque por palavra.
- [x] Manchete persistente opcional; flash/zoom nas junções entre peças.
- [x] Fundo desfocado com zoom e foco horizontal; zoom alternado nos cortes (padrão).
- [x] Faixas seguras v2 (interface do Reels/TikTok) na QA e largura da legenda.
- [x] API de prévia: `.ass` idêntico ao do render, fontes, faixas, render draft.
- [x] Editor de exportação: prévia real (draft + JASSUB), abas Legenda/Estilo/
  Enquadramento/Manchete, revisão de legenda estilo CapCut, layout 390 px — prévia e
  export conferidos lado a lado no Chrome.
- [x] Remotion removido (app, renderer opcional, configs e testes).

Aceite: prévia e export lado a lado sem diferença visível de legenda/manchete;
corte real exportado pelo lote com refino, destaques e manchete editada.

## Fase 2 — enquadramento e gancho

- [x] Foco automático por cena (`framing.focus_mode: faces`, padrão no Studio com zoom
  1,45): rostos YuNet em poucas amostras por cena; todos cabem → centraliza; senão →
  lado do Dayon ou maior rosto; um recorte estático por cena, motivo no manifest.
  Validado com rostos reais ([evidência](evidence/2026-10-14-studio-editor-and-framing.md)).
- [ ] Depois de validado: apagar modos congelados (planos de câmera, reações
  emprestadas, identidade) e decidir sobre a diarização (hoje 6 falhas em 7).
- [ ] Teaser de abertura opcional (repete 2–4 s do payoff; nunca muda o sentido).
- [ ] Capa JPG separada opcional (frame escolhido + manchete); nunca no 1º frame.

## Fase 3 — aprendizado e acesso

- [ ] Registrar cortes publicados e métricas (retenção 3 s, compartilhamentos,
  salvamentos) e usar exemplos aprovados/reprovados no prompt de seleção.
- [ ] Imagens ilustrativas opcionais: IA sugere momentos e termos; imagem enviada
  pelo usuário ou de acervo livre (ex.: Wikimedia), com licença registrada.
- [x] Acesso remoto por Cloudflare Tunnel + Access no código: JWT validado na origem,
  `scripts/setup_remote_access.py`, serviço `cortex-tunnel`, `cloudflared` instalado.
- [x] Mensagem clara na UI para upload acima de 100 MB pelo túnel (antes de enviar).
- [ ] Ativar no painel (Access app → túnel → rota) e testar do iPhone.

## Regras de aceite

Feature concluída = artefato real persistido + verificação automatizada focada.
Mudança visual = frames reais do FFmpeg inspecionados e Studio checado em 390 px e
desktop. Não afirmar ganho de desempenho sem medida persistida em `docs/evidence/`.

## Fora de escopo

Câmeras ISO ausentes do master, multicam sintético, clonagem de voz, editor genérico
de dezenas de tracks, auto-publicação, cloud GPU paga, APIs editoriais pagas.
