# Operação local

O CorteX roda só no PC (GPU, mídia e banco ficam lá), em `http://127.0.0.1:8787`.
Acesso remoto (Cloudflare Tunnel/Access) foi removido em 10/nov/2026; o celular só baixa
os cortes do Google Drive.

```bash
cd apps/web && npm ci && npm run build && cd ../..
.venv/bin/python scripts/install_local_service.py --install
```

Abra o app **CorteX** pelo atalho: `scripts/open_studio.py` inicia o
`cortex.service` (API + worker, `scripts/studio.sh`) e abre a janela; ao fechar a
janela o serviço é parado (se houver job na fila ou rodando, espera terminar e avisa
por notificação). O serviço não sobe no login. O unit usa o alias estável do fnm no PATH e lê `.env` via systemd. Para atualizar com segurança:
`./scripts/update_and_verify.sh`. Não rode `dev.sh` junto com o serviço.

## Google Drive

Cada corte exportado sobe sozinho (`drive.*` no config) para
`CorteX/<episódio>/<AAAA-MM-DD>/<NN - manchete>.mp4` e fica marcado como Salvo.
Uma vez: `brew install rclone` e `rclone config create gdrive drive scope=drive.file`
(login no navegador). Conferir: `.venv/bin/python -m cortex.drive --testar`.
Reenviar exports antigos: `.venv/bin/python -m cortex.drive --desde AAAA-MM-DD`.
