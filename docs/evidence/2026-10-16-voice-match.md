# Voice match (rótulo D/O por voz) — 2026-10-16

Substitui a diarização pyannote (9547 s no episódio de 9906 s) como forma de saber
quais linhas da seleção são do Dayon. Código: `diarize/voice_match.py`,
`diarize/voice_runner.py` (torch só no subprocesso `.venv-diarization`), config `voice.*`.

## Pesquisa (episódio wk7PY31TQBc, 3 vozes, SPEAKER_00 = Dayon)
- Modelo: embedding WeSpeaker ResNet34 do pyannote community-1, offline, cosseno contra
  uma impressão de voz do Dayon; unidades = frases (não segmentos Whisper de ~24 s).
- Metade retida: acurácia 96,3 %, precisão 0,96, recall 0,90, 98,2 % ponderado por duração.
- Impressão de voz com só 3 turnos limpos rendeu igual; limiares ótimos ≈ 0,34–0,39;
  unidades < 0,8 s rotuladas pelo vizinho mais próximo chegaram a 86 %; suavizar piorou.
- Velocidade: 265 s o episódio inteiro (8 threads), contra 9547 s do pyannote.

## Validação real (implementação final, cópia do SQLite, dados reais só leitura)
Perfil do episódio wk7PY31TQBc (diarização confirmada): voiceprint de 40 turnos limpos
(222,7 s). Limiar calibrado nas unidades de seleção do próprio episódio (domínio onde é
aplicado): <= 400 unidades por classe, espalhadas, com >= 80 % de cobertura de uma voz
pyannote e fora dos recortes do voiceprint; plateau de melhor balanced accuracy (0,986),
limiar 0,4395 (limites de sanidade [0,25; 0,60]). A calibração por turnos limpos dava 0,465.
Construção do perfil: 94,7 s (síncrona em `POST /voice/profile`).

(a) wk7PY31TQBc, 3817 unidades, 618 s ambíguos fora; match em 199-243 s (9906 s de áudio):
- Limiar novo 0,4395: acurácia ponderada 98,22 %, precisão 0,979, recall 0,962
  (antigo 0,465: 98,24 %, 0,983, 0,958; praticamente igual).
- Retido: voiceprint + calibração só com a 1a metade, avaliado na 2a metade, limiar 0,4053:
  acurácia 97,63 %, precisão 0,964, recall 0,956 (0,465: 97,75 %, 0,978, 0,945;
  0,37: 97,46 %, 0,955, 0,959). Os limiares não mudam o resultado de forma material.
- Dayon = 26,4 % da fala.

(b) 7b1f64527d1f48acb36bb24f458f0f60 (sem verdade de referência), 166 s (6876 s de áudio):
- Dayon = 69,4 % da fala, 307 turnos; 1882 unidades D e 740 O.
- D (5 amostras): "Então já começa por aí. Aí vem outro mistério também,"; "a gente é a
  geração mais envenenada da história."; "ficavam com o nariz vermelho, tipo o Rupert,";
  "é conhecimento codificado que ficou ali memorizado."; "que elas comiam o alimento de
  Deus e Jesus falava,".
- O (5 amostras): publicidade e fala do apresentador/outras vozes ("pela parceria. Uma
  marca que eu adoro...", "acesse e entre em contato conosco pelo Instagram.", "Porra,
  velho. Que sensação de aproveitar a câmera até mim.", "você entende que existiu
  influência divina ou espiritual ou", "marca que sou fã, e me veste aqui no programa,").
  O significa qualquer voz que não é a do protagonista; falas de apresentador em O estão
  corretas. Não apareceu fala explicativa do protagonista em O nas amostras.

## Comportamento
- Sem perfil ou com falha do runner: a seleção segue sem rótulos e o run registra
  `brief.voice_match = {status: skipped|failed, reason}`; nunca há fallback para pyannote.
- Confirmação manual (`PUT /episodes/{id}/voice`) sempre vence: o worker só roda o match
  quando o brief não tem `confirmed_voice`, e só preenche `subject_reference` se vazio.
