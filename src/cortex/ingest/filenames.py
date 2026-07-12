from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from uuid import uuid4

_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str) -> str:
    """Strip directory components and unsafe characters from a user filename.

    Guards against path traversal (`../`, absolute paths, NUL bytes) and
    normalizes exotic whitespace/unicode into a plain ASCII-ish filename.
    """
    name = name.replace("\\", "/").split("\x00")[0]
    base = PurePosixPath(name).name or "file"
    base = _UNSAFE_RE.sub("_", base).strip("._")
    return base or "file"


def unique_destination(directory: Path, filename: str) -> Path:
    """Return a destination path under `directory`, avoiding collisions.

    If `filename` already exists in `directory`, prefix it with a short
    random hex tag so concurrent uploads never clobber each other.
    """
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, _, suffix = filename.rpartition(".")
    tag = uuid4().hex[:8]
    if stem:
        return directory / f"{tag}-{stem}.{suffix}"
    return directory / f"{tag}-{filename}"
