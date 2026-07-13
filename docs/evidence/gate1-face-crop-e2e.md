# Gate 1 — E2E real do `face_static_crop` (crop facial estático)

Data: 2026-07-13
Sessão: validação E2E no host (GPU CUDA + NVENC), branch `feature/audio-aware-editing`.
Executado via API real (`cortex-api`) + worker real (`python -m cortex.worker`), config real
(`config/cortex.yaml`, `data_dir=data`). Nenhum código de produção foi alterado nesta sessão
(apenas execução e observação); o único "erro" encontrado (rejeição de
`camera_edit_plan_artifact_id` com `face_static_crop`) é comportamento fail-closed
documentado no plano, não um bug.

## Fonte e recorte real

- Episódio: `147ea06269174318a13a946f6e8a167d` ("4 Raças de ETs Foram Revelados? Dayon News
  Explica Tudo | Prosa Inversa 20"), MP4 1080p/1.6 GB em
  `data/projects/147ea062.../source/`.
- Recorte extraído com `ffmpeg -ss 1800 -t 240 -c copy` (stream copy, sem recodificação):
  4 minutos (240s) a partir dos 30 min do episódio, região de fala contínua entre dois
  apresentadores.
- Arquivo: scratchpad da sessão → ingerido como novo source asset do projeto de teste.

## Projeto de teste

- Nome: `gate1-e2e-face-crop`
- Project id: `20879e160f534d9ea820a15de8c63f0b`
- Source asset id: `3a53613407004a2a91e3f32ae6559dc4`
  (`gate1_segment.mp4`, sha256 `6c49670785b0...`, 1920x1080, 240.03s)
- Criado aditivamente; nada em `data/` de projetos existentes foi alterado ou removido.

## Cadeia de jobs executada (API real + worker real, produção)

Todos os jobs abaixo rodaram pela API HTTP real (`POST /api/v1/projects/{id}/...`) e foram
processados pelo worker real (`cortex.worker.process_next`, mesmo caminho usado em produção,
não TestClient/mocks). GPU CUDA confirmada (`./scripts/check_gpu.sh` passou antes da sessão:
NVIDIA GTX 1060, driver 580.159.04, NVENC h264 ok, CTranslate2 cuda int8 ok).

| # | Stage | job id | status | duração | observação |
|---|-------|--------|--------|---------|------------|
| 1 | transcription | `dccfe38c...04be` | succeeded | 22.1s | faster-whisper large-v3-turbo, device=cuda, compute_type=int8, fallback=false |
| 2 | analysis | `a05b9f10...8242d` | succeeded | 7.4s | local |
| 3 | scene_analysis | `66af13c6...0bef4` | succeeded | 21.5s | scdet, 22 cortes de cena em 240s |
| 4 | face_analysis | `88169b72...755c` | succeeded | 185.3s | YuNet, schema v2 |
| 5 | speaker_analysis | `4517376f...b7fa` | succeeded | 14.4s | schema v2 |
| 6 | visual_quality_analysis | `4b5ec190...18ac` | succeeded | 282.5s | sample_fps 2.0, sem black/freeze |
| 7 | edit_plan (descartado) | `fb714d74...a986` | succeeded | 226.0s* | start=70.5 end=130.2 — **descartado**, ver "Iteração 1" abaixo |
| 8 | camera_analysis | `e0f880b3...00b5` | succeeded | 1.1s | schema v1 |
| 9 | identity_analysis | `c6fbf861...2c` | succeeded | 2.2s | SFace, cross-layout, schema v1 |
| 10 | camera_planning (descartado) | `406887b3...bdb7` | succeeded | 0.3s | sobre o edit_plan #7 |
| 11 | render (falhou, fail-closed) | `73dd9270...c8431` | **failed** | 0.8s | `face_static_crop ainda não aceita camera_edit_plan` — comportamento esperado (ver Gate 1: "combinação com camera_edit_plan continua fail-closed") |
| 12 | render (fallback central) | `0c87c3c7...f5b` | succeeded | 308.3s | sobre edit_plan #7 — identidade confirmada não aparecia no intervalo 70.4–130.37s → `fallback=center_crop` auditável. **Não é a evidência final** |
| 13 | edit_plan (final) | `1e65a6d4...d679` | succeeded | 0.7s | start=0.0 end=69.8 — reescolhido para cobrir a janela onde a identidade confirmada aparece |
| 14 | camera_planning (final) | `711d5f01...5615` | succeeded | 0.2s | sobre edit_plan #13 |
| 15 | **render (final, evidência)** | `864ea456...0c1` | **succeeded** | 475.4s | ver detalhes abaixo |

\* Durações incluem espera em fila (worker único, processamento serial — igual a produção com
um worker ativo); jobs 7 e 10 competiram na fila com o job 6 (visual_quality, 282s), inflando a
duração aparente do job 7.

### Iteração 1 → 2: por que o primeiro clipe foi descartado

O primeiro corte escolhido (70.5–130.2s do trecho de 240s, região com diálogo contínuo sobre
"shapeshifters") caiu fora da janela em que qualquer identidade **confirmed** do
`identity_index` aparece (todas as 3 identidades confirmadas só têm observações entre 0.0s e
64.0s do trecho — a barra de amostragem de faces é 1 fps e o clipe escolhido inicialmente ficava
inteiramente depois do último frame com identidade confirmada). O render desse clipe
**teve sucesso técnico** (quality gate aprovado) mas usou o fallback central auditável
(`sample_count: 0`, `fallback: "center_crop"`, `effective_target_identity_id: null`) — o
comportamento fail-closed correto, mas não evidência do caminho principal de face tracking.

Corrigido recriando o `edit_plan` para o intervalo `0.0–69.8s` do mesmo trecho (segmentos 0–2
da transcrição, 69.8s, dentro do limite 45–75s pedido), onde as identidades confirmadas têm
amostras reais. O `camera_edit_plan` e o render foram refeitos sobre esse novo `edit_plan`.

## Identidades encontradas e escolha do alvo

`identity_index_artifact_id = 31d1f27433b44b60be0a5b7100bc1ff9`
(`data/projects/.../identities/identity-index-4c5b62ef089fb54a.json`)

- 143 identidades detectadas no total; `unresolved_face_count = 0`.
- **3 identidades com status `confirmed`**:

| identity_id | sample_count | minimum_pair_similarity | janela de observação (trecho de 240s) |
|---|---|---|---|
| `identity-94af0fa0e24a` | **39** | 0.382 | 0.0 – 35.0s |
| `identity-e147cad69b12` | 38 | 0.374 | 0.0 – 64.0s |
| `identity-9cf8e209da50` | 12 | 0.622 | 36.0 – 47.0s |

- Todas as demais identidades (~140) são `ambiguous` (a maioria com `sample_count=1`) ou
  `single_layout`; nenhuma foi promovida silenciosamente.
- **Alvo escolhido**: `identity-94af0fa0e24a` — maior `sample_count` (39) entre as confirmadas.

## Render final (evidência)

- `render_artifact_id`: `31140f3aab64440d8ee45b17c679fe36`
- Manifesto: `data/projects/20879e160f534d9ea820a15de8c63f0b/renders/render-3e3470e471d38695.json`
  (**schema_version 7**)
- MP4: `data/projects/20879e160f534d9ea820a15de8c63f0b/renders/render-3e3470e471d38695.mp4`
  (1080x1920, h264 yuv420p, 30fps, 68.90s, 105.515.908 bytes = ~100.6 MiB)
- SRT: `data/projects/20879e160f534d9ea820a15de8c63f0b/renders/render-3e3470e471d38695.srt`
  (2864 bytes, sidecar habilitado)
- Configuração solicitada = efetiva: `encoder=h264_nvenc` (sem troca silenciosa de engine),
  canvas 1080x1920@30fps, `framing.mode=face_static_crop`, captions habilitadas (Montserrat,
  karaoke, outline, shadow), headline habilitada, `subtitles.sidecar_srt=true`.
- **Sem `camera_edit_plan_artifact_id`** — face_static_crop com camera_edit_plan é fail-closed
  nesta versão (ver job #11); render usou apenas `edit_plan_artifact_id` +
  `face_index_artifact_id` + `identity_index_artifact_id` + `target_identity_id`, como previsto.

### Quality gate / publicação

```
quality.passed = true
quality.has_video = true / has_audio = true
quality.duration_delta_seconds = 0.038
quality.integrated_loudness_lufs = -14.0  (target -14.0, delta 0.0)
quality.true_peak_dbfs = -5.8  (limite -1.0 — dentro)
quality.black_interval_count = 0 / freeze_interval_count = 0
quality.caption_cue_count = 37 / subtitles_present = true / headline_present = true
publication.publish_ready = true
publication.reasons = []
```

Quality gate **aprovado** sem ressalvas; `publish_ready=true`.

### Manifesto v7 — `static_face_crops` com provenance

Dois segmentos (`segment_count=2`, `timeline_duration_seconds=68.811`):

**Segmento 0** (fonte 0.0–16.972s):
```
crop_x=1074, crop_y=0, crop_width=608, crop_height=1080
provenance.method = static_weighted_median
provenance.requested_target_identity_id = identity-94af0fa0e24a
provenance.effective_target_identity_id = identity-94af0fa0e24a   (== requested)
provenance.target_selection = confirmed_identity
provenance.sample_count = 24
provenance.fallback = null
provenance.temporal_motion = false
```

**Segmento 1** (fonte 18.086–69.97s, o maior segmento, 51.884s):
```
crop_x=619, crop_y=0, crop_width=608, crop_height=1080
provenance.method = static_weighted_median
provenance.requested_target_identity_id = identity-94af0fa0e24a
provenance.effective_target_identity_id = identity-94af0fa0e24a   (== requested)
provenance.target_selection = confirmed_identity
provenance.sample_count = 16
provenance.fallback = null
provenance.temporal_motion = false
```

Ambos os segmentos usaram o caminho principal de face tracking (não fallback), com
`effective_target_identity_id` igual ao solicitado — nenhuma identidade foi trocada
silenciosamente. `crop_x`/`crop_y` são **um único valor escalar por segmento** (não uma série
temporal), portanto a ausência de pan/zoom é garantida estruturalmente pelo próprio filtro
FFmpeg usado no render (`crop={w}:{h}:{x}:{y},scale={W}:{H}` aplicado uma vez por segmento,
`src/cortex/render/service.py:250-251`) — não há como o crop variar dentro do segmento.

## Verificação por frames — ausência de movimento/vertigem

Segmento verificado: **segmento 1** (maior, 51.884s, `crop_x=619`).

Dentro desse segmento, o `scene_index` revela que a cena do estúdio efetivamente corta várias
vezes (câmeras alternadas, 22 cortes no trecho de 240s; 6 deles caem dentro do segmento 1:
38.5s, 42.5s, 44.1s, 46.0s, 63.4s, 67.9s). As 16 amostras que embasam o `crop_x=619` estão
todas entre 21.4s e 35.0s (fonte), dentro de um único plano contínuo (cena de índice 1:
21.1–38.5s). Para provar ausência de pan **dentro do plano onde a identidade foi de fato
amostrada**, foram extraídos 4 frames espaçados do MP4 final, em `t_fonte = 22, 27, 32, 37s`
(mapeados para `t_saída = 20.886, 25.886, 30.886, 35.886s` via offset do segmento):

```
ffmpeg -ss <t> -i render-3e3470e471d38695.mp4 -frames:v 1 out_<t>.png
```

- Inspeção visual: nos 4 frames o fundo (livro "Academia de Líderes", câmera em tripé, quadro
  "Renata ...", luminária amarela, taça na mesa) permanece **pixel-a-pixel na mesma posição**;
  apenas a pessoa se move naturalmente (cabeça, mãos, fala). Nenhum pan, zoom ou deriva de
  câmera é visível.
- Métrica numérica: SSIM de uma faixa de fundo estático (canto superior esquerdo, região do
  livro, 100x600px, longe do rosto) entre os 4 frames:

  | par | Δt (fonte) | SSIM (faixa de fundo) |
  |---|---|---|
  | 22s vs 27s | 5s | 0.9464 |
  | 27s vs 32s | 5s | 0.9491 |
  | 32s vs 37s | 5s | 0.9447 |
  | 22s vs 37s | 15s | 0.9452 |

  O SSIM permanece estável (~0.945–0.949) independente da distância temporal — se houvesse
  pan/zoom, o SSIM cairia com o aumento de Δt (deriva acumulada). A variação residual (~0.05)
  é consistente com ruído de compressão H.264/NVENC, não com movimento de câmera.

### Risco observado: crop extrapolado além da janela amostrada

O segmento 1 do `edit_plan` cobre 18.086–69.97s da fonte, mas contém **6 cortes de câmera
reais** (a fonte é um vídeo com câmeras alternadas already "queimadas" no arquivo, sem
metadata de shot). As amostras da identidade confirmada só existem em 21.4–35.0s (dentro da
primeira cena do segmento). Do segundo 38.5s até o fim do segmento (69.97s), o mesmo
`crop_x=619` é aplicado **sem amostras adicionais** — extraí um frame em `t=45s` (fonte) e
confirmei visualmente que o plano físico mudou (parede escura, TV — nada a ver com o cenário
amostrado) enquanto o crop permanece fixo. Isso não é vertigem/pan (o crop continua
estruturalmente estático, `temporal_motion=false` continua verdadeiro), mas é um enquadramento
não validado por amostra real fora da primeira cena do segmento — exatamente a limitação já
documentada no Gate 1 ("combinação com `camera_edit_plan` continua fail-closed enquanto não
houver índice por fonte/shot"). Recomendação para o próximo gate: usar
`camera_edit_plan`/shot index também no `face_static_crop` para recomputar o crop por shot, não
por segmento inteiro do `edit_plan`.

## Verificação item a item (checklist da tarefa)

- [x] job de render SUCCEEDED, MP4 existe, SRT existe, quality gate aprovado
- [x] manifesto v7 com `static_face_crops`: samples (24 e 16), `fallback=null` nos dois
  segmentos usados como evidência, `temporal_motion=false`, `effective_target_identity_id`
  igual ao solicitado
- [x] maior segmento verificado por frames (4 frames espaçados, plano coerente
  21.1–38.5s): sem pan/zoom, `crop_x/crop_y` citados e constantes
- [x] tempo de cada stage e tamanho dos artifacts registrados acima
- [x] fail-closed comprovado ao vivo: tentativa de combinar `face_static_crop` com
  `camera_edit_plan_artifact_id` falhou imediatamente com mensagem clara, sem publicar nada

## Riscos e observações finais

1. **Extrapolação de crop além da janela amostrada dentro do mesmo segmento do edit_plan**,
   quando o segmento atravessa cortes de câmera reais na fonte (detalhado acima). Não bloqueia
   o Gate 1 (aceite fala em "índice por fonte/shot" como trabalho futuro), mas deve ser
   priorizado antes de liberar `face_static_crop` para clipes com múltiplos cortes de câmera
   sem revisão humana.
2. **face_sample_fps=1.0** (default) faz com que a cobertura de amostras de identidade seja
   esparsa (1 amostra/s); nesta sessão isso foi suficiente para localizar identidades
   confirmadas, mas a escolha do intervalo do clipe é sensível a essa esparsidade (o primeiro
   clipe testado ficou sem cobertura só por estar fora da janela amostrada).
3. Nenhuma identidade foi promovida silenciosamente; das ~143 identidades apenas 3 chegaram a
   `confirmed`, e a escolhida foi a de maior `sample_count`, exatamente como pedido.
4. Todos os artifacts, MP4, SRT e manifesto ficaram no projeto de teste
  `20879e160f534d9ea820a15de8c63f0b` (`gate1-e2e-face-crop`), sem tocar em nenhum projeto ou
  arquivo pré-existente em `data/`.

## Ambiente

- GPU: NVIDIA GTX 1060 6GB, driver 580.159.04, NVENC h264 confirmado
  (`./scripts/check_gpu.sh`).
- Transcrição: faster-whisper `large-v3-turbo`, `device=cuda`, `compute_type=int8`,
  `fallback=false` (sem troca silenciosa de engine).
- Render: `h264_nvenc` solicitado = efetivo (sem fallback para libx264).
- API real (`cortex-api`, porta 8787) e worker real (`python -m cortex.worker`) rodando como
  processos separados, mesma arquitetura de produção, apontando para `config/cortex.yaml`
  (`data_dir=data`).

## Alterações de código nesta sessão

Nenhuma. Apenas execução e observação (validação E2E). O único erro encontrado
(`face_static_crop` + `camera_edit_plan_artifact_id` → HTTP 201 com job `failed` e mensagem
clara) é o comportamento fail-closed intencional documentado no plano do Gate 1, não um bug a
corrigir.
