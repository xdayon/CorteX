from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from cortex.config import AiConfig
from cortex.suggest.provider import ClaudeCodeCliProvider, SuggestionProviderError

_FAKE_CLAUDE_SOURCE = '''#!/usr/bin/env python3
import json
import os
import sys

record_path = os.environ.get("FAKE_CLAUDE_RECORD")
if sys.argv[1:] == ["--version"]:
    print("2.1.283 (Claude Code)")
    sys.exit(0)

if record_path:
    with open(record_path, "w", encoding="utf-8") as handle:
        json.dump({
            "argv": sys.argv[1:],
            "has_api_key": "ANTHROPIC_API_KEY" in os.environ,
            "prompt": sys.stdin.read(),
        }, handle)
else:
    sys.stdin.read()

mode = os.environ.get("FAKE_CLAUDE_MODE", "success")
if mode == "success":
    print(json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "api_error_status": None, "result": "{\\"n\\":2}",
        "structured_output": {"n": 2},
        "usage": {
            "input_tokens": 2, "cache_creation_input_tokens": 1152,
            "cache_read_input_tokens": 0, "output_tokens": 51,
        },
        "modelUsage": {"claude-sonnet-5": {"inputTokens": 2}},
        "duration_ms": 1338,
    }))
elif mode == "is_error":
    print(json.dumps({
        "type": "result", "subtype": "error_during_execution", "is_error": True,
        "result": "algo deu errado ao gerar a resposta",
    }))
elif mode == "rate_limit":
    print(json.dumps({
        "type": "result", "subtype": "error_during_execution", "is_error": True,
        "result": "Claude AI usage limit reached|1234567890",
    }))
elif mode == "nonjson":
    print("isto nao e json")
sys.exit(0)
'''


def _fake_claude(tmp_path: Path, *, mode: str, record: bool = True) -> tuple[Path, Path | None]:
    script = tmp_path / "claude"
    script.write_text(_FAKE_CLAUDE_SOURCE, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    record_path = tmp_path / "record.json" if record else None
    os.environ["FAKE_CLAUDE_MODE"] = mode
    if record_path is not None:
        os.environ["FAKE_CLAUDE_RECORD"] = str(record_path)
    else:
        os.environ.pop("FAKE_CLAUDE_RECORD", None)
    return script, record_path


def _config(binary: Path) -> AiConfig:
    return AiConfig(
        provider="claude_code_cli",
        claude_binary=str(binary),
        claude_model="sonnet",
        claude_effort="low",
        timeout_seconds=30,
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE_MODE", raising=False)
    monkeypatch.delenv("FAKE_CLAUDE_RECORD", raising=False)
    yield
    os.environ.pop("FAKE_CLAUDE_MODE", None)
    os.environ.pop("FAKE_CLAUDE_RECORD", None)


def test_success_parses_document_and_provenance_usage(tmp_path):
    binary, record_path = _fake_claude(tmp_path, mode="success")
    provider = ClaudeCodeCliProvider(_config(binary))

    result = provider.generate("gere a seleção", {"type": "object"}, should_cancel=lambda: False)

    assert result.document == {"n": 2}
    assert result.provenance["provider"] == "claude_code_cli"
    assert result.provenance["model"] == "claude-sonnet-5"
    assert result.provenance["effort"] == "low"
    assert result.provenance["auth_mode"] == "claude_subscription_cli"
    assert result.provenance["usage"] == {
        "input_tokens": 2, "cache_creation_input_tokens": 1152,
        "cache_read_input_tokens": 0, "output_tokens": 51,
    }

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert "--bare" not in record["argv"]
    assert "--tools" in record["argv"]
    tools_index = record["argv"].index("--tools")
    assert record["argv"][tools_index + 1] == ""
    assert record["has_api_key"] is False


def test_is_error_raises_with_bounded_detail(tmp_path):
    binary, _record_path = _fake_claude(tmp_path, mode="is_error")
    provider = ClaudeCodeCliProvider(_config(binary))

    with pytest.raises(SuggestionProviderError, match="algo deu errado"):
        provider.generate("gere a seleção", {"type": "object"}, should_cancel=lambda: False)


def test_usage_limit_message_is_pt_br(tmp_path):
    binary, _record_path = _fake_claude(tmp_path, mode="rate_limit")
    provider = ClaudeCodeCliProvider(_config(binary))

    with pytest.raises(SuggestionProviderError, match="Limite de uso da assinatura Claude"):
        provider.generate("gere a seleção", {"type": "object"}, should_cancel=lambda: False)


def test_non_json_stdout_raises_explicit_error(tmp_path):
    binary, _record_path = _fake_claude(tmp_path, mode="nonjson")
    provider = ClaudeCodeCliProvider(_config(binary))

    with pytest.raises(SuggestionProviderError, match="não é JSON válido"):
        provider.generate("gere a seleção", {"type": "object"}, should_cancel=lambda: False)


def test_anthropic_api_key_is_stripped_from_child_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-reach-child")
    binary, record_path = _fake_claude(tmp_path, mode="success")
    provider = ClaudeCodeCliProvider(_config(binary))

    provider.generate("gere a seleção", {"type": "object"}, should_cancel=lambda: False)

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["has_api_key"] is False


def test_missing_binary_raises_explicit_error(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty-home")
    config = _config(Path("/does/not/exist/claude"))
    provider = ClaudeCodeCliProvider(config)

    with pytest.raises(SuggestionProviderError, match="não está funcionando"):
        provider.generate("gere a seleção", {"type": "object"}, should_cancel=lambda: False)


_BROKEN_STUB = """#!/bin/sh
echo "Error: claude native binary not installed." >&2
exit 1
"""


def test_broken_configured_stub_resolves_to_working_binary(tmp_path, monkeypatch):
    home = tmp_path / "home"
    working_dir = home / ".local/share/fnm/aliases/default/bin"
    working_dir.mkdir(parents=True)
    working, record_path = _fake_claude(working_dir, mode="success")
    stub = tmp_path / "stub-claude"
    stub.write_text(_BROKEN_STUB, encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setattr(Path, "home", lambda: home)

    result = ClaudeCodeCliProvider(_config(stub)).generate(
        "gere a seleção", {"type": "object"}, should_cancel=lambda: False,
    )

    assert result.provenance["binary_resolution"] == "fnm_default"
    assert result.provenance["binary_version"] == "2.1.283 (Claude Code)"
    assert json.loads(record_path.read_text(encoding="utf-8"))["prompt"] == "gere a seleção"


def test_system_context_goes_to_cacheable_system_prompt_file(tmp_path):
    binary, record_path = _fake_claude(tmp_path, mode="success")
    ClaudeCodeCliProvider(_config(binary)).generate(
        "pedido curto", {"type": "object"}, should_cancel=lambda: False,
        system_context="## Transcrição\n#0 [0.0|2.0] olá",
    )

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["prompt"] == "pedido curto"
    assert "--system-prompt-file" in record["argv"]
    assert "--system-prompt" not in record["argv"]


def test_effort_override_applies_to_one_provider(tmp_path):
    binary, record_path = _fake_claude(tmp_path, mode="success")
    result = ClaudeCodeCliProvider(_config(binary), effort="high").generate(
        "x", {"type": "object"}, should_cancel=lambda: False,
    )

    argv = json.loads(record_path.read_text(encoding="utf-8"))["argv"]
    assert argv[argv.index("--effort") + 1] == "high"
    assert result.provenance["effort"] == "high"
