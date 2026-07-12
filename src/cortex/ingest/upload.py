from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cortex.ingest.filenames import sanitize_filename, unique_destination

_CHUNK_SIZE = 1024 * 1024  # 1 MiB


class UploadValidationError(ValueError):
    pass


class AsyncReadable(Protocol):
    async def read(self, size: int) -> bytes: ...


@dataclass(frozen=True)
class StoredUpload:
    path: Path
    filename: str
    sha256: str
    size_bytes: int


def _extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


async def store_upload_stream(
    file: AsyncReadable,
    original_filename: str,
    *,
    destination_dir: Path,
    allowed_extensions: set[str],
    max_bytes: int,
) -> StoredUpload:
    """Stream `file` to disk in chunks, hashing and size-checking as it goes.

    Never buffers the whole upload in memory. Raises UploadValidationError
    for a rejected extension or a payload over `max_bytes`; the caller is
    responsible for removing any partial file left on disk after such an
    error (this function cleans up after itself on failure).
    """
    extension = _extension_of(original_filename)
    if extension not in allowed_extensions:
        raise UploadValidationError(f"extensão não permitida: .{extension or '?'}")

    destination_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(original_filename)
    if "." not in safe_name:
        safe_name = f"{safe_name}.{extension}"
    destination = unique_destination(destination_dir, safe_name)

    digest = hashlib.sha256()
    size = 0
    try:
        with destination.open("wb") as handle:
            while True:
                chunk = await file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadValidationError(
                        f"upload excede o limite de {max_bytes} bytes"
                    )
                digest.update(chunk)
                handle.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return StoredUpload(
        path=destination,
        filename=destination.name,
        sha256=digest.hexdigest(),
        size_bytes=size,
    )
