# CorteX — contrato de entrega

Atualizado em 2026-11-10 (auditoria). Objetivo: o melhor editor pessoal de
cortes virais para @dayon.news — master único, processamento local, revisão humana
e publicação manual. Itens entregues antes de 14/out estão em
[history](history/ROADMAP-before-2026-09-10.md) e no git log.

## Fase 0 — base confiável (entregue em 14/out)

- [x] Provider Claude robusto: binário resolvido em ordem fixa, cache só com o que
  muda o resultado, Codex mantido como alternativa explícita.
- [x] Seleção v4 (hoje schema v5): transcrição no system prompt cacheável, 3 manchetes, destaques,
  muletas iniciais removidas, `piece` na EDL; medido em episódio real.
- [x] Refino v2 para todos os cortes exportados (inclusive compostos), com piso de
  duração; primeiro item do lote voltou a ser refinado (payload único).
- [x] Transcrição registra modo batched efetivo; hotwords (config + por episódio).
- [x] `update_and_verify.sh` seguro; instalador do serviço sem Codex obrigatório.
- [x] NVENC: pré-checagem explícita; cortes e aulas em h264_nvenc pelo FFmpeg do Jellyfin (evidência nvenc-jellyfin).

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
- [x] Modos congelados apagados (planos de câmera, reações emprestadas, identidade,
  `speaker_auto`/`speaker_face`/`face_static_crop`); dados antigos seguem legíveis.
- [x] Diarização substituída pelo reconhecimento da voz do protagonista (perfil de voz +
  embeddings por frase, ~3 min por episódio de 2h45, 97,6 % retido); pyannote só manual
  ([evidência](evidence/2026-10-16-voice-match.md)).
- [x] Teaser de abertura opcional (`transitions.teaser`, IA escolhe 1,2–4,5 s dentro do
  corte; entrada separada no render; validado em vídeo real com flash na virada).
- [ ] Capa JPG separada opcional (frame escolhido + manchete); nunca no 1º frame.

## Fase 3 — aprendizado e acesso

- [x] ~~Registro de publicação e métricas + "Aprendizado do perfil"~~: removido em 10/nov
  (0 registros desde 14/out). "Postado" virou estado manual do corte (botão, lote e atalho P).
- [ ] Imagens ilustrativas opcionais: IA sugere momentos e termos; imagem enviada
  pelo usuário ou de acervo livre (ex.: Wikimedia), com licença registrada.
- [x] Acesso remoto por Cloudflare Tunnel + Access no código: JWT validado na origem,
  `scripts/setup_remote_access.py`, serviço `cortex-tunnel`, `cloudflared` instalado.
- [x] Mensagem clara na UI para upload acima de 100 MB pelo túnel (antes de enviar).
- [ ] Ativar no painel (Access app → túnel → rota) e testar do iPhone.

## Fase 4 — Studio integrado (entregue em 16/out)

- [x] Cortes integrados por episódio: sugestões de todas as seleções + exports + publicação
  e estado manual (aprovado, descartado com motivo, salvo); quadro global "Cortes".
- [x] Studio novo: design system, rotas reais, episódios com status, tarefas em segundo
  plano, player instantâneo dos trechos na fonte, ações em lote e atalhos.
- [x] Editor de estúdio: linha do tempo (trechos, junções, legenda, manchete), ajustes por
  corte (`clip_settings`), fila de prévias em segundo plano, seleções de várias rodadas.
- [x] Correção da deriva A/V em cortes com muitas junções
  ([evidência](evidence/2026-10-16-av-sync-drift.md)); drafts mais rápidos
  ([evidência](evidence/2026-10-16-draft-speed.md)).
- [x] Perfil de voz cadastrado (20/out) e botão no Studio para refazê-lo (diálogo de voz).
- [ ] Salvar no Drive automaticamente (rclone com remoto do Google Drive) — opcional.
- [ ] QA mais barato nos drafts (~4 s por prévia) e prévia progressiva enquanto renderiza.

## Fase 5 — triangulação ([plano](TRIANGULATION.md))

- [x] A: mapa de câmeras por episódio com papéis confirmados por uma pessoa.
- [x] B: sugestões de reação e plano aberto por corte, tocáveis na fonte, com aprovação
  medida ([evidência](evidence/2026-10-23-scene-suggestions.md)).
- [x] C: inserções que trocam só a imagem, com prévia igual ao export (pixel + áudio).
- [x] D: abertura com o fim da pergunta (determinística, por corte).
- [x] E: "Aplicar triangulação" no editor (hoje ligado por padrão, ver abaixo).
- [x] Studio: etapas do episódio e aba Cenas no editor (essencial × opcional).
- [x] Triangulação automática por padrão (pedido do Dayon em 01/nov): todo corte sem
  escolha própria ganha plano aberto + entrevistador; mapa de câmeras roda sozinho após a
  seleção; papéis pela voz até alguém confirmar; imagem emprestada conferida por quadros
  ([evidência](evidence/2026-11-01-auto-triangulation.md)).
