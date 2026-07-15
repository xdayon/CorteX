from pathlib import Path

import pytest

from cortex.transcribe.cuda_env import apply_cuda_env, bootstrap_cuda_env


def _cuda_venv(tmp_path: Path) -> tuple[Path, Path]:
    venv = tmp_path / ".venv"
    lib_dir = venv / "lib" / "python3.14" / "site-packages" / "nvidia" / "cublas" / "lib"
    lib_dir.mkdir(parents=True)
    return venv, lib_dir


def test_apply_cuda_env_preserves_existing_path(tmp_path: Path):
    venv, lib_dir = _cuda_venv(tmp_path)
    environ = {"LD_LIBRARY_PATH": "/host/lib"}

    assert apply_cuda_env(venv, environ) is True
    assert environ["LD_LIBRARY_PATH"] == f"{lib_dir}:/host/lib"


def test_bootstrap_reexecs_worker_with_cuda_env(tmp_path: Path):
    venv, lib_dir = _cuda_venv(tmp_path)
    captured = {}

    def fake_execve(executable, argv, environ):
        captured.update(executable=executable, argv=argv, environ=environ)
        raise RuntimeError("reexec")

    with pytest.raises(RuntimeError, match="reexec"):
        bootstrap_cuda_env(
            ["--max-jobs", "1"],
            venv_dir=venv,
            environ={"LD_LIBRARY_PATH": "/host/lib"},
            executable="/venv/python",
            execve=fake_execve,
        )

    assert captured["executable"] == "/venv/python"
    assert captured["argv"] == ["/venv/python", "-m", "cortex.worker", "--max-jobs", "1"]
    assert captured["environ"]["LD_LIBRARY_PATH"] == f"{lib_dir}:/host/lib"
    assert captured["environ"]["CORTEX_CUDA_ENV_BOOTSTRAPPED"] == "1"


def test_bootstrap_does_not_loop_or_reexec_without_cuda_libs(tmp_path: Path):
    def unexpected_execve(*_args):
        raise AssertionError("execve não deveria ser chamado")

    assert bootstrap_cuda_env(
        [], venv_dir=tmp_path, environ={}, execve=unexpected_execve,
    ) is False

    venv, _ = _cuda_venv(tmp_path)
    assert bootstrap_cuda_env(
        [],
        venv_dir=venv,
        environ={"CORTEX_CUDA_ENV_BOOTSTRAPPED": "1"},
        execve=unexpected_execve,
    ) is False
