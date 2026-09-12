from pathlib import Path
import json
import subprocess

from conftest import requires_remotion_e2e
from cortex.config import load_config


@requires_remotion_e2e
def test_real_caption_pages_preserve_words_two_lines_and_preview_geometry(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        'node', str(root / 'apps/remotion/tests/caption-layout.mjs'),
        str(load_config().render.remotion_browser), str(tmp_path),
    ], cwd=root / 'apps/remotion', capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr[-5000:]
    evidence = json.loads((tmp_path / 'caption-layout.json').read_text())
    assert len(evidence) == 18
    assert all(item['count'] <= 2 and item['fits'] for item in evidence)
    assert (tmp_path / 'caption-layout.png').stat().st_size > 1000
