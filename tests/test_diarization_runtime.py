from __future__ import annotations

import subprocess
import venv

import pytest

from cortex.diarize.service import runtime_path


@pytest.mark.parametrize('configured', [False, True])
def test_runtime_preserves_virtualenv_symlink(tmp_path, monkeypatch, configured):
    environment = tmp_path / '.venv-diarization'
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    executable = environment / 'bin/python'
    assert executable.is_symlink()
    monkeypatch.chdir(tmp_path)
    if configured:
        monkeypatch.setenv('CORTEX_DIARIZATION_PYTHON', str(executable))
    else:
        monkeypatch.delenv('CORTEX_DIARIZATION_PYTHON', raising=False)
    actual = runtime_path()
    assert actual == executable and actual != actual.resolve()
    prefix = subprocess.check_output([str(actual), '-c', 'import sys;print(sys.prefix)'], text=True, timeout=10).strip()
    assert prefix == str(environment)
