# Auditoria completa (2026-11-10)

Branch `audit/2026-11`, backup na tag `pre-audit-2026-11-10` (antes de qualquer mudança).
Método: três varreduras somente leitura (backend, frontend, docs/regras), um juiz
independente que reabriu cada citação (20 afirmações + 18 rotas, todas confirmadas) e
validação real numa cópia reflink de `data/` com API + worker do código novo na porta 8797.

## Medidas (mesmos dados, cópia de `data/`)

| Rota | Antes | Depois |
|---|---|---|
| Tarefas da pílula de atividade (a cada 3–15 s) | `GET /jobs?limit=20`: 1,15 MB, 0,16 s | `GET /jobs?active=true&view=summary`: 295 B, 0,03 s |
| Lista de episódios | `GET /episodes`: 1,29 MB, 0,65 s | 140 KB, 0,44 s |
| Página de um episódio | catálogo inteiro com arquivados: 1,48 MB | `GET /episodes/{id}`: 39 KB, 0,16 s |
| Prévia no editor | ecoava `confirmed_voice` (~190 KB) em cada plano/render | só `workflow_run_id`; mesma chave de cache |

Prévia de ponta a ponta no editor (corte de 1:45 → refino 1:25, `wk7PY31TQBc`): plano de
edição com voz resolvida no servidor + refino Claude + render draft em 72 s; legenda e
manchete do `.ass` desenhadas pelo JASSUB (conferido no Chrome do Flatpak em 1440 e 390 px,
sem rolagem horizontal nem erro de console).

## Achados corrigidos

- `prompts/*.schema.json` com `"$schema": draft 2020-12` (adicionado durante a auditoria)
  fazia o Claude CLI recusar `--json-schema` ("no schema with key or ref"): todo refino
  falhava. Só a prévia real pegou; agora um teste proíbe `$schema`.
- Cache da transcrição ignorava os nomes próprios por episódio (`hotwords`).
- `watchJob` rejeitava na primeira queda do SSE; o fallback de polling nunca entregava.
- `cortex.service` ficava `failed` a cada fechamento do app (saída 143).
- `update_and_verify.sh` conferia o FFmpeg do sistema, gravava a `dist` servida antes de
  checar jobs e religava o serviço parado.
- Seleções antigas (7 critérios de 1 a 5) mostravam "Nota 34" ao lado de notas 1–10.
- Miniatura 4:3 do YouTube estourava o quadro 16:9 dos cartões (faixa preta e arte cortada).
- `clips.*` do YAML nunca era lido; padrões do código divergiam do YAML (modelo, esforço,
  timeout, encoder das aulas…), e `.env.example` tinha chaves que quebrariam o config.

## Testes

Backend: 604 → 596 funções de teste, ~709 casos coletados pelo pytest com os parametrizados.
Saíram os testes de publicação, presets e do formato HTTP das rotas apagadas; os testes de
worker que usavam essas rotas foram portados com as mesmas asserções; entraram
cache/config/respostas, estado "postado" e hooks. Frontend: 183 → 192 testes.

## Para o usuário decidir (não alterado)

Provider Codex mantido; diarização pyannote + `.venv-diarization` (1,4 GB) congelada;
tipos de job legados só para ler o SQLite antigo; `cortex.eval`; acesso remoto (túnel nunca
instalado); dividir `render/service.py` (2445 linhas); `data/lessons` ocupa 106 GB e o
`maintain_data.py` não cobre aulas.

## Decisões do usuário (10/nov, tarde)

Codex mantido. Diarização completa removida (7h40 na única execução que terminou); o
reconhecimento rápido de voz fica (166 s na 1ª vez, depois cache). Acesso remoto removido
(uso só no PC). `render/service.py` não será dividido por ora. `data/lessons` fica: os
57 GB de `build/` + `check/` são cache de montagem das aulas ainda em revisão. Drive
automático entregue. Áudio: prévia rápida de 96k → 192k (medição em 30 s do episódio
`0feL2iE8Ubw`: SNR de codificação 23,2 dB a 96k, 39,4 dB a 192k; loudnorm 82 dB, limpo).
