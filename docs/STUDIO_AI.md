# Estúdio IA — aulas e promos (CLI)

Vídeos montados a partir de clipes do Google Flow, cenas Manim, narração (ElevenLabs) e
música, com efeitos sonoros de um kit curado. Tudo por `python -m cortex.lessons` e
`python -m cortex.sound`; ainda sem UI. Valores (LUFS, durações, caminhos) em
`config/cortex.yaml` → `lessons.*` e `sound.*`.

## Perfis de som

| projeto | `lesson.json` | som |
| --- | --- | --- |
| Aula (Robert) | sem `sound` | narração + música clássica baixa e constante, sem ducking, sem efeitos |
| Promo (Dayon/Roxxie) | `"sound": {"kit": "dayon", "duck_music": true, "native_lufs": -26, "dips": [...], "sfx": [...]}` | música + som nativo dos clipes que abaixam sob a voz (~8–9 dB), efeitos, silêncio dramático |

`aspect` (`"16:9"`/`"9:16"`) define o quadro; clipes de outra proporção são cortados no
centro. O idioma da narração vem de `language` no `lesson.json` (alinhamento por palavra).

## Receita promo (THEY KNOW, 28/out/2026 — `data/lessons/roxxie-they-know`)

1. Narração primeiro: um beat por cena; o último beat narrado é o CTA ("Comente estilo…
   Use DAYON10…") e um beat `hold` de ~2,8 s segura o cartão final.
2. Clipes do Flow com `in`/`out` explícitos (o `check` mostra `retime`; < 1 acelera).
3. Som nativo dos clipes (`native_lufs`, `flow.audio_db`, `flow.mute`) no mesmo "bed" da
   música; `dips` puxa o bed para baixo (ex.: depois de "esquecer" até a revelação).
4. Efeitos com moderação: gancho, match cut, entrada de cena-chave, riser que acaba no
   silêncio, braam na revelação, carimbo no cupom.
5. Textos animados em Manim (o usuário prefere ao libass: 28/out, v2): tomadas `hybrid` com
   `manim.transparent` e `shade: false` (sobreposição pura, sem a sombra lateral das aulas 16:9).
   `manim.cues` sincroniza a cena com a narração (`{"in": {"at": "word:S03:alquimia", "offset": -0.1}}`);
   o `render` grava `scenes/shots.json` com a duração da tomada e cada deixa em segundos desde o início
   dela. O `scenes/brand.py` do promo traz o sistema: IBM Plex Mono por coluna (`TextLine`), efeitos
   `Typewrite`, `Decode`, `TrackIn`, `Ignite` e o `Director` (faixas por relógio, para animações que se
   sobrepõem). Medidas em px de um quadro de 1080 de largura, então a prévia em 540×960 é fiel.
   No máximo 3 frases na narrativa; texto entre 10 % e 78 % da altura, até 80 % da largura.
   `titles` (libass) continua disponível para legenda simples.
6. Cartão final: `kind: "manim"` com o PNG transparente do produto (recorte feito pelo usuário; o
   `ImageMobject.set_opacity` apaga o alfa, então a cena multiplica o canal alfa). CTA `COMENTE` /
   `ESTILO` / "e receba o link da coleção na DM" e o cupom em caixa dourada, cada um na sua palavra.
   `kind: "image"` com `key` fica como alternativa sem Manim.
7. `music.sound` aponta para a biblioteca; créditos saem em `export/credits.txt`. Para mistério,
   evitar trilhas marcadas `trailer`/`epic`/`brass` (a v1 virou épica aos 32 s); medir a curva de energia
   e o brilho antes, e comparar de ouvido as alternativas mixadas no próprio anúncio.

## Fluxo

```bash
python -m cortex.sound login                        # chave Soundza → .env (uma vez)
python -m cortex.sound kit candidates dayon         # busca por papel → audition.html
python -m cortex.sound kit pick dayon whoosh 1 4 7  # baixa e põe no kit
python -m cortex.sound kit reel dayon               # reel.wav com o kit inteiro
python -m cortex.lessons init <slug> --script roteiro.txt --narration voz.mp3 \
  --music trilha.wav --language pt --aspect 9:16 --promo
python -m cortex.lessons align <slug>               # palavras → timings.json
python -m cortex.lessons flow-pack <slug>           # durações a pedir no Flow
python -m cortex.lessons ingest <slug> [--source pasta]  # clipes NN_* ou Cena_N_* do Flow
python -m cortex.lessons sfx-auto <slug>            # whoosh nos cortes, riser+impacto no final
python -m cortex.lessons check <slug>               # tomadas e deixas resolvidas
python -m cortex.lessons render <slug>              # cenas Manim (podman) + scenes/shots.json
python -m cortex.lessons assemble <slug>            # export/<slug>_<N>p.mp4, report.json, credits.txt
```

## Deixas de efeito (`sound.sfx`)

- `at`: `shot:<id>` (o corte para a tomada), `word:<beat>:<palavra>` (primeira vez que a
  palavra é ouvida no beat) ou `t:<segundos>`; `offset` desloca.
- `role` (papel do kit; cada uso pega o próximo som do papel) ou `sound` (`soundza:<id>`).
- `align: "anchor"` (padrão) põe o hit do som no instante: o `sync_ms` do Soundza ou o
  pico de energia medido; um riser termina no corte, um whoosh tem o pico nele.
  `"start"` começa o som ali. `trim` encurta com fade de 50 ms; `gain_db` ajusta.
- Nível: cada efeito é levado a `lessons.sfx_lufs` pelo trecho mais alto de 400 ms antes
  da normalização final (−14 LUFS).

## Fontes e licença

Soundza (principal: licença própria cobre anúncio, sem crédito obrigatório; pedem
"Sound effects: soundza.com" na descrição ou @soundza_audio no Instagram) e SFXMint (CC0).
Freesound/Jamendo/Openverse ficaram de fora para uso comercial — ver
[evidência](evidence/2026-10-28-sound-libraries.md). Rodar `python -m cortex.sound removals`
uma vez por mês (exigência do Soundza para arquivos guardados).
