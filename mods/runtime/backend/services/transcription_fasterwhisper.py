"""faster-whisper engine adapter — GPU (CUDA int8) transcription.

Runs the heavy model in a SEPARATE venv (the one that ships faster_whisper +
CUDA libs) via subprocess, so the podcli runtime python stays untouched. The
worker (fasterwhisper_worker.py) emits JSON in the transcribe_file() shape.

CTranslate2 dlopen()s libcublas/libcudnn at runtime, so we must inject the venv's
bundled nvidia/*/lib dirs into LD_LIBRARY_PATH before spawning — mirroring the
user's run_transcribe.sh. Setting it inside the worker is too late (the loader
reads LD_LIBRARY_PATH at exec time).
"""
import glob
import json
import os
import shutil
import subprocess
import tempfile
from typing import Optional

_DEFAULT_VENV_PYTHON = "/var/home/dx/Projects/transcription/venv/bin/python"


def _venv_python() -> str:
    return os.environ.get("PODCLI_FASTERWHISPER_PYTHON") or _DEFAULT_VENV_PYTHON


def _worker_script() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "fasterwhisper_worker.py")


def _cuda_ld_library_path(venv_python: str) -> str:
    """Collect the venv's bundled nvidia/*/lib dirs (libcublas, libcudnn, ...)."""
    venv_root = os.path.dirname(os.path.dirname(venv_python))
    dirs = glob.glob(os.path.join(venv_root, "lib*", "python*", "site-packages", "nvidia", "*", "lib"))
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    return ":".join([*dirs, existing]) if existing else ":".join(dirs)


def is_ready() -> bool:
    return os.path.exists(_venv_python()) and os.path.exists(_worker_script())


def _extract_wav(media_path: str, wav_path: str, ffmpeg: str = "ffmpeg") -> None:
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", media_path,
         "-ar", "16000", "-ac", "1", wav_path],
        check=True,
        timeout=1800,
    )


def transcribe_file(
    file_path: str,
    model: Optional[str] = None,
    language: Optional[str] = None,
    ffmpeg: str = "ffmpeg",
    device: Optional[str] = None,
    compute_type: Optional[str] = None,
    vad: bool = True,
    **_ignored,
) -> dict:
    py = _venv_python()
    if not os.path.exists(py):
        raise FileNotFoundError(f"faster-whisper venv python not found: {py}")

    tmpdir = tempfile.mkdtemp(prefix="fwsp_")
    wav = os.path.join(tmpdir, "audio.wav")
    try:
        _extract_wav(file_path, wav, ffmpeg)

        cmd = [
            py, _worker_script(), wav,
            "--model", model or os.environ.get("PODCLI_FASTERWHISPER_MODEL", "large-v3"),
            "--device", device or os.environ.get("PODCLI_FASTERWHISPER_DEVICE", "cuda"),
            "--compute-type", compute_type or os.environ.get("PODCLI_FASTERWHISPER_COMPUTE", "int8"),
            "--batch-size", os.environ.get("PODCLI_FASTERWHISPER_BATCH", "8"),
        ]
        if language:
            cmd += ["--language", language]
        if not vad:
            cmd += ["--no-vad"]
        prompt = os.environ.get("PODCLI_FASTERWHISPER_PROMPT")
        if prompt:
            cmd += ["--initial-prompt", prompt]

        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = _cuda_ld_library_path(py)

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200, env=env)
        if proc.returncode != 0:
            raise RuntimeError(
                f"faster-whisper worker failed (rc={proc.returncode}): {proc.stderr.strip()[-600:]}"
            )
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