- [ ] Dayon revisa cortes automáticos de 2–3 episódios (meta: ≥ 80 % das cenas mantidas,
  `GET /episodes/{id}/scenes` → `review`).
- [ ] Seleção da IA preferir cortes com pergunta (prompt avaliado em worktree antes do merge).

## Estúdio IA — fase 2 (aulas e promos)

Fluxo e regras em [LESSONS_HANDOFF](LESSONS_HANDOFF.md) e [STUDIO_AI](STUDIO_AI.md); itens em ordem:

- [ ] 1. **Alinhamento com dica do roteiro.** `lessons/align.py:transcribe_words` chama `FasterWhisperEngine` sem
   `initial_prompt`, e o erro chega a 0,28 s (o piloto, com dica, errou menos). Adicione `initial_prompt: str | None = None`
   em `transcribe/engine.py`, opcional e sem mudar quem já chama (o pipeline de episódios usa o mesmo motor).
   Aceite: `align i-quarter-turn` em `timings_aligned.json` com max |Δ| ≤ 0,15 s contra `timings.json`.
- [ ] 2. **Divisão de falas longas sem GPU.** Um beat pode ser quebrado em sub-beats em fronteira de frase, e os tempos
   são recalculados a partir do `alignment.json` salvo (`align.timings_from_words`), sem retranscrever. Esse é o
   mecanismo que o plano usa para respeitar o limite de 10 s do Flow.
- [ ] 3. **Importar o pacote do Gabriel (`lessons/package.py`, CLI `import <slug> <pacote.md>`).** É um parser
   determinístico do formato de `docs/chatgpt-gabriel/3-formatos-de-entrega.md` e não usa IA.
   - Cabeçalhos `## Sxx · TIPO · beats … · pedir D s · xN · vN`, blocos `text` (prompt) e `yaml` (brief).
   - A seção "Divisões" vira sub-beats (item 2).
   - Valida: cobertura e ordem dos beats; a duração pedida contra `timeline.flow_request`; o número do prompt
     igual ao da tomada; o fechamento "No text…"; o modelo Manim conhecido. Reporta cada erro com o id da tomada.
   - Escreve um `lesson.json` proposto, com os briefs guardados para o Manim.
   - O teste usa o exemplo do arquivo 3 como fixture.
   - Isso substitui o antigo `plan.py`: o plano com LLM próprio fica só para as aulas do usuário, mais tarde.
- [ ] 4. **Tomada `avatar` (HeyGen).** `kind: "avatar"` + `AvatarRef` na spec.
   - Exporta o trecho da narração mestre de cada tomada (com folga do dissolve) para upload no HeyGen.
   - Ingest pelo número no título; montagem sem retime (o lábio quebra), com o áudio do clipe mudo.
   - Painel Manim opcional (`layout: lado`).
   - Bloqueado até o usuário mandar o print do painel do HeyGen (upload de áudio, fundo, resolução).
- [ ] 5. **Biblioteca de componentes Manim.** Extrair das cenas do piloto (`data/lessons/i-quarter-turn/scenes/`)
   componentes que recebem parâmetros (o brief YAML) e usam o `brand.py`. Os ids já estão no contrato do Gabriel
   (`2-guia-visual.md`):

   | id | Vem de |
   |---|---|
   | `rotating_plane` | s03 |
   | `matrix_product` | s04 |
   | `glass_equations` (overlay) | s05, s14 |
   | `reciprocal_lines` | s06 |
   | `function_graph` (overlay) | s07 |
   | `complex_map` | s08 |
   | `angle_ladder` | s10 |
   | `log_scale` | s13 |
   | `check_counter` (overlay) | s15 |
   | `end_card` | s18 |

   Aceite: recriar s03, s04 e s15 só por spec, com a mesma duração e comparação visual lado a lado com os renders
   do piloto. Cena nova só quando nenhum componente serve, e ela vira componente.
- [ ] 6. **Provider Gemini: baixa prioridade.** O plano criativo agora é feito no ChatGPT do Gabriel. Só vale se o usuário
   quiser fazer as próprias aulas sem gastar tokens do Claude. `GeminiCliProvider` ficaria no
   `build_suggestion_provider`, sem API e sem fallback, e o CLI ainda não está instalado.
- [ ] 7. **Fase 3, depois:** a aba "Aulas" na UI, com upload, prompts com botão de copiar, arrastar clipes, folha de QC,
   prévia e export. Ela segue as regras de prévia e verificação visual do AGENTS.md.

## Regras de aceite

Feature concluída = artefato real persistido + verificação automatizada focada.
Mudança visual = frames reais do FFmpeg inspecionados e Studio checado em 390 px e
desktop. Não afirmar ganho de desempenho sem medida persistida em `docs/evidence/`.

## Fora de escopo

Câmeras ISO ausentes do master, multicam sintético, clonagem de voz, editor genérico
de dezenas de tracks, auto-publicação, cloud GPU paga, APIs editoriais pagas.
