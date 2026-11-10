"""The never-break rules enforced as Claude Code hooks (.claude/settings.json, scripts/hooks/)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "scripts/hooks"
pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="hooks need jq")


def _run(hook: str, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run([str(HOOKS / hook)], input=json.dumps(payload), text=True,
                          capture_output=True, timeout=30, cwd=ROOT)


@pytest.mark.parametrize("expected,command", [
    (0, "python - <<'PY'\ns = \"(cd apps/web && npm run build)\"\nPY\necho done"),
    (2, "cat <<EOF > x\nhello\nEOF\nnpm run build"),
    (2, "cd apps/web && npm run build"),
    (0, 'grep -n "npm run build" AGENTS.md README.md'),
    (2, "(cd apps/web && npm test && npm run build)"),
    (0, "cat > notes.md <<'EOF'\nnunca rode cat .env nem rm -rf data/\nEOF"),
    (2, "cat .env"),
    (2, "grep -c X /var/home/dx/Projects/CorteX/.env"),
    (2, "set -a; source .env; set +a"),
    (0, "cat .env.example"),
    (2, "git add -f .env"),
    (2, "git add data/cortex.sqlite3"),
    (0, "git add src tests docs/evidence/x.md"),
    (2, "git reset --hard"),
    (2, "git push --force origin main"),
    (0, "git push origin main"),
    (2, "rm -rf data/projects"),
    (2, "rm -rf ./data"),
    (2, "cd /tmp && rm -rf /var/home/dx/Projects/CorteX/data/projects"),
    (2, "find data/cache -name '*.tmp' -delete"),
    (0, "rm -rf /var/home/dx/.cache/cortex-visual/data"),
    (2, "rm -r entrada/polymath"),
    (0, "rm -rf apps/web/dist.next /tmp/x"),
    (2, "systemctl --user restart cortex.service"),
    (0, "systemctl --user is-active cortex.service"),
    (2, "npx vite build"),
    (0, "npx vite build --outDir /tmp/x --emptyOutDir"),
    (2, "git stash"),
    (0, "git stash push -u -m tag"),
])
def test_guard_bash(expected: int, command: str) -> None:
    result = _run("guard_bash.sh", {"tool_input": {"command": command}})
    assert result.returncode == expected, result.stderr


# Fake credentials built at runtime so this file never contains a literal token.
HF = "hf_" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zZa"
AWS = "AKIA" + "QYLPMN5HHHFPZAM2"


@pytest.mark.parametrize("expected,path,field,text", [
    (0, "src/a.py", "content", "print(1)"),
    (2, "src/a.py", "content", f"HF_TOKEN={HF}"),
    (2, "docs/a.md", "new_string", f"token {HF} here"),
    (2, "src/b.py", "content", f'key = "{AWS}"'),
    (0, ".env.example", "content", "HF_TOKEN=<seu-token>\nSOUNDZA_API_KEY="),
    (2, ".env", "content", "A=1"),
    (0, "src/c.py", "content", "api_key = config.sound.api_key"),
])
def test_secret_scan(expected: int, path: str, field: str, text: str) -> None:
    result = _run("secret_scan.sh", {"tool_input": {"file_path": str(ROOT / path), field: text}})
    assert result.returncode == expected, result.stderr


def test_settings_wire_every_hook() -> None:
    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    commands = [hook["command"] for groups in settings["hooks"].values()
                for group in groups for hook in group["hooks"]]
    for script in ("secret_scan.sh", "guard_bash.sh", "tests_before_done.sh"):
        assert any(script in command for command in commands)
        assert (HOOKS / script).stat().st_mode & 0o111
