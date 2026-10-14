# Connex — Brief de construção (handoff para orquestração)

> Documento de handoff. Escrito por Claude (Opus 4.8) numa sessão anterior, para
> ser lido por um novo orquestrador (Fable) numa sessão nova. Contém o contexto
> completo, as decisões já tomadas com o usuário, e o plano de execução.
> **Leia inteiro antes de agir.** Data: 2026-07-10.

---

## 0. Quem é o usuário e como trabalhar

- Cria cortes verticais (Reels/Instagram) a partir de podcasts em que participa (PT-BR, nicho: espiritualidade, ETs, consciência, filosofia). Voz principal: "Dayon".
- Máquina local: Linux (Fedora Silverblue/ostree), **GPU NVIDIA GTX 1060**. Quer **tudo que puder ser acelerado rodando na GPU** (NVENC/NVDEC, CUDA), com compatibilidade total.
- Frequentemente trabalha **em movimento (ônibus, 5G instável)** — o sinal cai. Isso é requisito de arquitetura, não detalhe (ver §5, tolerância a offline).
- **Fluxo de trabalho preferido:** orquestrador/advisor. Delegar implementação bem-especificada pro lane `implementer` (Sonnet) ou `codex exec --full-auto`; usar `advisor` (Opus, read-only) pra decisões arquiteturais grandes; **sempre revisar o diff** antes de dar por pronto. Não relatar "pronto" sem verificar.
- Idioma: **responder sempre em PT-BR** (com acentuação correta).

## 1. O que é o Connex (visão)

Um **editor algorítmico de cortes virais de podcast**, 100% focado e centralizado, que o usuário controla de ponta a ponta. Substitui e **abandona a marca `podcli`**.

**Fluxo único, sem desvios:**

```
Arquivo local OU link do YouTube
        │
        ▼
1. Transcrição perfeita (faster-whisper GPU, PT-BR, word-level + VAD)
        │
        ▼
2. Roteiro de cortes virais (IA lê o transcript e monta o plano editorial)
        │
        ▼
3. Edição de alta qualidade (waveform+VAD, transições pro, hook falado + título visual)
        │
        ▼
4. Legendas animadas + render 100% GPU
        │
        ▼
Cortes .mp4 verticais prontos p/ postar no Instagram
```

Tudo com **branding Connex**, um repo, um ambiente, um config, um frontend.

## 2. Escopo — o que ENTRA e o que SAI

### Entra (foco exclusivo)
- Ingestão por **arquivo** e por **link do YouTube** (yt-dlp).
- Transcrição PT-BR de alta qualidade, word-level, com VAD (Silero).
- **Seleção de cortes por IA** com prompts excelentes (ver §6) — é um pilar central, o usuário reclama muito da escolha ruim dos trechos.
- **Motor de edição profissional** (ver §7): cortes conscientes de fala, transições reais (jump cut, punch-in, micro-dissolve, J/L-cut), hook falado, título visual (`hook_text`).
- Legendas animadas (Remotion) + render NVENC.
- Frontend web local agradável.
- Muitas configurações/otimizações expostas ao usuário.

### Sai (remover do que o podcli fazia — o usuário NÃO quer)
- ❌ **Ferramenta de thumbnail** — ele detesta o frame inicial que "pisca" a thumb no começo do vídeo. Remover completamente.
- ❌ **Content Studio** — não usa, não sabe o que é.
- ❌ **Fluxo/UI de "new episode"** do podcli — considera bugado e feio.
- ❌ **Knowledge base** — não usa.
- ❌ Diarização pesada / pyannote (a menos que vire necessária pra troca de falante; começar sem).
- ❌ Auto-update de app fechado, overlay via `apply.sh`, múltiplos runtimes.

## 3. Decisões já tomadas com o usuário (NÃO reabrir)

| Tema | Decisão |
|---|---|
| **Seleção IA** | Usar a **assinatura** do usuário via **Claude headless** (Claude Code em modo `-p`/Agent SDK, subscription-billed). **NÃO usar a API paga por token.** Caminho único e robusto + fallback heurístico local. Remover o shell-out pro Codex CLI. |
| **Estrutura** | **Repo novo `Connex`, do zero**, importando só os módulos bons já prontos (ver §4). Sem herdar o emaranhado do podcli. |
| **Legendas/Render** | **Manter Remotion** (legendas animadas palavra-a-palavra), otimizando a fase de CPU dele; encode 100% NVENC. |
| **Frontend** | **Web local**: backend **FastAPI** (Python) + UI **React** (localhost). Mesmo backend serve automação/CLI depois. |

