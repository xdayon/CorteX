# CorteX

Editor pessoal de cortes virais do @dayon.news: transforma entrevistas de podcast
(link do YouTube ou arquivo local) em cortes 9:16 para Reels e TikTok, com IA para
escolher e enxugar as falas, legenda animada, manchete e edição seca — tudo
processado no seu computador. [Roadmap](docs/ROADMAP.md) ·
[Estado atual](docs/HANDOFF.md).

## Usar

1. **Novo episódio:** cole o link (ou envie o arquivo), defina quantidade e faixa de
   duração, nomes próprios do episódio (ajudam a transcrição) e a direção editorial.
2. **Escolher cortes:** a IA entrega até 25 cortes com manchete recomendada, opções
   alternativas, trechos montados e começo limpo; marque os que quer.
3. **Exportar:** para cada corte, a prévia é o vídeo real da edição com a legenda
   exata da exportação. Revise a legenda por blocos (corrigir palavra, destacar,
   ocultar, localizar e substituir), escolha estilo, enquadramento e manchete, e
   renderize. O lote continua no servidor mesmo se a aba for fechada.

## O que a edição faz

- Seleção por IA (Claude Code CLI na assinatura, sem API): uma chamada por episódio,
  transcrição em cache; refino por corte exportado (gancho mais forte, sem gordura).
- Corte seco: pausas ≥ 0,25 s e muletas removidas; muletas do começo do corte saem.
- Legenda libass com estilos clássico, palavra a palavra, pop e caixa; palavras de
  destaque em amarelo; manchete com selo (temporizada ou o corte inteiro).
- Fundo desfocado com zoom e foco horizontal (rosto maior mantendo o estúdio) ou tela
  cheia; zoom alternado nos cortes; flash ou zoom na junção entre trechos.
- QA técnico: loudness −14 LUFS/−1 dBTP, sincronia, e legenda fora das áreas da
  interface do Instagram/TikTok. Exporta MP4 + SRT + manifest.

## Instalação

Python 3.12, Node, FFmpeg/ffprobe com libass e Claude Code CLI logado na assinatura.
Máquina atual: GTX 1060 (transcrição em CUDA); encode em NVENC pelo FFmpeg portátil do
Jellyfin configurado em `render.ffmpeg`, porque o do sistema não abre o NVENC com o driver
da placa ([detalhes](docs/evidence/2026-11-02-nvenc-jellyfin.md)).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev,transcription,analysis,youtube]'
(cd apps/web && npm ci && npm run build)
```

Configuração em `config/cortex.yaml` (fonte da verdade para modelos, esforços,
durações e caminhos), com overrides `CORTEX__SECAO__CHAVE`. O processo não carrega
`.env` sozinho; o serviço systemd carrega. Nunca versionar tokens, login ou mídia.

## Abrir sem terminal

```bash
.venv/bin/python scripts/install_local_service.py --install
```

O atalho **CorteX** no menu inicia o serviço e abre o
[Studio local](http://127.0.0.1:8787) numa janela própria; ao fechar a janela o serviço
para (se houver job na fila, espera terminar). Para atualizar dependências, rodar testes e
trocar a interface com segurança (recusa se houver job em andamento; com o app fechado, a
versão nova vale na próxima abertura):

```bash
./scripts/update_and_verify.sh
```

Backup e retenção de cache (só aplicam com `--apply`):

```bash
.venv/bin/python scripts/maintain_data.py backup --output /caminho/cortex.tar.gz
.venv/bin/python scripts/maintain_data.py prune-cache --max-age-days 30
```

## Desenvolvimento e verificação

Pare o serviço antes de iniciar outro worker (`systemctl --user stop cortex.service`).
`./scripts/dev.sh` sobe Vite (5173), API (8787) e worker.

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src/cortex scripts tests
(cd apps/web && npx tsc --noEmit && npm test && npx vite build --outDir /tmp/cortex-web --emptyOutDir)
git diff --check
```

`apps/web/dist` é servida ao vivo: compile em outra pasta (como acima); só o
`update_and_verify.sh` troca a `dist` servida, e só sem job em andamento.

## Estúdio IA (aulas e promos)

`python -m cortex.lessons` monta aulas 16:9 e promos 9:16 a partir de narração
(ElevenLabs), clipes do Flow, cenas Manim e música; `python -m cortex.sound` cuida da
biblioteca de som. Fluxo em [STUDIO_AI](docs/STUDIO_AI.md) e estado em
[LESSONS_HANDOFF](docs/LESSONS_HANDOFF.md).

## Para agentes

Leia [AGENTS](AGENTS.md), o [handoff](docs/HANDOFF.md) e a fase aberta do
[ROADMAP](docs/ROADMAP.md); o [mapa da arquitetura](docs/ARCHITECTURE.md) indica
módulos e testes, e [AI_PROVIDERS](docs/AI_PROVIDERS.md) o contrato da IA. Históricos
ficam em `docs/history/`, fora da leitura inicial.
