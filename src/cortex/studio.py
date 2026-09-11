"""Serve the built Studio on the API origin without a development server."""
from pathlib import Path

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles


def mount_studio(app: FastAPI, directory: Path) -> None:
    directory = directory.resolve()
    if not (directory / "index.html").is_file():
        raise ValueError("Studio não compilado: execute cd apps/web && npm ci && npm run build")
    # Mount last: API routes keep their normal JSON/404 responses. Only the build
    # directory is exposed; StaticFiles rejects traversal and escaping symlinks.
    app.mount("/", StaticFiles(directory=directory, html=True), name="studio")
