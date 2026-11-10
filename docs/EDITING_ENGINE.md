# CorteX Editing Engine

## Objetivo

O motor transforma uma sugestao semantica aproximada em uma timeline
deterministica, segura para fala e reproduzivel. A LLM decide **o que** preservar
e a intencao narrativa; o motor decide **onde** cortar em frames e samples. Uma
fronteira proposta pela LLM nunca deve ir diretamente ao FFmpeg.

## Artefatos de analise

Cada fonte e identificada por hash e analisada uma unica vez. Os resultados sao
cacheados no mesmo sistema de coordenadas da fonte:

- transcricao com timestamps por palavra e confianca (`transcribe/`);
- VAD com intervalos de fala e energia/waveform (`analyze/audio.py`);
- loudness e true peak;
- voz do protagonista: rotulos D/O por frase a partir do perfil de voz
  (`diarize/voice_match.py`); diarizacao pyannote so manual;
- cortes de cena (scdet), rostos em poucas amostras por cena (YuNet) e mapa de
  cameras por keyframes (`analyze/`);
- FPS, timebase, duracao e keyframes da midia.

Os tempos internos sao segundos (`float`) no relogio absoluto do master; o render
converte para quadros exatos por segmento.

## Plano de edição (EDL resolvida)

O contrato real é `EditPlanDocument` em `src/cortex/edit/schemas.py`: segmentos com tempos
da fonte em segundos (`float`, relógio absoluto do master), `audio_start`/`audio_end` separados
do vídeo para J/L-cuts, `piece_index` da peça escolhida pela IA e as inserções de cena da
triangulação (que trocam só a imagem). O render (`render/service.py`) converte para quadros
exatos por segmento.

Invariantes:

- eventos de video nao se sobrepoem, exceto overlays declarados;
- audio pode anteceder ou ultrapassar o evento visual;
- nenhuma fronteira de fala fica dentro de palavra ou regiao VAD protegida;
- toda referencia de fonte permanece dentro da duracao da midia;
- a timeline final e monotona e possui duracao calculavel antes do render;
- cada desvio A/V e intencional e registrado no manifesto.

## Resolucao de fronteiras

Para cada entrada e saida aproximada:

1. Localizar palavras e regioes VAD adjacentes.
2. Criar handles de pre-roll e post-roll conforme o perfil de ritmo.
3. Procurar, dentro do raio permitido, regioes de baixa energia e baixo spectral
   flux; preferir zero crossing quando houver empate.
4. Rejeitar candidatos dentro de palavra, fala protegida ou respiracao relevante.
5. Preservar pausas de fim de frase dentro do teto retorico do perfil.
6. Limitar densidade de cortes e unir eventos curtos demais.
7. Se nenhuma fronteira for segura, manter a pausa ou ampliar o segmento. Nunca
   cortar uma silaba apenas para atingir a duracao alvo.

A mesma resolucao se aplica a EDL com um ou varios segmentos. Planos da LLM nao
recebem tratamento privilegiado.

## J-cuts, L-cuts e jump cuts

**J-cut:** o audio do proximo evento inicia antes da troca visual. E apropriado
para introduzir uma resposta enquanto ainda vemos a escuta/reacao do
entrevistador. O teto do deslocamento vem do perfil de ritmo (`max_jl_offset` em
`edit/profiles.py`); J/L-cut e opcional por plano.

**L-cut:** o audio atual continua apos a troca visual. Serve para sustentar uma
frase enquanto a imagem revela o interlocutor ou um plano aberto. A cauda deve
ser curta e nao pode expor fala labial conflitante no novo plano.

**Jump cut:** e um hard cut visual entre instantes nao contiguos. O audio usa
crossfade curto sample-safe ou room tone; o video nao usa dissolve por padrao.
Saltos fortes da mesma camera devem ser mascarados por punch-in alternado, plano
aberto ou reaction shot. Dissolve fica reservado a mudanca perceptivel de tempo,
local ou atmosfera, pois em rostos ele cria ghosting.

## Reacoes e planos abertos (triangulacao)

A antiga analise de camera/identidade/falante e o planejador de camera foram removidos.
Hoje a imagem de outra camera entra como inserção que troca só o vídeo, mantendo o áudio
contínuo da fala principal: plano aberto e entrevistador vindos do mapa de câmeras do
episódio, com a imagem emprestada conferida por quadros. Detalhes em
[TRIANGULATION.md](TRIANGULATION.md); código em `analyze/scene_suggestions.py`,
`analyze/footage_check.py`, `analyze/triangulation.py` e inserções em `render/service.py`.
O enquadramento por rostos fica em `render/face_focus.py`.

## Pipeline de render

FFmpeg e responsavel por decode, trims, audio, crop/reframe, color e encode final.
libass (`render/ass_overlay.py`) desenha headline e legendas animadas no mesmo encode do FFmpeg.

Pipeline alvo:

1. Gerar proxy apenas para preview, sem afetar o master.
2. Resolver EDL e crop tracks antes do render.
3. Compor headline e captions na mesma passagem grafica.
4. Aplicar loudness e encode no composite final, evitando reencodes sucessivos.
5. Usar NVENC somente apos probe funcional (`render.encoder`); sem fallback silencioso
   para CPU, e o encoder pedido e o efetivo ficam no manifesto.

## Quality gates

### Antes do render

- timestamps validos, ordenados e dentro da fonte;
- nenhuma fronteira dentro de palavra ou VAD protegido;
- soma da EDL dentro dos limites de duracao;
- score e soma editorial validos;
- inserções de cena com a câmera conferida por quadros;
- headline dentro de limite e sem divergencia factual;
- densidade de cortes compativel com o perfil.

### Depois do render

- streams de audio e video presentes e duracao coerente com a EDL;
- sync A/V dentro de 40 ms, salvo offsets editoriais declarados;
- ausencia de frames pretos, congelamentos inesperados e discontinuidades de PTS;
- clicks de audio e saltos de waveform abaixo do limiar;
- loudness integrado no alvo com tolerancia de 1 LU e true peak seguro;
- sem clipping, audio ausente ou canais invertidos;
- captions sem overlap, orfaos, overflow ou violacao de safe zones;
- headline legivel e livre da area critica das interfaces sociais;
- arquivo decodificavel do inicio ao fim e `faststart` habilitado.

Falhas obrigatorias bloqueiam publicacao. Warnings permitem preview, mas ficam no
manifesto do render. Auto-fix e limitado, deterministico e nunca pode borrar
legendas ou substituir uma fronteira de fala insegura por um efeito visual.

