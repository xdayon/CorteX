"""Origin-side check of Cloudflare Access for the remote (tunnel) entry point.

Cloudflare Access already blocks unauthorized users at the edge; validating its
signed JWT here as well means a misconfigured hostname or Access policy can never
expose the Studio. Requests addressed to the loopback host (the PC itself) are not
affected. Requests that arrive through the tunnel (public hostname or any
Cloudflare edge header) must carry a valid ``Cf-Access-Jwt-Assertion``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import jwt
from starlette.requests import Request
from starlette.responses import JSONResponse

from cortex.config import RemoteAccessConfig

ACCESS_JWT_HEADER = "cf-access-jwt-assertion"
_EDGE_HEADERS = ("cf-ray", "cf-connecting-ip")


class AccessDenied(Exception):
    pass


def _host(request: Request) -> str:
    return (request.headers.get("host") or "").split(":", 1)[0].strip().casefold()


def requires_access_token(request: Request, config: RemoteAccessConfig) -> bool:
    """Tunnel traffic is identified by the public hostname or Cloudflare edge
    headers; plain loopback requests from the PC are local use."""
    if _host(request) == config.public_hostname.casefold():
        return True
    return any(request.headers.get(name) for name in _EDGE_HEADERS)


class AccessTokenVerifier:
    def __init__(
        self, config: RemoteAccessConfig,
        jwks_client_factory: Callable[[str], Any] = lambda url: jwt.PyJWKClient(
            url, cache_keys=True, lifespan=3600, timeout=10,
        ),
    ) -> None:
        self._config = config
        self._issuer = f"https://{config.team_domain}"
        self._jwks = jwks_client_factory(f"{self._issuer}/cdn-cgi/access/certs")

    def verify(self, token: str | None) -> dict[str, Any]:
        if not token:
            raise AccessDenied("token do Cloudflare Access ausente")
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key, algorithms=["RS256"],
                audience=self._config.audience, issuer=self._issuer,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AccessDenied(f"token do Cloudflare Access inválido: {type(exc).__name__}") from exc
        allowed = {email.casefold() for email in self._config.allowed_emails}
        email = str(claims.get("email") or "").casefold()
        if allowed and email not in allowed:
            raise AccessDenied("e-mail não autorizado para o CorteX")
        return claims


class CloudflareAccessMiddleware:
    """Pure ASGI (no BaseHTTPMiddleware) so SSE job streams and ranged video
    responses pass through untouched once the request is authorized."""

    def __init__(self, app, *, config: RemoteAccessConfig, verifier: AccessTokenVerifier) -> None:
        self._app = app
        self._config = config
        self._verifier = verifier

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            request = Request(scope)
            if requires_access_token(request, self._config):
                try:
                    # Key fetches are network I/O; keep them off the event loop.
                    await asyncio.to_thread(
                        self._verifier.verify, request.headers.get(ACCESS_JWT_HEADER),
                    )
                except AccessDenied as exc:
                    response = JSONResponse({"detail": f"Acesso negado: {exc}"}, status_code=403)
                    await response(scope, receive, send)
                    return
        await self._app(scope, receive, send)


def install_remote_access(app, config: RemoteAccessConfig, **verifier_kwargs) -> None:
    if not config.enabled:
        return
    app.add_middleware(
        CloudflareAccessMiddleware, config=config,
        verifier=AccessTokenVerifier(config, **verifier_kwargs),
    )
