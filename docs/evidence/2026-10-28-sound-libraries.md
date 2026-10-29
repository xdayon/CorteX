# Bibliotecas de som para o Estúdio IA — estudo de 28/out/2026

Comparação de Openverse, Soundza, SFXMint, Freesound, Jamendo e API League para música e
SFX em vídeo de **uso comercial** (propaganda da coleção). Testes com `curl` anônimo.

## Decisão proposta

| fonte | veredito | por quê |
| --- | --- | --- |
| Soundza | **integrar (principal)** | licença própria cobre anúncio, sem crédito; WAV com loudness igualado; pacotes de edição; API ~1 s |
| SFXMint | **integrar (complemento)** | CC0, sem chave; set `video-editing` com 14 papéis; metadados de ataque/cauda/LUFS |
| Freesound | só curadoria manual pelo site | maior acervo e melhor ranking, mas API grátis só para fins não comerciais |
| Jamendo | não para anúncio | API grátis só não comercial; faixas na maioria NC; comercial = Jamendo Licensing pago |
| API League | não | agregador genérico; plano grátis 50 tokens/dia, não comercial e com backlink |
| Openverse | não | instável (500/504 por ~20 min); entrega o mesmo preview MP3 do Freesound |

## Openverse (`https://api.openverse.org/v1/`, schema do commit 639e926)

## Catálogo de áudio (`/v1/audio/stats/`)

| fonte | itens | serve para |
| --- | --- | --- |
| freesound | 591.898 | efeitos sonoros (whoosh, impacto, riser…) |
| jamendo | 644.709 | música (maioria NC; uso comercial não medido, ver abaixo) |
| wikimedia_audio | 3.986.828 | pronúncias, gravações clássicas em domínio público |

## Comportamento real

- `category=sound_effect` devolve **0** para `whoosh`: itens do Freesound vêm com
  `category: null`. Filtrar SFX por `source=freesound`, não por categoria.
- `q=whoosh` → 240 resultados (teto anônimo = 12 páginas × 20); `license_type=commercial`
  mantém 240. `q=riser&source=freesound&license=cc0` → 240, todos CC0.
- Arquivo entregue (`url`) do Freesound = preview `cdn.freesound.org/previews/*-hq.mp3`
  (128 kbps). O WAV original (`alt_files`) exige OAuth do próprio Freesound.
- Registro traz `license`, `license_version`, `license_url`, `creator`,
  `foreign_landing_url`, `attribution` pronto, `duration` (ms), `tags`, `audio_set`.
- `page_size=50` anônimo → `page_size may not exceed 20 for anonymous requests`.
- Instabilidade: das 11:27 às ~11:45 (BRT) toda busca nova deu 504 (60 s) ou 500 (30 s),
  inclusive Jamendo, Wikimedia, imagens e Freesound, com ou sem `filter_dead=false`;
  `/rate_limit/` e respostas em cache do Cloudflare seguiram 200. Cliente precisa de
  timeout curto, retry com backoff, cache local e erro explícito.
- Jamendo comercial (`license=cc0,by,pdm`) e imagens: **não medidos** (API fora).

## Limites (headers ao vivo + `WordPress/openverse` main)

| modo | burst | diário | page_size | profundidade/consulta |
| --- | --- | --- | --- | --- |
| anônimo (headers) | 20/min | 200/dia | 20 | 240 |
| registrado e verificado | 100/min | 10.000/dia | 50 | 240 |
| "enhanced" (pedido manual) | 200/min | 20.000/dia | 240 | 4.800 |

Só busca/detalhe contam; o download vem do CDN de cada fonte. Registro:
`POST /v1/auth_tokens/register/` (`name` único, `description`, `email`) → `client_id` +
`client_secret` (mostrados uma vez) → confirmar o e-mail (sem isso vale o limite anônimo)
→ `POST /v1/auth_tokens/token/` (form-urlencoded, `grant_type=client_credentials`) →
`Authorization: Bearer`, com `expires_in`.

## Termos (docs.openverse.org/terms_of_service.html)

Proibido scraping e burlar limites; o Openverse não verifica licenças (conferir na
`foreign_landing_url`); atribuição obrigatória em CC BY; app deve indicar "feito com
Openverse, sem endosso".

## Soundza (`https://api.soundza.com/v1`)

- Busca sem chave: 20/min, 300/dia, 3 páginas de 20, sem download. Download exige chave
  grátis (login por dispositivo): 60 buscas/min, 250 downloads/h, 500/semana, até 500 sons
  guardados. Resposta da busca em ~1 s em todos os testes.
- Licença Soundza Standard: uso comercial inclusive anúncio e sync, sem atribuição; proíbe
  redistribuir o arquivo solto, registrar como próprio (Content ID) e treinar IA; "as is",
  sem indenização.
- WAV com loudness igualado (SFX −13 LUFS, música/loops −16, ambiência −20, pico ≤ −1 dBTP);
  `?variant=original` traz o arquivo enviado. Sons gerados por IA vêm marcados `ai_content`.
- Medido: `whoosh` 500 resultados; `impact hit` 500; `riser` 406; `camera shutter` 22;
  `type=music&q=cinematic trailer` 48 (faixas de ~2 min, algumas com tom); `dark trap` 96
  (loops com BPM). Pacotes úteis: "Video Editing Sound Effects" (77), "Risers, Uplifters and
  Transitions" (84), "Ultimate Risers Vol 1" (54), "IMPACT — Trailer Braams" e "BLACKOUT —
  Impacts, Downlifters and Drops".

## SFXMint (`https://sfxmint.com/api/v1`)

