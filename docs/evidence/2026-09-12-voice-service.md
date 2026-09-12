# Correção do ambiente de voz no serviço

O botão falhava antes de executar Pyannote porque `runtime_path()` chamava
`Path.resolve()` em `.venv-diarization/bin/python`. Esse executável é um symlink
para o Python base do uv. Executar o destino resolve fora do venv: o probe de
`importlib.metadata` retornava `PackageNotFoundError`. O teste anterior de 90s
chamava o runner diretamente pelo venv e não exercitava esse caminho do serviço.

## Alterações

- Preservar o symlink do executável com caminho absoluto, incluindo override por
  `CORTEX_DIARIZATION_PYTHON`; não trocar de Python silenciosamente.
- Prontidão verifica os pacotes no mesmo Python usado pelo worker, antes de
  liberar o botão. API respeita `ready`, incluindo falhas nas dependências.
- Erros de verificação têm orientação e timeout de 10s; não expõem comando e
  traceback internos na interface.
- Falha no job limpa “Em execução” e mostra “Separação de vozes não concluída”.

## Verificação

- Regressão cria um venv real com symlink, instala somente metadados de pacotes
  nele e verifica o Python/prefixo efetivo para caminho padrão e configurado.
- Backend: 486 passaram, 1 opt-in de diarização omitido na suíte geral.
- Frontend: 59 passaram; build web, Ruff e `git diff --check` passaram.
- Opt-in `tests/test_diarization_e2e.py`: passou separadamente em 87,03s.
  Usa os endpoints reais do botão e confirmação, `process_next` e o runner CPU.
  A única fixture de engine é a interface de transcrição, não usada pelo handler
  de diarização; Pyannote, áudio e persistência não são simulados.
- Amostra de 90s produziu 6 turnos/2 vozes em 78,07s de carregamento/inferência.
  GET de amostras e PUT de confirmação funcionaram em banco isolado. Segundo POST
  criou job concluído em cache, retornando o mesmo artefato sem rodar o modelo.
- JSON e prova locais:
  `.cache/voice-service-e2e/test_episode_button_worker_per0/voice-e2e-proof.json`.
  Isso valida execução e contratos, não acurácia da identificação de Dayon.

Serviço `cortex.service` reiniciado após verificar ausência de jobs ativos.
Readiness do serviço em execução confirmou pacotes e credencial disponíveis.
Nenhum episódio, token ou identidade confirmada no banco do usuário foi alterado.
Para aplicar o feedback novo da interface, recarregar o Studio antes de repetir
“Separar vozes em CPU”. O episódio inteiro ainda exige o processamento de suas vozes.
