# Editor de legenda e troca de episódio

Prévia ao vivo usa `apps/remotion/src/CaptionLayer.tsx`, compartilhado com o
render Remotion. Simula blocos e palavra ativa sobre um frame real do corte;
texto de teste não altera transcrição. Não simula enquadramento automático,
headline ou cortes temporais. Mantém coordenadas 1080 × 1920 e escala no
navegador, com fontes locais servidas por whitelist da API. Fontes da prévia
usam alias exclusivo para não alterar a tipografia da interface.

Controles: família (Montserrat/Lato/DejaVu Sans), peso normal/900, tamanho,
posição vertical, caixa alta, palavras por bloco, contorno, sombra, karaoke,
cores e animação. Padrão visual novo: 56 px. `font_weight` e `uppercase` chegam
pelo schema Python, payload validado e settings efetivos no manifesto. Defaults
compatíveis com exports antigos: 900 e caixa alta. Hash do app Remotion e settings
invalidam o overlay alterado.

Começar outro episódio remove somente o apontador de restauração automática,
guarda referência no histórico local (10 itens) e snapshot de seleção/settings/
exports. Retomar recupera workflow persistido e draft. Nenhum cache ou artifact
foi apagado. Histórico/draft são locais ao navegador, não sincronizados. Durante
processamento o reset fica desabilitado para preservar o agendamento do lote.

Validação real no host: Chrome 1440 px, preview 360 × 640, fonte carregada,
sem overflow horizontal. Captura em `data/qa/caption-editor-20260909/studio.png`.
Render real 4 s, NVENC, publish_ready=true, Lato 64, peso400, caixa normal,
posição .62. Manifesto: `render-9497435e44bd5775.json` no projeto
`d7066b6201f34e0292fd8bbc2ceaa4e3`; imagem conferida visualmente em
`data/qa/caption-editor-20260909/render.jpg`.

Verificação final: 351 testes backend passaram com Remotion opt-in; após ampliar
as asserções, teste de render persistido/cache passou novamente e 9 testes de
settings passaram. Frontend: 25 testes; builds Web/Remotion, Ruff e diff-check
passaram. Browser real também validado em 390 px sem overflow; reset removeu
apontador ativo, manteve histórico e expôs input de vídeo novo.
