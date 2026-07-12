from __future__ import annotations

import os
from pathlib import Path


def find_cuda_library_dirs(venv_dir: Path) -> list[Path]:
    """Locate the NVIDIA wheel lib dirs bundled in the venv's site-packages.

    Mirrors scripts/cuda_env.sh: CTranslate2 loads cuBLAS/cuDNN via dlopen,
    so those directories must be on LD_LIBRARY_PATH before CTranslate2 is
    imported. This is the Python-side fallback for processes started without
    going through scripts/worker.sh (which already sets it via the shell).
    """
    if not venv_dir.exists():
        return []
    return sorted(
        path
        for path in venv_dir.glob("lib*/python*/site-packages/nvidia/*/lib")
        if path.is_dir()
    )


def apply_cuda_env(venv_dir: Path, environ: dict[str, str] | None = None) -> bool:
    """Prepend the venv's NVIDIA lib dirs to LD_LIBRARY_PATH in `environ`.

    Returns True if any directories were found and applied. Safe to call
    even when no CUDA libraries are present (e.g. CPU-only host).
    """
    environ = os.environ if environ is None else environ
    lib_dirs = find_cuda_library_dirs(venv_dir)
    if not lib_dirs:
        return False
    joined = ":".join(str(path) for path in lib_dirs)
    existing = environ.get("LD_LIBRARY_PATH")
    environ["LD_LIBRARY_PATH"] = f"{joined}:{existing}" if existing else joined
    return True
