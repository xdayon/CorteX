from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from cortex.config import AiConfig

_CLAUDE_SYSTEM_PROMPT = (
    "Você é um editor de cortes de podcast. Responda somente com o JSON do schema."
)
_CLAUDE_STRIPPED_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)


class SuggestionProviderError(RuntimeError):
    pass


class SuggestionProviderCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class SuggestionProviderResult:
    document: dict[str, Any]
    provenance: dict[str, Any]


class SuggestionProvider(Protocol):
    """``system_context`` carries the large, stable part of a request (editorial
    instructions + episode transcript). Providers that support a cacheable system
    prompt send it there so repeated calls on the same episode reuse the cache;
    ``prompt`` is the small per-request part."""

    def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        should_cancel: Callable[[], bool],
        system_context: str | None = None,
    ) -> SuggestionProviderResult: ...


def _binary_version(binary: str) -> str:
    try:
        return subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()[:200] or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


@dataclass(frozen=True)
class ResolvedBinary:
    path: str
    version: str
    source: str  # "configured" or the fallback location that answered


def _working_version(binary: str) -> str | None:
    try:
        completed = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = completed.stdout.strip()
    if completed.returncode != 0 or "claude code" not in output.casefold():
        return None
    return output[:200]


def _claude_binary_candidates(configured: str) -> list[tuple[str, str]]:
    home = Path.home()
    candidates: list[tuple[str, str]] = []
    if "/" in configured:
        candidates.append((configured, "configured"))
    else:
        found = shutil.which(configured)
        if found:
            candidates.append((found, "configured"))
    for path, source in (
        (home / ".local/bin/claude", "native_installer"),
        (home / ".local/share/fnm/aliases/default/bin/claude", "fnm_default"),
    ):
        candidates.append((str(path), source))
    extensions: list[Path] = []
    for root in (".vscode/extensions", ".antigravity/extensions", ".vscode-oss/extensions"):
        extensions.extend((home / root).glob("anthropic.claude-code-*/resources/native-binary/claude"))

    def version_key(path: Path) -> tuple[int, ...]:
        match = re.search(r"claude-code-(\d+(?:\.\d+)*)", str(path))
        return tuple(int(part) for part in match.group(1).split(".")) if match else ()

    for path in sorted(extensions, key=version_key, reverse=True):
        candidates.append((str(path), "editor_extension"))
    seen: set[str] = set()
    unique = []
    for path, source in candidates:
        if path not in seen:
            seen.add(path)
            unique.append((path, source))
    return unique


def resolve_claude_binary(configured: str) -> ResolvedBinary:
    """Find a Claude Code CLI that actually runs.

    npm/fnm upgrades can leave the configured path as a stub that only prints
    "native binary not installed". The resolution order is fixed and the chosen
    path is recorded in provenance; the model, effort and subscription auth never
    change, so this is not an editorial fallback.
    """
    tried = []
    for path, source in _claude_binary_candidates(configured):
        if not Path(path).is_file():
            continue
        version = _working_version(path)
        if version is not None:
            return ResolvedBinary(path=path, version=version, source=source)
        tried.append(path)
    detail = ", ".join(tried) if tried else configured
    raise SuggestionProviderError(
        "Claude Code CLI não está funcionando (testado: " + detail + "). "
        "Reinstale ou repare o Claude Code e tente de novo."
    )