## 4. Contexto desta conversa (o que já existe e por que migrar)

O repo atual **CorteX** (`/var/home/dx/Projects/CorteX`, GitHub `xdayon/CorteX`) **não é um app** — é um conjunto de **mods aplicados por cima do `podcli`** (app fechado: launcher Go + runtime hermético próprio em `~/.local/share/podcli`, com auto-update). `./apply.sh` copia `mods/` pra dentro da instalação. Isso cria a fragilidade "Frankenstein":

- Não é dono da base (podcli auto-atualiza e atropela os mods; roda 2.3.2 modificado enquanto oferece 2.4.5).
- Mods vivem fora de onde o app roda → dessincronização (pegamos `.pyc` velhos, confusão de versão de Python: hermético é 3.12.13, transcrição usa venv separado 3.x).
- **Três runtimes** disputando: python hermético do podcli (sem faster_whisper) + venv de transcrição (`/var/home/dx/Projects/transcription/venv`, faster_whisper 1.2.1 GPU) + CLIs externos claude/codex.

**Falha diagnosticada num teste real hoje:** o `podcli process` na etapa de seleção faz shell-out pros CLIs `claude` (deu timeout 90s — provável queda de 5G) e `codex` (deu erro `--full-auto deprecated` + `Not inside a trusted directory` — isso **não é sinal**, é a interface do CLI tendo mudado). Caiu numa heurística fraca, escolheu 1 corte e **travou antes de renderizar**. Ou seja: **boa parte da insatisfação com os cortes vinha do cérebro de seleção falhando**, não do motor de corte. Isso é justamente o que a arquitetura nova elimina.

### Ativos reutilizáveis (o IP real, já desenvolvido — portar pro Connex)

Existe a branch **`feature/audio-aware-editing`** (commit `d58c603`) no CorteX, com um **motor de edição profissional** gerado pelo Codex/GPT-5.6 a partir do design do §7, **já validado** (9/9 testes passam, imports OK, aplica limpo). Ainda **não** foi testado num render real de ponta a ponta (por causa da falha de seleção acima). Módulos a portar:

- `mods/runtime/backend/services/audio_editing.py` — boundary optimizer: RMS da waveform + VAD + word timestamps, perfis de ritmo (dynamic/balanced/contemplative/auto), fail-safe (não corta sem fronteira segura).
- `mods/runtime/backend/services/edit_quality.py` — quality gates (fronteira dentro de palavra, segmento inválido, render sem áudio/vídeo, duração divergente).
- `mods/runtime/backend/services/hook_overlay.py` — título visual `hook_text` em ASS (Montserrat, já instalada na máquina).
- `mods/runtime/backend/services/video_cut.py` — `cut_multi_segment` com `xfade`+`acrossfade` equal-power e fallback pro concat.
- Lógica relevante de `clip_generator.py`, `fasterwhisper_worker.py` (salva `speech_intervals` do Silero VAD), presets `Neat`/`Neat90`.
- Transcrição faster-whisper GPU já funciona (venv `/var/home/dx/Projects/transcription`).

**Ferramentas do sistema já instaladas (nada pra baixar):** ffmpeg com `ass`/`xfade`/`acrossfade`, NVENC/NVDEC, fonte **Montserrat**, `faster_whisper 1.2.1` (com API de VAD `decode_audio`/`VadOptions`/`get_speech_timestamps`), Node, yt-dlp (confirmar).

## 5. Arquitetura proposta do Connex

Repo único, um ambiente Python (**um venv, deps pinadas**), um `config.yaml`, sem overlay.

