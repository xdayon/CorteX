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


_TRANSCRIPT_REQUEST_MARKER = "## Pedido atual e transcrição\n"
_TRANSCRIPT_LINE_RE = re.compile(r"^\[(?P<start>[\d.]+)-(?P<end>[\d.]+)\]\s(?P<text>.*)$")


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)].rstrip() + "…"


class LocalHeuristicProvider:
    """Deterministic, LLM-free candidate generator.

    Reads the structured request payload that ``SuggestionService`` appends to
    the end of every prompt (after ``_TRANSCRIPT_REQUEST_MARKER``) and derives
    clip candidates directly from transcript timing and pause data, without
    calling any external model.
    """

    name = "local_heuristic"

    def _parse_request_payload(self, prompt: str) -> dict[str, Any]:
        marker_index = prompt.rfind(_TRANSCRIPT_REQUEST_MARKER)
        if marker_index == -1:
            raise SuggestionProviderError(
                "heurística local não encontrou o payload estruturado no prompt"
            )
        tail = prompt[marker_index + len(_TRANSCRIPT_REQUEST_MARKER) :]
        try:
            payload = json.loads(tail)
        except json.JSONDecodeError as exc:
            raise SuggestionProviderError(
                "heurística local não conseguiu decodificar o payload estruturado"
            ) from exc
        if not isinstance(payload, dict):
            raise SuggestionProviderError("payload estruturado da heurística local não é um objeto")
        return payload

    @staticmethod
    def _parse_segments(transcript_text: str) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for line in transcript_text.splitlines():
            match = _TRANSCRIPT_LINE_RE.match(line)
            if not match:
                continue
            start = float(match.group("start"))
            end = float(match.group("end"))
            text = match.group("text").strip()
            if end <= start or not text:
                continue
            segments.append({"start": start, "end": end, "text": text})
        segments.sort(key=lambda item: item["start"])
        return segments

    @staticmethod
    def _pause_boundaries(local_audio_analysis: dict[str, Any] | None) -> set[float]:
        if not local_audio_analysis:
            return set()
        boundaries: set[float] = set()
        for pause in local_audio_analysis.get("notable_pauses") or []:
            start = pause.get("start")
            end = pause.get("end")
            if isinstance(start, (int, float)):
                boundaries.add(round(float(start), 1))
            if isinstance(end, (int, float)):
                boundaries.add(round(float(end), 1))
        return boundaries

    @staticmethod
    def _pacing(words_per_second: float) -> str:
        if words_per_second >= 2.5:
            return "dynamic"
        if words_per_second <= 1.2:
            return "contemplative"
        return "balanced"

    def _build_candidates(
        self,
        segments: list[dict[str, Any]],
        *,
        minimum_seconds: float,
        maximum_seconds: float,
        pause_boundaries: set[float],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for i in range(len(segments)):
            words_so_far = 0
            for j in range(i, len(segments)):
                window_segments = segments[i : j + 1]
                duration = window_segments[-1]["end"] - window_segments[0]["start"]
                if duration > maximum_seconds:
                    break
                words_so_far = sum(len(seg["text"].split()) for seg in window_segments)
                if duration < minimum_seconds:
                    continue
                start = window_segments[0]["start"]
                end = window_segments[-1]["end"]
                has_pause_boundary = (
                    round(start, 1) in pause_boundaries or round(end, 1) in pause_boundaries
                )
                density = words_so_far / duration if duration > 0 else 0.0
                candidates.append(
                    {
                        "segments": window_segments,
                        "start": start,
                        "end": end,
                        "duration": duration,
                        "words": words_so_far,
                        "density": density,
                        "has_pause_boundary": has_pause_boundary,
                    }
                )
        candidates.sort(
            key=lambda item: (
                not item["has_pause_boundary"],
                -item["density"],
                -item["duration"],
                item["start"],
            )
        )
        return candidates

    @staticmethod
    def _select_non_overlapping(candidates: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for candidate in candidates:
            if len(selected) >= count:
                break
            overlaps = any(
                candidate["start"] < chosen["end"] and chosen["start"] < candidate["end"]
                for chosen in selected
            )
            if overlaps:
                continue
            selected.append(candidate)
        selected.sort(key=lambda item: item["start"])
        return selected

    def _build_clip(self, rank: int, candidate: dict[str, Any], *, topic: str | None) -> dict[str, Any]:
        segments = candidate["segments"]
        first_text = segments[0]["text"]
        last_text = segments[-1]["text"]
        middle_segments = segments[1:-1] or segments
        middle_text = " ".join(seg["text"] for seg in middle_segments)
        pacing = self._pacing(candidate["density"])
        title = _truncate(first_text, 100) or "Corte heurístico local"
        headline = _truncate(first_text, 100)
        if len(headline) < 4:
            headline = _truncate(f"{first_text} {last_text}", 100) or "Corte sem LLM"
        boundary_note = " delimitada por pausa segura" if candidate["has_pause_boundary"] else ""
        reasoning = _truncate(
            "Seleção heurística local, sem LLM: janela com densidade de "
            f"{candidate['density']:.2f} palavras/s{boundary_note}, sem cortar no meio de "
            "um segmento de transcrição.",
            800,
        )
        return {
            "rank": rank,
            "title": title,
            "headline": headline,
            "start_second": round(candidate["start"], 3),
            "end_second": round(candidate["end"], 3),
            "estimated_duration": round(candidate["duration"], 3),
            "primary_speaker": "Locutor não identificado (sem diarização)",
            "topic": _truncate(topic or first_text, 160),
            "pacing": pacing,
            "hook": {
                "start_second": round(segments[0]["start"], 3),
                "end_second": round(segments[0]["end"], 3),
                "summary": _truncate(first_text, 300),
                "evidence": _truncate(first_text, 240),
            },
            "context": {
                "start_second": round(middle_segments[0]["start"], 3),
                "end_second": round(middle_segments[-1]["end"], 3),
                "summary": _truncate(middle_text, 300),
                "evidence": _truncate(middle_text, 240),
            },
            "payoff": {
                "start_second": round(segments[-1]["start"], 3),
                "end_second": round(segments[-1]["end"], 3),
                "summary": _truncate(last_text, 300),
                "evidence": _truncate(last_text, 240),
            },
            "approximate_edl": [
                {
                    "order": 0,
                    "source_start": round(candidate["start"], 3),
                    "source_end": round(candidate["end"], 3),
                    "purpose": "development",
                    "preferred_visual": "auto",
                    "audio_mode": "source",
                    "transition_in": "hard_cut",
                    "mask_jump_with": "none",
                    "confidence": 0.35,
                    "notes": "Corte único cobrindo a janela selecionada pela heurística local.",
                },
            ],
            "scores": {
                "spoken_hook": 3,
                "standalone_clarity": 3,
                "emotion": 3,
                "quotability": 3,
                "payoff": 3,
                "compression_safety": 3,
                "audience_relevance": 3,
                "total": 21,
            },
            "reasoning": reasoning,
            "warnings": [
                "Seleção heurística local, sem LLM: recomenda-se revisão humana antes de publicar.",
            ],
        }

    def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        should_cancel: Callable[[], bool],
    ) -> SuggestionProviderResult:
        started = time.monotonic()
        if should_cancel():
            raise SuggestionProviderCancelled("seleção cancelada pelo usuário")
        payload = self._parse_request_payload(prompt)
        segments = self._parse_segments(str(payload.get("transcript") or ""))
        if not segments:
            raise SuggestionProviderError(
                "heurística local não encontrou segmentos de transcrição para gerar candidatos"
            )
        brief = payload.get("brief") or {}
        minimum_seconds = float(brief.get("minimum_seconds") or 15)
        maximum_seconds = float(brief.get("maximum_seconds") or 180)
        count = int(brief.get("count") or 10)
        topic = brief.get("topic")
        pause_boundaries = self._pause_boundaries(payload.get("local_audio_analysis"))

        candidates = self._build_candidates(
            segments,
            minimum_seconds=minimum_seconds,
            maximum_seconds=maximum_seconds,
            pause_boundaries=pause_boundaries,
        )
        if not candidates:
            raise SuggestionProviderError(
                "heurística local não encontrou janelas válidas dentro dos limites de duração"
            )
        selected = self._select_non_overlapping(candidates, count)
        clips = [
            self._build_clip(rank, candidate, topic=topic)
            for rank, candidate in enumerate(selected, start=1)
        ]
        document = {
            "schema_version": "1.0",
            "selection_notes": _truncate(
                f"Seleção heurística local, sem LLM: {len(clips)} corte(s) gerados por densidade "
                "de fala e pausas seguras a partir da transcrição.",
                1000,
            ),
            "clips": clips,
        }
        duration_ms = round((time.monotonic() - started) * 1000)
        return SuggestionProviderResult(
            document=document,
            provenance={
                "provider": self.name,
                "mode": "heuristic",
                "engine": "local_heuristic_v1",
                "llm_used": False,
                "duration_ms": duration_ms,
                "auth_mode": "none",
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
        heuristic: SuggestionProvider | None = None,
        heuristic_name: str | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_name = primary_name
        self._fallback_name = fallback_name
        self._heuristic = heuristic
        self._heuristic_name = heuristic_name

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
        attempts: list[tuple[SuggestionProvider, str]] = [(self._primary, self._primary_name)]
        if self._fallback is not None:
            attempts.append((self._fallback, self._fallback_name or "fallback"))
        if self._heuristic is not None:
            attempts.append((self._heuristic, self._heuristic_name or "local_heuristic"))

        last_error: SuggestionProviderError | None = None
        for index, (candidate_provider, candidate_name) in enumerate(attempts):
            try:
                result = self._validated(candidate_provider, prompt, schema, should_cancel)
            except SuggestionProviderCancelled:
                raise
            except SuggestionProviderError as exc:
                last_error = exc
                continue
            provenance = {
                **result.provenance,
                "requested_provider": self._primary_name,
                "effective_provider": result.provenance.get("provider", candidate_name),
                "fallback_used": index > 0,
            }
            if index > 0 and last_error is not None:
                provenance["fallback_reason"] = str(last_error)[-1000:]
            return SuggestionProviderResult(result.document, provenance)
        assert last_error is not None
        raise last_error


def build_named_provider(name: str, config: AiConfig, *, cwd: Path) -> SuggestionProvider:
    if name == "codex_cli":
        return CodexCliProvider(config)
    if name == "claude_cli":
        return ClaudeCliProvider(config, cwd=cwd)
    if name == "local_heuristic":
        return LocalHeuristicProvider()
    raise SuggestionProviderError(f"provider de IA não suportado: {name}")


def build_suggestion_provider(config: AiConfig, *, cwd: Path) -> SuggestionProvider:
    primary = build_named_provider(config.provider, config, cwd=cwd)
    fallback = (
        build_named_provider(config.fallback_provider, config, cwd=cwd)
        if config.fallback_provider is not None
        else None
    )
    heuristic = (
        LocalHeuristicProvider()
        if config.enable_local_heuristic_fallback and config.provider != "local_heuristic"
        and config.fallback_provider != "local_heuristic"
        else None
    )
    return FallbackSuggestionProvider(
        primary,
        fallback,
        primary_name=config.provider,
        fallback_name=config.fallback_provider,
        heuristic=heuristic,
        heuristic_name="local_heuristic" if heuristic is not None else None,
    )
