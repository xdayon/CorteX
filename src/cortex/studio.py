"""Serve the built Studio on the API origin without a development server."""
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class StudioFiles(StaticFiles):
    """Static files plus an SPA fallback: client-side routes serve index.html."""

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or not self._is_client_route(scope):
                raise
            return await super().get_response("index.html", scope)

    @staticmethod
    def _is_client_route(scope: Scope) -> bool:
        url_path = scope.get("path", "")
        return (
            scope.get("method") in {"GET", "HEAD"}
            and not url_path.startswith("/api/")
            and "." not in url_path.rstrip("/").rsplit("/", 1)[-1]
        )


def mount_studio(app: FastAPI, directory: Path) -> None:
    directory = directory.resolve()
    if not (directory / "index.html").is_file():
        raise ValueError("Studio não compilado: execute cd apps/web && npm ci && npm run build")
    # Mount last: API routes keep their normal JSON/404 responses. Only the build
    # directory is exposed; StaticFiles rejects traversal and escaping symlinks.
    app.mount("/", StudioFiles(directory=directory, html=True), name="studio")