```
connex/
├── pyproject.toml            # deps pinadas (faster-whisper, ffmpeg-python, fastapi, yt-dlp, ...)
├── config.yaml               # todas as configs/otimizações num lugar (GPU, perfis, paths, estilos)
├── connex/
│   ├── ingest/               # arquivo local + youtube (yt-dlp), normalização
│   ├── transcribe/           # faster-whisper GPU, word-level + Silero VAD → transcript.json
│   ├── select/               # IA lê transcript → plano editorial (hook/segments/pacing/hook_text)
│   │   ├── prompts/          # prompts versionados (§6) — cidadão de primeira classe
│   │   ├── claude_headless.py# chamada à assinatura via Claude headless (subscription)
│   │   └── heuristic.py      # fallback local offline
│   ├── edit/                 # boundary optimizer, perfis de ritmo, transições (porta §4)
│   ├── render/               # Remotion (legendas) + FFmpeg NVENC, hook overlay
│   ├── quality/              # quality gates + eval set de regressão
│   ├── pipeline.py           # orquestra as fases, resumable/caching
│   └── api/                  # FastAPI (jobs, progresso via SSE/websocket)
├── frontend/                 # React (Vite) — UI local
└── tests/                    # unit + integração (render real curto)
```

### Princípios não-negociáveis
1. **GPU-first:** decode NVDEC, encode NVENC, transcrição CUDA. Se algo puder acelerar na GTX 1060, tem que estar ligado por padrão e testado.
2. **Fases desacopladas e resumíveis (tolerância a offline):** transcrição e render são **100% locais (sem rede)**; só a *seleção* precisa de internet. Cachear cada estágio em disco. Poder transcrever offline no ônibus, rodar a seleção quando o sinal voltar, e renderizar offline. Nada de perder trabalho quando o 5G cai.
3. **Progresso real e legível:** a UI e o CLI mostram % e etapa de verdade (a dor de hoje foi output "preso" no buffer via `tee`). Usar streaming de progresso (SSE/websocket) e stdout line-buffered.
4. **Config única e exposta:** perfis de ritmo, estilos de legenda, handles, crossfades, duração de cortes, etc. — tudo em `config.yaml` e/ou na UI.

## 6. Seleção por IA — prompts são um pilar

O usuário enfatizou: **os prompts têm que ser perfeitos** pra IA gerar os melhores cortes possíveis. Tratar isso como entregável de primeira classe, não como string solta.

- **Caminho:** Claude headless (subscription). Invocação robusta: working dir fixo/confiável, `--output-format json`, timeout explícito + retries com backoff. **Sem** fallback pro Codex CLI. Se offline após retries → `heuristic.py` local (energia + palavras-chave + perguntas/exclamações), marcando os cortes como "heurístico (revisar)".
- **Saída estruturada** (a IA devolve JSON, não texto): por corte → `hook_text` (4–9 palavras), `pacing` (dynamic/balanced/contemplative), e `segments[]` com `start/end/purpose(hook|context|payoff)/transition_out/timeline_order` (permitir **cold open**: começar pelo payoff e voltar ao contexto — ordem narrativa, não cronológica).
- **O prompt deve obrigar microestrutura:** `hook → contexto mínimo → payoff → última frase forte`. E pontuar (rubric do §7): força dos 2s iniciais, compreensão sem contexto, curiosidade aberta, carga emocional, clareza da promessa, quotabilidade, qualidade do payoff, segurança das fronteiras, potencial de compressão.
- **Few-shot em PT-BR** com exemplos de bons cortes do nicho do usuário.
- **Eval set de regressão:** montar 10–20 cortes reais (bons e ruins) e testar cada mudança de prompt/motor contra eles. Isso vira a métrica de qualidade.

## 7. Motor de edição — design de referência (validado pelo usuário)

Este é o design que o GPT-5.6 (SOL) propôs e o usuário aprovou; virou a branch `feature/audio-aware-editing`. Serve de especificação:

