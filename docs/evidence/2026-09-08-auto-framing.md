# Master único: reenquadramento por cena e falante visual

Data: 2026-09-08. Escopo: corrigir o centro da mesa nos cortes verticais de um
master já comutado pelo estúdio, usando os modelos locais existentes.

## Mudança e limite

O episódio de teste tinha dois rostos com o mesmo `track_id=person` no plano
aberto. O agrupamento por posição agora é local a cada cena, com diâmetro máximo
0,18 e rejeição de colisões simultâneas. O algoritmo de faces v3 invalida o cache
v2; a interface solicita a cadeia atual antes do render automático.

`speaker_auto` reenquadra turnos visuais sustentados (mínimo 1,2 s, confiança
mínima 0,6), preserva pessoa única visível inclusive quando está ouvindo e segura
oscilações inferiores a 0,75 s. Em ambiguidade mantém a imagem inteira com fundo
blur. Não existe identificação acústica nem escolha de câmera ausente no master.
Correções esquerda/direita/ambos e zoom estático opcional são persistidos no render.

## Evidência real no host

- GPU: GTX 1060 Max-Q, 6144 MiB, driver 580.173.02. `check_gpu.sh` executou H.264
  NVENC e confirmou um device CTranslate2 CUDA com `int8`, `int8_float32`, `float32`.
- Projeto: `d7066b6201f34e0292fd8bbc2ceaa4e3`; fonte local previamente ingerida,
  derivada de episódio real já editado. Não foi repetido o download do YouTube
  nem a transcrição completa neste gate.
- Novo índice de faces: `e7466b0772e44c878c1c4c59f5504d53`; primeiro plano agora
  distingue `person_left` de `person_right`.
- Comparação: mesma EDL, 34,33 s, 1080x1920, 30 fps, H.264 NVENC. O frame em 1 s
  do modo central mostra mesa/TV; o automático mostra o rosto da esquerda.
  Em 20 s, preserva a pessoa que aparece no plano fechado do estúdio.
- Automático: `renders/render-71b3ee4a646c2e50.json`, `publish_ready=true`, delta
  A/V 0,003008 s. Warning explícito `auto_framing_context_preserved` nos planos
  sem evidência suficiente para escolher um rosto.
- Antes: `renders/render-4a9108ccc3700a10.json`, `publish_ready=true`, delta A/V
  0,030 s. Warning de cena quase estática; áudio e duração preservados.
- SHA-256 do áudio decodificado, idêntico nos dois arquivos:
  `0698cbe0a61a2c1b978e5949abf2d1732f6e9e5e564676f22ac68539c847578c`.
- Capturas e resumo local: `data/qa/auto-framing-20260908/summary.json`,
  `compare-1.jpg`, `compare-20.jpg`, `studio.png` (ignorados pelo Git, sem publicar
  mídia privada no repositório).
- Browser real: Chrome Headless Shell configurado pelo projeto abriu a interface,
  restaurou um WorkflowRun real e exibiu Exportar com `speaker_auto`, sem alerta
  de erro nem overflow horizontal a 1440 px. Esta inspeção não acionou render
  pelo navegador nem fez uma nova ingestão.

## Verificação reproduzível

```bash
.venv/bin/pytest -q tests/test_auto_framing.py tests/test_face_classify.py tests/test_render_punch_in.py tests/test_camera_edit_plan.py
CORTEX_RUN_REMOTION_E2E=1 .venv/bin/pytest -q
npm run build --prefix apps/web
npm test --prefix apps/web
npm run build --prefix apps/remotion
.venv/bin/ruff check src/cortex/render/auto_framing.py src/cortex/render/service.py src/cortex/render/schemas.py src/cortex/analyze/face_classify.py src/cortex/analyze/face_service.py src/cortex/api.py tests/test_auto_framing.py
git diff --check
./scripts/check_gpu.sh
```

As regressões cobrem mudança de falante dentro do mesmo plano, ouvinte natural,
ausência/baixa confiança/colisão de track, segurança geométrica, correção manual,
cadeia cross-project/source, pixels de MP4 real, continuidade do áudio e cache
invalidado por alteração dos índices. O frontend testa preparação automática,
opt-in de reações, falha explícita e envio da correção manual ao render.

## Pendências de produto

Revisão humana de diferentes estúdios e episódios completos; exportação ainda
agendada pela aba. Seleção/settings/resultados da tela não são restaurados ao
reabrir. A análise visual completa continua custosa na primeira execução;
cache evita repeti-la. Não foram adicionados pyannote, novas bibliotecas CUDA,
tracking contínuo nem fontes ISO. A comparação visual acima testa enquadramento,
sem pretender aprovar semanticamente todos os cortes do episódio.

Resultado da verificação final: **351 testes backend passaram**, incluindo os
E2E reais de Remotion/browser (`CORTEX_RUN_REMOTION_E2E=1`, 270,22 s, sem skips).
**21 testes frontend passaram**; builds Web/Remotion, Ruff dos arquivos alterados
e `git diff --check` passaram.

A demonstração completa também terminou: `renders/render-369b1b40d1fb4407.json`
e MP4/SRT correspondentes, 34,33 s com captions/karaoke Remotion e punch-in
solicitado 1,15×, efetivo 1,0× ou 1,15× por plano. `publish_ready=true`, delta A/V
0,003008 s; somente warning de preservação do quadro inteiro. Segunda execução
retornou `cached=true`. Frame em 1,5 s inspecionado visualmente. A codificação VP9
do overlay continua CPU e levou alguns minutos neste host; não se alega aceleração
GPU das legendas nem benchmark completo de um episódio.
