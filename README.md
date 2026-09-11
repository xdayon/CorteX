# CorteX

Editor pessoal Dayon News/HiTechX para transformar podcasts do YouTube ou arquivos
locais em cortes para Reels/TikTok, usando o notebook e sua GPU.

**Estado:** MVP com artefatos reais e testes. Ainda faltam lote de exportação
independente da aba, confirmação de voz no plano final e avaliação ampla com
podcasts reais. [Roadmap](docs/ROADMAP.md) · [Revisão completa](docs/REVIEW_DAYON_NEWS.md).

## Usar

1. Abrir a biblioteca, adicionar o link ou arquivo e preparar o episódio.
2. Para focar em Dayon, separar vozes e confirmar sua amostra antes de gerar
   sugestões; [diarização opcional](docs/DIARIZATION.md).
3. Processar com Codex CLI, revisar sugestões e escolher os cortes.
4. Ajustar legendas, texto da headline por corte e enquadramento; gerar prévia
   curta real e exportar MP4/SRT. Resultados e mídia permanecem locais.

Selecionar fonte/configuração não inicia processamento. O workflow persiste até
revisão. Ajustes, seleção, headlines e correções têm autosave por execução nesse
navegador; não são sincronizados entre navegadores. Jobs enviados sobrevivem à
aba, mas **mantenha a aba aberta para agendar o lote inteiro**.

## Edição disponível

- Fonte única do YouTube, biblioteca e galeria dos renders persistidos.
- Transcrição por palavra e seleção Codex com prompt PT-BR/saída validada.
- Correção textual por corte preservando áudio/timestamps; tamanho, altura,
  fonte/peso, cores, outline, sombra, karaoke/pop e SRT.
- Headline manual por corte, sugestão IA quando vazia e botão de restaurar sugestão.
- Quadro inteiro com **Gaussian blur** por padrão, sem exigir análise facial.
- Automático opcional, limitado aos cortes selecionados + 3 s de margem; correção
  por cena esquerda/direita/ambos e zoom discreto. Em dúvida preserva quadro inteiro.
- Reutilizar reações é opcional, requer entrevistador confirmado e usa só vídeo.
- EDL e motores de pausas, J/L-cut, punch-in e presets; a UI não expõe todos os
  controles avançados da API. Não existem câmeras ISO além do master recebido.
- Prévia curta (~10 s) e QA técnico do MP4. Preview tipográfico não demonstra
  enquadramento/headline finais; confira o vídeo renderizado.

## Instalação

Python 3.11–3.14 (3.12 recomendado para instalar a stack principal), Node instalado,
FFmpeg/ffprobe e Codex CLI autenticado. A máquina atual tem GTX 1060 Max-Q 6 GB:
transcrição CUDA `int8`, encode H.264 NVENC. Visão/diarização usam CPU.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev,transcription,analysis,youtube]'
(cd apps/web && npm ci && npm run build)
(cd apps/remotion && npm ci && npm run build)
```

Configuração em `config/cortex.yaml` e overrides `CORTEX__SECAO__CHAVE`.
`render.remotion_browser` deve apontar para um `chrome-headless-shell` executável;
o renderer não baixa browser nem troca silenciosamente para libass.
O processo não carrega `.env` sozinho; o serviço systemd o carrega.
Nunca versionar tokens, login Codex ou mídia.

## Abrir sem terminal

Instalar uma vez o serviço de usuário e o atalho do menu de aplicativos:

```bash
.venv/bin/python scripts/install_local_service.py --install
```

Clique **CorteX** no menu: o atalho inicia o serviço, aguarda a API e abre o
navegador. O serviço também inicia no login. Endereço padrão:
[Studio local](http://127.0.0.1:8787). Requer notebook ligado e acordado.

```bash
systemctl --user status cortex.service
systemctl --user restart cortex.service
systemctl --user disable --now cortex.service
```

Após atualizar código, compilar Web/Remotion e reiniciar o serviço. Arquivos em
`data/` não são backupados pelo GitHub. Para endereço privado próprio, a proposta
é **Tunnel + Access com processamento local**; nenhum deploy foi realizado.
[Operação e Cloudflare](docs/LOCAL_AND_CLOUDFLARE.md).

## Desenvolvimento e verificação

Pare o serviço antes de iniciar outro worker: `systemctl --user stop cortex.service`.
`./scripts/dev.sh` inicia Vite em 5173, API em 8787 e worker. A interface usa a API
real via `/api`; [OpenAPI local](http://127.0.0.1:8787/docs).

```bash
.venv/bin/pytest -q
(cd apps/web && npm test && npm run build)
(cd apps/remotion && npm run build)
.venv/bin/ruff check src/cortex scripts tests
git diff --check
./scripts/check_gpu.sh
```

No host com browser: `CORTEX_RUN_REMOTION_E2E=1 .venv/bin/pytest -q` habilita os
três testes Remotion normalmente pulados. GPU smoke não comprova episódio E2E.
[Resultados desta revisão](docs/evidence/2026-09-10-architecture-review.md).

## Para agentes

Leia [AGENTS](AGENTS.md), [handoff curto](docs/HANDOFF_FABLE_5.md) e o gate do
[ROADMAP](docs/ROADMAP.md). O [mapa da arquitetura](docs/ARCHITECTURE.md) indica
módulos e testes. Históricos ficam em `docs/history/`, fora da leitura inicial.
Codex editorial usa exclusivamente `gpt-5.5`, medium, pelo CLI/login ChatGPT;
[provenance e contrato](docs/AI_PROVIDERS.md). Isso exige rede e limites da conta,
mas não exige API key. Não restaurar frontend PodCLI ou importar `mods/`.
