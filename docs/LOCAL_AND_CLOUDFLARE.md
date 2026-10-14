# Operação local e acesso remoto (Cloudflare)

O CorteX roda no PC de casa (GPU, mídia e banco ficam lá). O acesso de fora — iPhone
ou notebook — passa por um Cloudflare Tunnel até a API local (`127.0.0.1:8787`),
protegido pelo Cloudflare Access: só o e-mail autorizado entra, com código enviado
por e-mail. O próprio CorteX também valida o crachá (JWT) do Access em toda
requisição que chega pelo túnel, então um erro de configuração no painel não expõe o
Studio. No PC, `http://127.0.0.1:8787` continua funcionando sem login.

Endereço: `https://cortex.synapses.academy` (o e-mail autorizado fica só no painel e no `.env`).

## Operação local

```bash
cd apps/web && npm ci && npm run build && cd ../..
.venv/bin/python scripts/install_local_service.py --install
systemctl --user start cortex.service
```

O serviço `cortex.service` sobe API + worker (`scripts/studio.sh`) no login, com o
alias estável do fnm no PATH, e lê `.env` via systemd. Para atualizar com segurança:
`./scripts/update_and_verify.sh`. Não rode `dev.sh` junto com o serviço.

## Ligar o acesso remoto (uma vez)

Pré-requisito: o domínio `synapses.academy` está na sua conta Cloudflare. O
`cloudflared` já está instalado no PC (`brew install cloudflared`).

1. **Aplicação do Access primeiro** — no painel, Zero Trust → **Access controls →
   Applications** → adicionar aplicação **Self-hosted**:
   - hostname público `cortex.synapses.academy`;
   - política **Allow** incluindo só o seu e-mail (política
     reutilizável); método de login **One-time PIN**; sessão de, por exemplo, 1 mês;
   - salve; em **Configure → Additional settings** copie o **Application Audience
     (AUD) Tag**. Anote também o *team domain* do Zero Trust
     (`<seu-time>.cloudflareaccess.com`).
2. **Túnel** — **Networking → Tunnels → Create a tunnel**, nome `cortex`, sistema
   **Linux**. Copie o comando de instalação exibido (ele contém o token) — não rode
   esse comando. No PC, rode:

   ```bash
   .venv/bin/python scripts/setup_remote_access.py
   ```

   Cole o comando quando pedido (o token não aparece na tela e fica em
   `~/.config/cortex/tunnel.env`, permissão 600), informe team domain, AUD e e-mail.
   O script grava os identificadores no `.env` (não versionado) e ativa o serviço
   `cortex-tunnel.service`. Volte ao painel e espere o túnel ficar conectado.
3. **Rota** — no túnel, aba **Routes → Add route → Published application**:
   subdomínio `cortex`, domínio `synapses.academy`, Service URL
   `http://localhost:8787`.
4. Reinicie o CorteX sem job rodando (`systemctl --user restart cortex.service`) e,
   para tudo subir no boot mesmo sem login, `loginctl enable-linger $USER`.

## Conferir

- No iPhone, abra `https://cortex.synapses.academy`: pede o código por e-mail e
  depois mostra o Studio.
- Numa janela anônima com outro e-mail: bloqueado pela Cloudflare.
- No PC: `systemctl --user status cortex-tunnel.service` ativo; no painel o túnel
  aparece como *Healthy*.

## Limites

- O plano Free da Cloudflare aceita no máximo 100 MB por requisição: fora de casa,
  use links do YouTube; arquivos grandes, envie pelo próprio PC.
- O PC precisa estar ligado e acordado (evite suspender na tomada).
- Revogar o acesso: remova a aplicação/rota no painel ou pare o túnel
  (`systemctl --user disable --now cortex-tunnel.service`).

Referências: [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/),
[validar o JWT do Access](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/),
[limites de upload](https://developers.cloudflare.com/workers/platform/limits/).