- **Boundary Optimizer:** a IA propõe onde cortar (aproximado); o motor acha a fronteira segura ao redor, checando VAD + energia da waveform + palavras + pontuação. Handles: **80–140 ms antes** da fala, **140–250 ms depois**. **Crossfade de áudio 30–80 ms**. Proibir corte dentro de voz detectada. Confiança baixa → **não corta** (preserva a pausa).
- **Três perfis de ritmo:** *Dinâmico* (hot takes, pausas comprimidas, jump cuts), *Equilibrado* (padrão, remove só silêncio vazio, preserva respiração/conclusão), *Contemplativo* (espiritualidade/emoção, preserva pausas dramáticas, transições suaves). Modo **auto** classifica por trecho.
- **Transições com função editorial** (eliminar o blur corretivo): jump cut limpo; punch-in 4–8% (disfarça corte do mesmo enquadramento); micro-dissolve 3–6 frames (emocional); **J-cut** (áudio da próxima fala entra antes da imagem, em troca de interlocutor); **L-cut**; micro-crossfade de áudio ao remover hesitação do mesmo falante. Usar **PySceneDetect** pra alinhar cortes a mudanças de cena reais do podcast (não pra acionar blur).
- **Hook falado + título visual:** `hook_text` separado do nome do arquivo — 4–9 palavras, 2 linhas, entrada suave, 2,5–4 s, safe zone superior (respeita UI do Instagram), consciente de rosto/legenda, complementar à fala (não repetição literal).
- **Quality gate obrigatório** antes de dar corte por pronto: nenhuma palavra atravessando fronteira; nenhum corte dentro de voz; sem estalo/salto de amplitude; legendas sincronizadas após crossfade; densidade de cortes compatível com o perfil; título sem cobrir rosto/legenda; sem frames pretos/congelados/borrados; áudio dentro do loudness/true-peak.

## 8. Roadmap sugerido (ordem de implementação)

1. **Fase 0 — Fundação:** repo Connex, venv único, `config.yaml`, esqueleto do pipeline, ingestão (arquivo + YouTube), transcrição GPU portada e funcionando ponta-a-ponta gerando `transcript.json` (word-level + VAD). *Critério:* transcrição perfeita de um episódio real, cacheada.
2. **Fase 1 — Speech Safety + Edição:** portar `audio_editing/edit_quality/video_cut/hook_overlay`; boundary optimizer + handles + micro-crossfade + perfis de ritmo. *Critério:* render real de 1 corte sem cortar fala, sem blur, com transição suave (o teste que não conseguimos rodar hoje).
3. **Fase 2 — Seleção IA + Prompts:** Claude headless (subscription) robusto + fallback heurístico + saída JSON estruturada + eval set. *Critério:* roteiros com hook/contexto/payoff e cold open, batendo o eval set.
4. **Fase 3 — Render/Legendas:** Remotion otimizado + NVENC + título visual + quality gate completo.
5. **Fase 4 — Frontend web:** FastAPI (jobs + progresso streaming) + React (enviar arquivo/link, escolher preset, acompanhar %, revisar/baixar cortes).
6. **Fase 5 — Transições avançadas:** J/L-cut contextual, PySceneDetect, punch-in.

Cada fase entrega algo testável de ponta a ponta. Priorizar 0→1→2 (o núcleo da dor).

## 9. Instruções para o orquestrador (Fable)

- Confirme com o usuário **um nome/local pro repo novo** (ex.: `/var/home/dx/Projects/Connex`) e o **remote GitHub** antes de scaffoldar.
- Delegue implementação bem-especificada pro `implementer` (Sonnet) ou `codex exec`; escreva specs tight. Use `advisor` (Opus) pra decisões arquiteturais grandes. **Revise todo diff** e rode/valide antes de reportar pronto.
- **Valide, não confie:** especialmente (a) a invocação headless exata do Claude que usa a assinatura sem custo por token — teste de verdade; (b) NVENC/NVDEC ativos na GTX 1060 em cada passe; (c) o render real de um corte (foi o teste pendente).
- Preserve os ativos: importe da branch `feature/audio-aware-editing` do CorteX; não reescreva o que já está validado.
- Não reintroduza: thumbnails, content studio, knowledge base, UI de "new episode", auto-update/overlay, shell-out pro Codex.

## 10. Perguntas em aberto (resolver com o usuário quando chegar a hora)
- Nome/caminho exato e repo GitHub do Connex.
- Estilo(s) visual(is) de legenda desejado(s) (herdar `branded/hormozi/karaoke/subtle`? criar o do Connex?).
- Quantos cortes por episódio por padrão e faixa de duração alvo (15–60s?).
- Formato de saída (só vertical 1080×1920? também quadrado?).
- Onde salvar os cortes finais (hoje: `~/Videos/podcli-clips` com subpasta por episódio).
