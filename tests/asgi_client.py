from __future__ import annotations

import asyncio
from typing import Any

import httpx
import fastapi.dependencies.utils
import fastapi.routing


async def _run_direct(function: Any, *args: Any, **kwargs: Any) -> Any:
    return function(*args, **kwargs)


class ASGITestClient:
    """Synchronous facade over HTTPX's in-process async ASGI transport.

    Starlette's thread-portal TestClient deadlocks on the project's Python 3.14
    test environment. This adapter keeps the workaround out of production.
    """

    __test__ = False

    def __init__(self, app: Any) -> None:
        self._app = app

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self._app)
            routing_threadpool = fastapi.routing.run_in_threadpool
            dependency_threadpool = fastapi.dependencies.utils.run_in_threadpool
            fastapi.routing.run_in_threadpool = _run_direct
            fastapi.dependencies.utils.run_in_threadpool = _run_direct
            try:
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    return await client.request(method, url, **kwargs)
            finally:
                fastapi.routing.run_in_threadpool = routing_threadpool
                fastapi.dependencies.utils.run_in_threadpool = dependency_threadpool

        return asyncio.run(send())

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)
