from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable


_BOOTSTRAP_FLAG = "CORTEX_CUDA_ENV_BOOTSTRAPPED"


def find_cuda_library_dirs(venv_dir: Path) -> list[Path]:
    """Locate the NVIDIA wheel lib dirs bundled in the venv's site-packages.

    Mirrors scripts/cuda_env.sh: CTranslate2 loads cuBLAS/cuDNN via dlopen,
    so those directories must be on LD_LIBRARY_PATH before Python starts.
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


def bootstrap_cuda_env(
    argv: list[str],
    *,
    venv_dir: Path | None = None,
    environ: dict[str, str] | None = None,
    executable: str | None = None,
    execve: Callable[[str, list[str], dict[str, str]], None] = os.execve,
) -> bool:
    """Re-exec the worker once when venv CUDA libraries are not loader-visible."""
    source_env = os.environ if environ is None else environ
    if source_env.get(_BOOTSTRAP_FLAG) == "1":
        return False

    resolved_venv = Path(sys.prefix) if venv_dir is None else venv_dir
    lib_dirs = find_cuda_library_dirs(resolved_venv)
    if not lib_dirs:
        return False

    current_paths = source_env.get("LD_LIBRARY_PATH", "").split(":")
    if all(str(path) in current_paths for path in lib_dirs):
        return False

    next_env = dict(source_env)
    apply_cuda_env(resolved_venv, next_env)
    next_env[_BOOTSTRAP_FLAG] = "1"
    python = executable or sys.executable
    execve(python, [python, "-m", "cortex.worker", *argv], next_env)
    return True