- 7.700+ sons CC0, sem chave. Download por API: 20 sons distintos/dia por rede sem chave,
  50 com chave grátis; depois 429 até meia-noite UTC.
- `GET /search?q=…&response=structured` traz `duration_ms`, `audible_ms`, `attack_ms`,
  `tail_ms`, centroide, LUFS, pico e sha256 de WAV/MP3. `GET /sets/video-editing`: whoosh,
  swoosh, riser, braam, impact, pop, scratch, ding, applause, laugh, boing, typing, shutter,
  cash. Títulos numerados em série ("… Burst 36") sugerem acervo em boa parte gerado; ouvir
  antes de aprovar.

## Freesound (`/apiv2`, só documentação; precisa de chave para testar)

- Chave (token) para busca e previews: 60/min, 2.000/dia. Original (WAV) exige OAuth2:
  30/min, 500/dia. Previews: hq-mp3 ~128 kbps, hq-ogg ~192 kbps.
- Busca: `filter` por `duration`, `license`, `tag`, `avg_rating`, `num_downloads`, `bpm`,
  `loudness`…; `sort` por `downloads_desc`/`rating_desc`; `page_size` até 150; similaridade.
- Termos da API: "You can use the Freesound API for free only for non-commercial purposes";
  comercial via formulário de contato. A licença de cada som (CC0/BY) vale à parte.

## Jamendo (`developer.jamendo.com/v3.0`, só documentação)

- Termos 3.3: API livre só para uso não comercial (inclui receita de publicidade); outro uso
  via licensing@jamendo.com; proíbe app feito para cachear conteúdo. Plano grátis: 35 mil
  requisições/mês. `/tracks` filtra `speed`, `vocalinstrumental`, `durationbetween`,
  `ccnc`/`ccnd`/`ccsa`, `prolicensing`/`probackground` (catálogo pago), `audiodownload_allowed`.

## API League

- Agregador genérico (apileague.com). Grátis: 50 tokens/dia, 1 req/s, não comercial, backlink
  obrigatório; pago a partir de US$ 7/mês. Busca sem chave → 401. Origem dos sons não informada.

## Integração (mesmo dia, com chave Soundza)

- `python -m cortex.sound kit candidates dayon`: 13 papéis, 196 candidatos em 20 s (cache
  local depois). Com chave, 100 % têm prévia e `download_url`; `sync_ms` em ~55 %
  (o resto usa o pico medido); `logo` veio todo `ai_content`; `braam` só 4 candidatos.
- Teste de montagem 9:16 (`tests/test_lessons_sound.py`): música acima de 3 kHz −31,4 dB
  livre contra −40,5 dB sob a voz (ducking de 9,1 dB); efeito em `t:4.5` +24 dB na sua
  faixa no instante certo; 6,000 s, −14,06 LUFS, −1,93 dBTP.
- Bug antigo corrigido: placa com `minterpolate` (tomada desacelerada) perdia 2 quadros
  (46 em vez de 48 a 15 fps), e o vídeo terminava ~0,13 s antes do áudio; `fps` depois
  da interpolação resolve (chave `plate-v2`).

## Primeira promo: THEY KNOW (`data/lessons/roxxie-they-know`)

- 7 clipes do Flow (720×1280, 24 fps, áudio próprio −18,9 a −36,3 LUFS; Whisper só
  alucina "You"/"Thank you" → sem fala) + narração ElevenLabs de 52,4 s → 56,4 s em
  1080×1920 com cartão final de 9,4 s (mockup real recortado + CTA + cupom).
- Defeitos achados e corrigidos com teste:
  - dip sem efeito: depois de loudnorm/amix os quadros de áudio têm segundos e
    `volume eval=frame` lê `t` uma vez por quadro → `asetnsamples=1024` + `asetpts=N/SR/TB`
    (teste: −30 dB no trecho);
  - `colorkey` furava áreas claras da estampa → recorte por preenchimento a partir da
    borda (`lessons/cutout.py`);
  - pico: mix −1,8 dBTP virava −0,43 dBTP no AAC (+1,35 dB nos booms) → o loop do mix
    mede o AAC que vai no arquivo e aperta o limitador; o encode final copia esse AAC.
- Títulos com 20 caracteres estouravam 1080 px no efeito `track` → largura calculada
  (fonte mono) e reduzida para caber em 80 % do quadro.

## THEY KNOW v2 (feedback do usuário, 28/out)

- Trilha v1 ("Cinematic Trailer Build 2", tags `trailer, epic, taiko, brass`): energia por 4 s
  −34 → −13 dB e brilho 1,0 → 2,0 kHz depois dos 32 s; o usuário ouviu "alegre, épica". Trocada por
  "Slow-Burn Thriller Underscore 4" (Lá menor estimado, r = 0,85; −20 → −17 dB, brilho 1,1–1,6 kHz,
  sobe ~4 dB só no cartão final). Alternativas mixadas no anúncio: Dark Suspense Underscore 3
  (Ré menor, plana em −14,6 dB), Mysterious (cordas, Mi menor, não IA), Dark Hypnotic Electronic Pulse 2.
- Efeito "Shade Sound 11" no corte S01→S02 rejeitado ("esquisito, não conversa com o tema");
  trocado por "Muffled Sub Drop" do kit `dayon` (58 sons escolhidos pelo usuário).
- Manim 0.19 em 9:16: a câmera mantém a largura (14,22 × 25,28 unidades); `Text` corta a linha
  depois de ~77 caracteres (área do Pango), por isso os glifos mestres vão em linhas de 24;
  `ImageMobject.set_opacity` apaga o alfa do PNG; objetos guardados em mobjects precisam
  sobreviver a `deepcopy`. Render das 5 cenas (1080×1920, 30 fps): 66 s.
