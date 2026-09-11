# Separar vozes e confirmar Dayon

O adapter opcional usa Pyannote community-1 em CPU, isolado da transcrição CUDA
em `.venv-diarization`. Não é reconhecimento facial nem clonagem de voz.

1. Instalar com `scripts/setup_diarization.sh` (requer `uv`, Python 3.13 e rede).
   O script instala Torch CPU, torchaudio, torchcodec e pyannote fixados. Não o
   execute sobre um ambiente customizado sem conferir as versões.
2. Aceitar as condições de acesso ao modelo e criar token Hugging Face que permita
   baixá-lo. Consultar [instruções oficiais](https://github.com/pyannote/pyannote-audio).
3. Configurar `HF_TOKEN` somente no ambiente local. O serviço systemd lê `.env`;
   comandos manuais precisam receber a variável. Nunca colocar o token em Git,
   argumentos do processo, screenshots ou logs. `CORTEX_DIARIZATION_PYTHON` permite
   apontar explicitamente para outro ambiente preparado.
4. Na biblioteca, baixar episódio, indicar quantidade de participantes quando
   conhecida, separar vozes, ouvir amostras e confirmar a voz de Dayon.
5. Iniciar uma nova seleção editorial depois da confirmação. Sugestões anteriores
   não passam a ter o filtro retrospectivamente. A referência precisa corresponder
   à fonte e ao artefato de diarização daquele episódio.

A inferência é local, mas o primeiro download exige rede/autorização do modelo.
O status de readiness atual confirma executável/token, não mede precisão nem
prova que os pesos estejam acessíveis. Não foi validado desempenho em podcast
longo nesta revisão. O job usa CPU explicitamente e falhas não viram identificação
visual silenciosa. Detalhes de timeout/model revision/erros ainda estão no roadmap.

A seleção exige maioria temporal de Dayon no envelope e na união dos trechos
editoriais, tratando overlap conservadoramente. `voice_validation` registra as
métricas. Ainda falta revalidação sobre o plano de áudio final e associação humana
voz/rosto. Corrigir falas simultâneas e conferir o resultado antes da publicação.
