# CorteX Editing Engine

## Objetivo

O motor transforma uma sugestao semantica aproximada em uma timeline
deterministica, segura para fala e reproduzivel. A LLM decide **o que** preservar
e a intencao narrativa; o motor decide **onde** cortar em frames e samples. Uma
fronteira proposta pela LLM nunca deve ir diretamente ao FFmpeg.

## Artefatos de analise

Cada fonte e identificada por hash e analisada uma unica vez. Os resultados sao
cacheados no mesmo sistema de coordenadas da fonte:

- transcricao com timestamps por palavra e confianca;
- alinhamento forcado para regioes de baixa confianca;
- VAD com intervalos de fala;
- waveform com RMS, pico, zero crossing e spectral flux;
- loudness, true peak e amostras limpas de room tone;
- diarizacao e identidade editorial dos speakers;
- shot boundaries, layout, face tracks, embeddings e mouth motion;
- FPS, timebase, duracao e keyframes da midia.

Todos os tempos internos usam microssegundos inteiros. Segundos decimais sao
aceitos apenas nas APIs de entrada e saida.

## EDL resolvida

A EDL real separa video e audio. Isso permite J-cuts, L-cuts e reaction shots sem
forjar sincronismo:

```json
{
  "schema_version": "1.0",
  "timeline": { "timebase": "1/1000000", "width": 1080, "height": 1920 },
  "events": [
    {
      "id": "evt-002",
      "timeline_in_us": 8400000,
      "timeline_out_us": 14300000,
      "video": {
        "source_id": "episode",
        "source_in_us": 721800000,
        "source_out_us": 727700000,
        "visual_role": "interviewer_reaction",
        "crop_track_id": "face-host-1"
      },
      "audio": {
        "source_id": "episode",
        "source_in_us": 694200000,
        "source_out_us": 700100000,
        "gain_db": 0,
        "continuity_group": "primary-speech"
      },
      "transition_in": {
        "video": "hard_cut",
        "audio": "equal_power_crossfade",
        "audio_duration_us": 60000
      }
    }
  ]
}
```

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
entrevistador. O lead padrao e 180 ms e deve permanecer abaixo de 450 ms para
fala rapida.

**L-cut:** o audio atual continua apos a troca visual. Serve para sustentar uma
frase enquanto a imagem revela o interlocutor ou um plano aberto. A cauda deve
ser curta e nao pode expor fala labial conflitante no novo plano.

**Jump cut:** e um hard cut visual entre instantes nao contiguos. O audio usa
crossfade curto sample-safe ou room tone; o video nao usa dissolve por padrao.
Saltos fortes da mesma camera devem ser mascarados por punch-in alternado, plano
aberto ou reaction shot. Dissolve fica reservado a mudanca perceptivel de tempo,
local ou atmosfera, pois em rostos ele cria ghosting.

## Reaction shots

Um reaction shot nunca e B-roll arbitrario. O candidato deve satisfazer todos os
gates obrigatorios:

- identidade confirmada como entrevistador/interlocutor, nao o speaker principal;
- face visivel, track estavel e enquadramento utilizavel em 9:16;
- diarizacao indica que o interlocutor nao esta falando;
- mouth motion abaixo do limiar de fala visivel;
- ausencia de troca de layout, frame congelado ou oclusao relevante;
- preferencia por proximidade temporal ao trecho principal;
- audio do reaction shot mutado; permanece o audio continuo da fala principal.

O score combina listening posture, expressao, estabilidade, nitidez, proximidade
temporal e seguranca labial. Se qualquer gate obrigatorio falhar, usar o speaker
principal, plano aberto ou punch-in. Nao inserir labios pronunciando outra fala.

## Planejamento de camera

O planner recebe a EDL resolvida, diarizacao e indice visual. Ele deve:

- manter o primary subject como ancora narrativa;
- evitar permanecer mais que 12 a 18 segundos no mesmo enquadramento quando
  houver alternativa segura;
- inserir ao menos uma reacao curta por corte quando existir candidato valido;
- trocar enquadramento em mudancas de beat, nao no meio de uma palavra;
- limitar pans digitais e preferir composicoes estaveis;
- respeitar safe zones de headline, legenda e interfaces de Reels/TikTok.

## Pipeline de render

FFmpeg e responsavel por decode, trims, audio, crop/reframe, color e encode final.
Remotion e responsavel por headline, legendas animadas e overlays de marca.

Pipeline alvo:

1. Gerar proxy apenas para preview, sem afetar o master.
2. Resolver EDL e crop tracks antes do render.
3. Produzir no maximo um intermediario visual quando Remotion precisar de alpha.
4. Compor headline e captions na mesma passagem grafica.
5. Aplicar loudness e encode no composite final, evitando reencodes sucessivos.
6. Usar NVDEC/NVENC somente apos probe funcional; fallback de CPU deve ser
   explicito no job e na telemetria.

## Quality gates

### Antes do render

- timestamps validos, ordenados e dentro da fonte;
- nenhuma fronteira dentro de palavra ou VAD protegido;
- soma da EDL dentro dos limites de duracao;
- score e soma editorial validos;
- reaction shots aprovados pelos gates de identidade e mouth motion;
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

