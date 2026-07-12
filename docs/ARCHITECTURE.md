# Arquitetura do CorteX

## Objetivo

O CorteX e um editor local, GPU-first e retomavel para transformar episodios de
podcast em cortes verticais profissionais. Este repositorio e a fonte unica do
produto. O runtime do PodCLI, seus bundles e o venv externo de transcricao sao
apenas referencias temporarias de migracao.

## Limites do produto

O fluxo principal contem seis momentos explicitos:

1. importar arquivo local ou URL do YouTube;
2. configurar e iniciar a transcricao;
3. configurar o brief e solicitar sugestoes de cortes;
4. revisar transcript, waveform, headline, enquadramento e plano editorial;
5. configurar o visual e iniciar o render;
6. validar, comparar e exportar os resultados.

Selecionar uma fonte ou alterar uma configuracao nunca inicia trabalho pesado.
Transcricao, analise e render possuem comandos separados.

## Monorepo

```text
apps/api/                  FastAPI, jobs e streaming de progresso
apps/web/                  Studio React/TypeScript da HiTechX
packages/cortex/           Dominio e pipeline Python
packages/remotion/         Composicoes de legenda e headline
config/                    Configuracao global do runtime
presets/                   Presets visuais e editoriais versionados
prompts/                   Prompts, schemas de saida e exemplos PT-BR
data/                      Projetos, cache e artefatos locais (ignorado pelo Git)
tests/                     Unidade, integracao, E2E e fixtures
```

Durante a migracao, o codigo original permanece em `mods/` somente para
comparacao. Novos modulos nao podem importar `PODCLI_*`, caminhos absolutos ou
arquivos da instalacao do PodCLI.

## Pipeline retomavel

Cada etapa le entradas imutaveis e grava um manifesto com schema, hash, engine,
versao, duracao e artefatos. Uma etapa concluida pode ser reutilizada quando seu
hash de entrada e configuracao continuar igual.

```text
ingest -> normalize/probe -> transcribe -> audiovisual analysis
       -> suggest -> human review -> edit plan -> render -> quality gate
```

Os jobs sao persistidos em SQLite e executados por um worker local. Cancelar ou
perder a rede nao apaga resultados anteriores. Apenas ingestao por URL e selecao
por LLM exigem rede; transcricao, edicao e render sao locais.

## Responsabilidades

- **faster-whisper/CTranslate2:** transcricao CUDA, timestamps por palavra e
  Silero VAD. `large-v3-turbo` e o padrao; `large-v3` fica disponivel para uma
  passada de maxima qualidade.
- **FFmpeg:** normalizacao, waveform, cortes, J/L-cut, crossfade, crop, loudness,
  composicao final, NVDEC e NVENC.
- **Remotion:** animacao de legenda, karaoke e headline em overlay. Nao decide
  cortes e nao substitui o FFmpeg.
- **LLM:** propoe narrativa, headline, perfil e regioes aproximadas. Nunca decide
  sozinha uma fronteira fisica.
- **Boundary optimizer:** ajusta fronteiras usando palavras, VAD, RMS, pontuacao,
  cenas e handles de fala. Em baixa confianca, preserva o trecho.
- **Quality gate:** bloqueia palavra cortada, voz atravessada, dessincronia,
  ausencia de stream, duracao incorreta, clipping e layout fora da safe area.

## Edicao e cameras

O plano editorial usa uma EDL com relogios separados para audio e video. Isso
permite J-cut e L-cut reais. Trocas de camera e reaction shots precisam vir de um
mapa de cenas/faces e de uma janela semanticamente relacionada. Nunca se injeta
um rosto arbitrario apenas para variar a imagem.

Perfis de ritmo:

- `dynamic`: comprime silencio vazio, favorece jump cut e punch-in;
- `balanced`: padrao profissional, preserva respiracao e conclusao;
- `contemplative`: preserva pausas retoricas e usa transicoes discretas;
- `auto`: classifica por trecho, mas registra a escolha efetiva.

## GPU e transparencia

GPU-first nao significa fallback silencioso. Cada passe registra `requested` e
`effective` para device, decoder e encoder. A interface mostra CUDA, NVDEC e
NVENC ativos ou sinaliza claramente CPU/x264.

A telemetria combina NVML e psutil: uso/VRAM/temperatura/potencia da GPU, CPU,
RAM, disco e throughput. A existencia de `h264_nvenc` na listagem do FFmpeg nao
prova que o driver esta funcional; o health check executa um encode curto real.

## Presets

Presets sao autocontidos, versionados e divididos em:

- `visual`: legenda, headline, camera e output;
- `content brief`: objetivo editorial e prompt;
- `project`: combinacao dos dois com overrides.

Assim, um brief sobre falas biblicas pode ser combinado com o visual Neat90 sem
duplicar configuracoes.

## Gates de entrega

Uma fase so e considerada concluida com um artefato real e verificavel:

1. transcript JSON com palavras, VAD e device efetivo;
2. corte com fronteiras seguras e audio sem estalo;
3. sugestoes estruturadas validadas por schema;
4. legenda/headline sincronizadas e editaveis;
5. frontend controlando jobs reais e mostrando telemetria;
6. E2E de episodio real com relatorio de qualidade.
