# 2026-10-14 — editor de exportação, prévia fiel e foco por rosto

## Prévia = export (verificado)
- Método: API + worker de teste em outra porta sobre cópia do SQLite; Google Chrome
  155 (Flatpak, headless, perfil temporário) dirigido por CDP; captura do corpo do
  `overlay-preview` enviado pela página; export renderizado com as mesmas
  configurações; frames comparados em 0,5 s (manchete + legenda) e 6,5 s.
- Resultado: manchete, selo, fonte, tamanho, posição e palavra ativa idênticos.
- Atenção: o `chrome-headless-shell` (Chrome for Testing) e o Chromium do Playwright
  não decodificam H.264 (erro 3) apesar de `canPlayType` dizer "probably"; usar o
  Chrome do Flatpak para checagens com vídeo.

## Corrigido durante a verificação
- JASSUB caía numa sans genérica: `availableFonts` é buscado por "família + peso" e
  não achava "Montserrat Black"; as fontes do export agora vão em `fonts`.
- Vídeo parado não pintava a legenda (sem frame novo): repaint + seek no mesmo tempo.
- 390 px: grade do "Localizar e substituir" estourava para 445 px (`1fr` → `minmax(0,1fr)`);
  cabeçalho fixo cobria o topo da prévia (deixou de ser fixo em telas estreitas);
  números do menu e rank em 9 px subiram para 11 px.
- Checado em 390 px e 1440 px: sem rolagem horizontal, sem erros de console do app.

## Foco por rosto com rostos reais
- Corte de 69,7 s ("anticorpo"), cópia do banco, x264:
  - fundo desfocado, zoom 1,45, `focus_mode: faces`: 58 s no total, QA aprovado;
  - tela cheia, `focus_mode: faces`: 50 s no total, QA aprovado;
  - YuNet achou 1 rosto em todas as amostras; foco 0,49 (`all_faces`); o trecho tem
    uma câmera só (scdet sem cortes na janela de 98 s).
- Frames: rosto centralizado e maior no fundo desfocado; close vertical sem cortar
  a cabeça na tela cheia. Troca de câmera dentro de um segmento coberta por teste
  real de FFmpeg com detecção simulada.

## Pendente
- Episódio com plano aberto de duas pessoas para validar `primary_side`/`largest_face`
  com rostos reais.
- Acesso remoto: passos do usuário no painel (ver LOCAL_AND_CLOUDFLARE.md).
