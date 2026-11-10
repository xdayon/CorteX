import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/install_local_service.py"
spec = importlib.util.spec_from_file_location("install_local_service", SCRIPT)
assert spec and spec.loader
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


def test_unit_treats_sigterm_as_a_clean_stop(tmp_path):
    # studio.sh exits 143 when the app window closes and systemd stops it; without this the
    # unit is left "failed" after every normal close.
    text = install.service_text(tmp_path, [])
    assert "SuccessExitStatus=143" in text
    assert f"ExecStart=/usr/bin/bash \"{tmp_path / 'scripts/studio.sh'}\"" in text


def test_unit_quotes_paths_with_percent_and_quotes():
    assert install.unit_quote('/a%b/"c"') == '"/a%%b/\\"c\\""'
