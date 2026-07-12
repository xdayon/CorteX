from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from jsonschema import Draft202012Validator

from cortex.config import AiConfig


class SuggestionProviderError(RuntimeError):
    pass


class SuggestionProviderCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class SuggestionProviderResult:
    document: dict[str, Any]
    provenance: dict[str, Any]


class SuggestionProvider(Protocol):
    def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        should_cancel: Callable[[], bool],
    ) -> SuggestionProviderResult: ...


def _binary_version(binary: str) -> str:
    try:
        return subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()[:200] or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


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


def _claude_document(wrapper: dict[str, Any]) -> dict[str, Any]:
    structured = wrapper.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result = wrapper.get("result")
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError as exc:
            raise SuggestionProviderError(
                f"Claude CLI não retornou JSON estruturado: {result.strip()[-1000:]}"
            ) from exc
        if isinstance(parsed, dict):
            return parsed
    raise SuggestionProviderError("Claude CLI não retornou um objeto de seleção")


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
    ) -> SuggestionProviderResult:
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


class ClaudeCliProvider:
    """Runs Claude Code headlessly using the user's configured subscription."""

    name = "claude_cli"

    def __init__(self, config: AiConfig, *, cwd: Path) -> None:
        self._config = config
        self._cwd = cwd

    def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        should_cancel: Callable[[], bool],
    ) -> SuggestionProviderResult:
        binary = shutil.which(self._config.claude_binary)
        if binary is None:
            raise SuggestionProviderError(
                f"CLI configurada não encontrada no PATH: {self._config.claude_binary}"
            )
        command = [
            binary,
            "-p",
            "--safe-mode",
            "--model",
            self._config.claude_model,
            "--effort",
            self._config.claude_effort,
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=self._cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise SuggestionProviderError(f"não foi possível iniciar Claude CLI: {exc}") from exc
        stdout, stderr, duration_ms = _communicate(
            process,
            prompt,
            timeout_seconds=self._config.timeout_seconds,
            should_cancel=should_cancel,
            provider_name="Claude CLI",
        )
        if process.returncode != 0:
            detail = (stdout or stderr or "falha sem detalhes").strip()[-2000:]
            try:
                wrapper = json.loads(stdout)
                detail = str(wrapper.get("result") or wrapper.get("error") or detail)
            except (json.JSONDecodeError, AttributeError):
                pass
            raise SuggestionProviderError(f"Claude CLI falhou ({process.returncode}): {detail}")
        try:
            wrapper = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise SuggestionProviderError("saída do Claude CLI não é JSON válido") from exc
        if not isinstance(wrapper, dict):
            raise SuggestionProviderError("saída do Claude CLI não é um objeto")
        if wrapper.get("is_error"):
            raise SuggestionProviderError(str(wrapper.get("result") or "Claude CLI reportou erro"))
        return SuggestionProviderResult(
            document=_claude_document(wrapper),
            provenance={
                "provider": self.name,
                "model": self._config.claude_model,
                "effort": self._config.claude_effort,
                "binary": Path(binary).name,
                "binary_version": _binary_version(binary),
                "session_id": wrapper.get("session_id"),
                "duration_ms": wrapper.get("duration_ms") or duration_ms,
                "total_cost_usd": wrapper.get("total_cost_usd"),
                "auth_mode": "subscription_or_cli_configuration",
            },
        )


class FallbackSuggestionProvider:
    def __init__(
        self,
        primary: SuggestionProvider,
        fallback: SuggestionProvider | None,
        *,
        primary_name: str,
        fallback_name: str | None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_name = primary_name
        self._fallback_name = fallback_name

    @staticmethod
    def _validated(
        provider: SuggestionProvider,
        prompt: str,
        schema: dict[str, Any],
        should_cancel: Callable[[], bool],
    ) -> SuggestionProviderResult:
        result = provider.generate(prompt, schema, should_cancel=should_cancel)
        errors = sorted(Draft202012Validator(schema).iter_errors(result.document), key=str)
        if errors:
            raise SuggestionProviderError(f"resposta incompatível com schema: {errors[0].message}")
        return result

    def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        should_cancel: Callable[[], bool],
    ) -> SuggestionProviderResult:
        try:
            result = self._validated(self._primary, prompt, schema, should_cancel)
            return SuggestionProviderResult(
                result.document,
                {
                    **result.provenance,
                    "requested_provider": self._primary_name,
                    "effective_provider": result.provenance.get("provider", self._primary_name),
                    "fallback_used": False,
                },
            )
        except SuggestionProviderCancelled:
            raise
        except SuggestionProviderError as primary_error:
            if self._fallback is None:
                raise
            result = self._validated(self._fallback, prompt, schema, should_cancel)
            return SuggestionProviderResult(
                result.document,
                {
                    **result.provenance,
                    "requested_provider": self._primary_name,
                    "effective_provider": result.provenance.get("provider", self._fallback_name),
                    "fallback_used": True,
                    "fallback_reason": str(primary_error)[-1000:],
                },
            )


def _provider(name: str, config: AiConfig, *, cwd: Path) -> SuggestionProvider:
    if name == "codex_cli":
        return CodexCliProvider(config)
    if name == "claude_cli":
        return ClaudeCliProvider(config, cwd=cwd)
    raise SuggestionProviderError(f"provider de IA não suportado: {name}")


def build_suggestion_provider(config: AiConfig, *, cwd: Path) -> SuggestionProvider:
    primary = _provider(config.provider, config, cwd=cwd)
    fallback = (
        _provider(config.fallback_provider, config, cwd=cwd)
        if config.fallback_provider is not None
        else None
    )
    return FallbackSuggestionProvider(
        primary,
        fallback,
        primary_name=config.provider,
        fallback_name=config.fallback_provider,
    )
