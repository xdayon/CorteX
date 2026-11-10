# Estúdio IA (`cortex.lessons`): handoff da fase 2

Leia isto e [STUDIO_AI.md](STUDIO_AI.md) (CLI, perfis de som, receita de promo). Não reler o histórico
de sessões nem `data/lessons/piloto/` inteiro: as decisões estão aqui.

## Produto
Aulas 16:9 do curso do Robert Edward Grant, feitas com o Gabriel Lírio, e depois as aulas do próprio
usuário. Também promos 9:16 (Dayon News). A referência de qualidade é `data/lessons/i-quarter-turn/` (aprovada).

**Divisão de trabalho (decidida em 2026-10-28)**
- O ChatGPT do Gabriel escreve o roteiro e todos os prompts. O contrato está em `docs/chatgpt-gabriel/`: as instruções,
  o guia visual e os formatos de entrega, que são lidos por máquina.
- O usuário gera a narração (ElevenLabs), os clipes do Flow (manual, assinatura AI Pro, nunca API) e o clone do
  Robert (HeyGen).
- O CorteX alinha, valida, faz o Manim e monta.
- Ciclo: Etapa 1 (narração, um parágrafo por beat, vai direto para `init --script`) → `init` + `align` + `check`
  (a tabela de tempos volta para o Gabriel) → Etapa 2 (pacote visual) → importação.

## Estado (2026-10-28; tudo no `main` desde então)
- A fase 1 está pronta: `lessons/` com spec, align, timeline, ingest, flow_pack, manim (podman) e assemble
  (cache por hash de conteúdo). A aula de referência reproduz o piloto bit a bit (PSNR inf nas 17 tomadas).
- Outra sessão acrescentou no mesmo branch: promos, `sound/` (Soundza/SFXMint), `lessons/sound.py`,
  `titles.py`, `cutout.py`, tomadas `image`, `aspect` 9:16, fontes IBM Plex Mono e STUDIO_AI.md. Preserve tudo.
- Testes: `.venv/bin/pytest -q tests/test_lessons_*.py tests/test_sound_*.py` passa (49 em 10/nov).
- `i-quarter-turn` tem `music.gain_db: -6`: o Robert quer a música bem baixa (padrão das aulas dele).
- Aulas em produção: o curso Polymath Academy. O handoff da produção está em `data/lessons/_polymath/HANDOFF.md`, fora do Git.
  A Intro está montada (v1, sem avatar). Lições para o código: o plano precisa definir `flow.out`, senão o assemble acelera o
  clipe; o número do clipe aceita qualquer quantidade de dígitos (4 desde 04/nov); o `ingest` exige todos os clipes.

## Regras que valem para toda a fase 2
- **Narração primeiro.** Alinhar e só então escrever os prompts do Flow com a duração exata. O Flow (Omni 1.1
  Flash) só gera 4/6/8/10 s, 720p, x1–x4: `lessons.flow_durations`, `flow_max_retime`, `timeline.flow_request`.
  Na i-quarter-turn, 01/05/07/14 passam de 11 s → "split the beat".
- **Fidelidade editorial:** nunca afirmar além do paper. O vídeo do NotebookLM exagerava ("i não é necessário",
  "elimina erros de ponto flutuante", "blueprint da realidade") e errava sinais (J₁ invertido). A IA propõe;
  a matemática das cenas é verificada com SymPy (ver `piloto/verify_*.py`).
- **Flow inventa letras e números** (a bússola veio com "120" ao lado do W). Todo prompt termina com
  "No text, no letters, no numbers, no symbols, no people, no faces", e a folha de quadros do `ingest` é revisada.
  O Flow é ruim em morph geométrico preciso ("feixe que se curva como vidro" virou mangueira) e bom em movimento
  simples (ponto de luz que deixa rastro, prumo que assenta, câmera lenta).
- **Estilo dos prompts do Gabriel:** o número do segmento vem antes da 1ª palavra ("02 Photorealistic cinematic
  shot, 8 seconds. …"), com batidas 0–Xs, linha de câmera e bloco de estilo/paleta fixo. Temas: fotorrealista,
  geometria sagrada, natureza, arquitetura monumental, cosmos. Câmera fluida, transições do micro ao cósmico.
  Nada de olhos, galáxias ou figuras humanas repetidos; cenas concretas, sem metáfora genérica. Exemplos aprovados
  em `data/lessons/i-quarter-turn/lesson.json` (`shots[].flow.prompt`).
- **Tokens:** montagem, áudio, QC e render são código e não gastam token. A IA entra em no máximo 1–2 chamadas por
  aula. Cena Manim nova (código) é o único trabalho caro: as 11 do piloto foram escritas pelo Sonnet a partir de
  specs, com revisão visual do Opus.
- **Cache:** mudar a chave de um estágio em `assemble.py` invalida tudo. As placas com `minterpolate` levam
  cerca de 30 min para refazer, então só mude chaves quando a saída de fato mudar.

## Próximos itens
A fila da fase 2 (alinhamento com dica do roteiro, divisão de falas, importação do pacote do
Gabriel, tomada `avatar`, biblioteca de componentes Manim, provider Gemini e a aba "Aulas") está no
[ROADMAP](ROADMAP.md#estúdio-ia--fase-2-aulas-e-promos), com os critérios de aceite.

## Decisões pendentes (perguntar ao usuário)
- Opções do HeyGen (item 4): o usuário manda o print do painel.
- Mudar o contrato (`docs/chatgpt-gabriel/`) exige reenviar os arquivos ao Gabriel: mantenha o formato estável.

## Verificação
`.venv/bin/pytest -q` (suíte completa) · `git diff --check` · aulas: `python -m cortex.lessons check <slug>` e a
folha de quadros. Não reiniciar `cortex.service` nem rodar `npm run build` em `apps/web/dist` (AGENTS.md).
