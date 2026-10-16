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
Perfil construído do episódio wk7PY31TQBc com a diarização confirmada: 40 turnos limpos
positivos (222,7 s) e 40 negativos; limiar calibrado (balanced accuracy, positivos em
leave-one-out) = 0,465; construção em 21,6 s.

(a) wk7PY31TQBc, 3817 unidades de seleção, 199,6 s de relógio (9906 s de áudio):
- Contra os turnos pyannote (unidades com >= 80 % de cobertura de uma voz; 618 s ambíguos
  fora): acurácia ponderada por duração 98,24 %, precisão 0,983, recall 0,958.
- Dayon = 26,4 % da fala, 265 turnos. Observação: o perfil usa turnos desse mesmo episódio,
  então este número não é retido; o retido está na pesquisa acima.

(b) 7b1f64527d1f48acb36bb24f458f0f60 (sem verdade de referência), 137,3 s (6876 s de áudio):
- Dayon = 68,9 % da fala, 321 turnos; 1868 unidades D e 754 O.
- Amostra de 10 linhas por rótulo (aleatória): as D são coerentes com fala explicativa do
  Dayon; entre as O há frases de apresentador ("Seus contatos pra quem quiser te
  acompanhar?", "E vamos em frente para muito mais"), então este episódio precisa de
  conferência humana antes de confiar no rótulo (pode haver outro apresentador ou
  limiar alto demais para esta gravação).

## Comportamento
- Sem perfil ou com falha do runner: a seleção segue sem rótulos e o run registra
  `brief.voice_match = {status: skipped|failed, reason}`; nunca há fallback para pyannote.
- Confirmação manual (`PUT /episodes/{id}/voice`) sempre vence: o worker só roda o match
  quando o brief não tem `confirmed_voice`, e só preenche `subject_reference` se vazio.