def _stop(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()


def _communicate(
    process: subprocess.Popen[str],
    prompt: str,
    *,
    timeout_seconds: int,
    should_cancel: Callable[[], bool],
    provider_name: str,
) -> tuple[str, str, int]:
    started = time.monotonic()
    pending_input: str | None = prompt
    try:
        while True:
            if should_cancel():
                _stop(process)
                raise SuggestionProviderCancelled("seleção cancelada pelo usuário")
            if time.monotonic() - started >= timeout_seconds:
                _stop(process)
                raise SuggestionProviderError(
                    f"{provider_name} excedeu o timeout de {timeout_seconds}s"
                )
            try:
                stdout, stderr = process.communicate(input=pending_input, timeout=0.5)
                return stdout, stderr, round((time.monotonic() - started) * 1000)
            except subprocess.TimeoutExpired:
                pending_input = None
    finally:
        if process.poll() is None:
            process.kill()


def _codex_error_detail(stderr: str) -> str:
    messages = re.findall(r'"message"\s*:\s*("(?:\\.|[^"\\])*")', stderr)
    if messages:
        try:
            return str(json.loads(messages[-1]))[:1000]
        except json.JSONDecodeError:
            pass
    codes = re.findall(r'"code"\s*:\s*"([^"\\]+)"', stderr)
    if codes:
        return f"erro Codex: {codes[-1][:200]}"
    return "falha do Codex CLI sem detalhe seguro; o prompt não foi persistido"


class CodexCliProvider:
    """Runs Codex in an empty, read-only, ephemeral workspace."""

    name = "codex_cli"

    def __init__(self, config: AiConfig) -> None:
        self._config = config

    def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        should_cancel: Callable[[], bool],
        system_context: str | None = None,
    ) -> SuggestionProviderResult:
        if system_context:
            prompt = f"{system_context}\n\n{prompt}"
        binary = shutil.which(self._config.codex_binary)
        if binary is None:
            raise SuggestionProviderError(
                f"CLI configurada não encontrada no PATH: {self._config.codex_binary}"
            )
        with tempfile.TemporaryDirectory(prefix="cortex-codex-") as directory:
            workdir = Path(directory)
            schema_path = workdir / "output.schema.json"
            output_path = workdir / "selection.json"
            schema_path.write_text(
                json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            command = [
                binary,
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "-C",
                str(workdir),
                "-m",
                self._config.codex_model,
                "-c",
                f'model_reasoning_effort="{self._config.codex_reasoning_effort}"',
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
            try:
                process = subprocess.Popen(
                    command,
                    cwd=workdir,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except OSError as exc:
                raise SuggestionProviderError(f"não foi possível iniciar Codex CLI: {exc}") from exc
            stdout, stderr, duration_ms = _communicate(
                process,
                "Não use ferramentas. Retorne somente o objeto JSON solicitado.\n\n" + prompt,
                timeout_seconds=self._config.timeout_seconds,
                should_cancel=should_cancel,
                provider_name="Codex CLI",
            )
            if process.returncode != 0:
                detail = _codex_error_detail(stderr)
                raise SuggestionProviderError(f"Codex CLI falhou ({process.returncode}): {detail}")
            if not output_path.exists():
                raise SuggestionProviderError("Codex CLI não gravou a resposta estruturada")
            try:
                document = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SuggestionProviderError("resposta final do Codex CLI não é JSON válido") from exc
            if not isinstance(document, dict):
                raise SuggestionProviderError("resposta final do Codex CLI não é um objeto")
        return SuggestionProviderResult(
            document=document,
            provenance={
                "provider": self.name,
                "model": self._config.codex_model,
                "reasoning_effort": self._config.codex_reasoning_effort,
                "binary": Path(binary).name,
                "binary_version": _binary_version(binary),
                "duration_ms": duration_ms,
                "auth_mode": "chatgpt_subscription_or_cli_configuration",
            },
        )


def _claude_usage_limit_message(detail: str) -> str | None:
    lowered = detail.lower()
    markers = ("rate limit", "usage limit", "limit reached", "hit your limit", "limite de uso")
    if any(marker in lowered for marker in markers):
        return "Limite de uso da assinatura Claude atingido; tente novamente após o reset do limite."
    return None


class ClaudeCodeCliProvider:
    """Runs suggestion generation through the Claude Code CLI, billed to the
    user's Claude Pro subscription (never API credits)."""

    name = "claude_code_cli"

    def __init__(self, config: AiConfig, *, effort: str | None = None) -> None:
        self._config = config
        self._effort_override = effort

    def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        should_cancel: Callable[[], bool],
        system_context: str | None = None,
    ) -> SuggestionProviderResult:
        resolved = resolve_claude_binary(self._config.claude_binary)
        binary = resolved.path
        command = [
            binary,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
            "--model",
            self._config.claude_model,
            "--tools",
            "",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--disable-slash-commands",
            "--no-session-persistence",
        ]
        effort = self._effort_override or self._config.claude_effort
        if effort:
            command += ["--effort", effort]
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in _CLAUDE_STRIPPED_ENV_VARS
        }
        # The CLI routes small auxiliary calls to Haiku; keep every call of an
        # editorial run on the configured model.
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = self._config.claude_model
        env["ANTHROPIC_SMALL_FAST_MODEL"] = self._config.claude_model
        with tempfile.TemporaryDirectory(prefix="cortex-claude-") as directory:
            workdir = Path(directory)
            if system_context:
                # A file keeps long transcripts clear of the per-argument size
                # limit, and the CLI caches the system prompt: a second call on the
                # same episode (top-up round, new direction) reads it from cache.
                system_path = workdir / "system.md"
                system_path.write_text(
                    f"{_CLAUDE_SYSTEM_PROMPT}\n\n{system_context}", encoding="utf-8"
                )
                command += ["--system-prompt-file", str(system_path)]
            else:
                command += ["--system-prompt", _CLAUDE_SYSTEM_PROMPT]
            try:
                process = subprocess.Popen(
                    command,
                    cwd=workdir,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except OSError as exc:
                raise SuggestionProviderError(
                    f"não foi possível iniciar Claude Code CLI: {exc}"
                ) from exc
            stdout, stderr, duration_ms = _communicate(
                process,
                prompt,
                timeout_seconds=self._config.timeout_seconds,
                should_cancel=should_cancel,
                provider_name="Claude Code CLI",
            )
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                if process.returncode != 0:
                    detail = stderr.strip()[:500] or f"código de saída {process.returncode}"
                    usage_limit = _claude_usage_limit_message(detail)
                    raise SuggestionProviderError(
                        usage_limit or f"Claude Code CLI falhou ({process.returncode}): {detail}"
                    ) from None
                raise SuggestionProviderError(
                    "resposta do Claude Code CLI não é JSON válido"
                ) from None
            if not isinstance(payload, dict) or payload.get("type") != "result":
                raise SuggestionProviderError(
                    "resposta do Claude Code CLI não é um objeto de resultado"
                )
            if payload.get("is_error") or payload.get("subtype") != "success":
                detail = str(
                    payload.get("result") or payload.get("api_error_status") or "erro desconhecido"
                )[:500]
                usage_limit = _claude_usage_limit_message(detail)
                raise SuggestionProviderError(
                    usage_limit or f"Claude Code CLI reportou erro: {detail}"
                )
            document = payload.get("structured_output")
            if not isinstance(document, dict):
                raise SuggestionProviderError(
                    "Claude Code CLI não retornou structured_output válido"
                )
            usage = payload.get("usage") or {}
            model_usage = payload.get("modelUsage") or {}
            # The CLI also bills auxiliary Haiku calls here; the selection model
            # is the one that produced the most output.
            model = max(
                model_usage,
                key=lambda name: (model_usage[name] or {}).get("outputTokens") or 0,
                default=None,
            ) or self._config.claude_model
        return SuggestionProviderResult(
            document=document,
            provenance={
                "provider": self.name,
                "model": model,
                "effort": effort,
                "binary": Path(binary).name,
                "binary_version": resolved.version,
                "binary_resolution": resolved.source,
                "duration_ms": duration_ms,
                "auth_mode": "claude_subscription_cli",
                "usage": {
                    "input_tokens": usage.get("input_tokens"),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                },
            },
        )


def build_suggestion_provider(
    config: AiConfig, *, cwd: Path, effort: str | None = None,
) -> SuggestionProvider:
    """The configured provider only; switching providers is a config change,
    never an automatic fallback. ``effort`` overrides the configured effort for
    one stage (e.g. the per-clip refine pass)."""
    del cwd
    if config.provider == "claude_code_cli":
        return ClaudeCodeCliProvider(config, effort=effort)
    if config.provider == "codex_cli":
        return CodexCliProvider(config)
    raise SuggestionProviderError(f"provider de IA desconhecido: {config.provider}")
