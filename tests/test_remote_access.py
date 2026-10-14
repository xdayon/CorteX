from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import cortex.remote_access as remote_access
from cortex.api import create_app
from cortex.config import RemoteAccessConfig

from test_suggestion_cli import _config

TEAM = "time.cloudflareaccess.com"
AUD = "aud-tag-123"
HOST = "cortex.example.com"
EMAIL = "dono@example.com"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeJwks:
    def __init__(self, url: str) -> None:
        assert url == f"https://{TEAM}/cdn-cgi/access/certs"

    def get_signing_key_from_jwt(self, token: str):
        return SimpleNamespace(key=_KEY.public_key())


def _token(*, key=_KEY, aud=AUD, iss=f"https://{TEAM}", email=EMAIL, exp_delta=600) -> str:
    now = int(time.time())
    return jwt.encode(
        {"aud": [aud], "iss": iss, "email": email, "iat": now, "exp": now + exp_delta},
        key, algorithm="RS256", headers={"kid": "k1"},
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(remote_access.jwt, "PyJWKClient", lambda url, **_kwargs: _FakeJwks(url))
    config = _config(tmp_path)
    config = config.model_copy(update={"remote_access": RemoteAccessConfig(
        enabled=True, public_hostname=HOST, team_domain=TEAM, audience=AUD,
        allowed_emails=[EMAIL],
    )})
    return TestClient(create_app(config))


def _get(client: TestClient, *, host: str = HOST, token: str | None = None, **headers):
    if token is not None:
        headers["cf-access-jwt-assertion"] = token
    return client.get("/api/v1/health", headers={"host": host, **headers})


def test_local_requests_do_not_need_a_token(client) -> None:
    assert _get(client, host="127.0.0.1:8787").status_code == 200


def test_tunnel_requests_without_token_are_denied(client) -> None:
    response = _get(client)
    assert response.status_code == 403
    assert "Acesso negado" in response.json()["detail"]


def test_edge_headers_require_a_token_even_on_loopback_host(client) -> None:
    assert _get(client, host="127.0.0.1:8787", **{"cf-ray": "abc"}).status_code == 403


def test_valid_access_token_is_accepted(client) -> None:
    assert _get(client, token=_token()).status_code == 200


@pytest.mark.parametrize("token_kwargs", [
    {"aud": "outra-aplicacao"},
    {"iss": "https://outro.cloudflareaccess.com"},
    {"exp_delta": -60},
    {"key": _OTHER_KEY},
    {"email": "intruso@example.com"},
])
def test_invalid_access_tokens_are_denied(client, token_kwargs) -> None:
    assert _get(client, token=_token(**token_kwargs)).status_code == 403


def test_enabled_remote_access_requires_complete_identifiers() -> None:
    with pytest.raises(ValueError, match="exige public_hostname, team_domain e audience"):
        RemoteAccessConfig(enabled=True, public_hostname=HOST, team_domain=TEAM)


def test_disabled_remote_access_installs_nothing(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    assert client.get("/api/v1/health", headers={"host": HOST, "cf-ray": "x"}).status_code == 200


def _setup_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "setup_remote_access", Path(__file__).resolve().parents[1] / "scripts/setup_remote_access.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_preserves_env_lines_and_updates_keys(tmp_path: Path, monkeypatch) -> None:
    setup = _setup_module()
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "HF_TOKEN=segredo\nCORTEX__REMOTE_ACCESS__AUDIENCE=antigo\n", encoding="utf-8",
    )
    setup._update_env({
        "CORTEX__REMOTE_ACCESS__AUDIENCE": "novo",
        "CORTEX__REMOTE_ACCESS__ENABLED": "true",
    })
    lines = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert lines == [
        "HF_TOKEN=segredo",
        "CORTEX__REMOTE_ACCESS__AUDIENCE=novo",
        "CORTEX__REMOTE_ACCESS__ENABLED=true",
    ]
    assert (tmp_path / ".env").stat().st_mode & 0o077 == 0


def test_setup_extracts_token_from_dashboard_command() -> None:
    setup = _setup_module()
    token = "eyJhIjoiYWJjIiwidCI6IjEyMyIsInMiOiJ4eXoifQ" + "A" * 20
    assert setup._TOKEN_RE.search(f"sudo cloudflared service install {token}").group(1) == token
    assert setup._TOKEN_RE.search(token).group(1) == token
    assert setup._TOKEN_RE.search("sem token aqui") is None
