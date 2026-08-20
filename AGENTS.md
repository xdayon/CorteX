# CorteX Agent Operating Guide

## Product Source Of Truth

- Read `docs/HANDOFF_FABLE_5.md` before changing pipeline behavior.
- Treat `docs/ROADMAP.md` and executable tests as the delivery contract.
- Preserve uncommitted user work. Never restore the PodCLI frontend or import
  new code from `mods/` without removing legacy paths and dependencies.
- A feature is complete only when it produces a real persisted artifact and has
  focused automated verification. Mock UI is not completion evidence.

## Orchestration And Token Budget

- Default project model: `gpt-5.4-mini` with low reasoning.
- Reserve a frontier model for architecture choices, security-sensitive work,
  cross-module integration, difficult debugging, and final review.
- Delegate only bounded work that has clear parallel value. Good delegation:
  one module, one test file, one audit question, or one mechanical migration.
- Never ask a subagent to read the whole repository or repeat documents already
  summarized by the orchestrator. Pass exact files, symbols, constraints, and
  acceptance commands.
- Every subagent prompt must include: read/write scope, expected deliverable,
  verification command, maximum response size, and a stop condition.
- Prefer `gpt-5.4-mini`, low reasoning, read-only sandbox for discovery. Grant
  workspace writes only for isolated file ownership; never let parallel agents
  edit the same files.
- Stop agents that begin broad exploration or dump large file contents. Raw
  agent transcripts are not useful deliverables.
- The orchestrator owns integration, conflict resolution, final tests, product
  claims, and updates to roadmap/handoff state.

## Context Discipline

- Use `rg` and targeted line reads. Do not preload generated files, media,
  caches, lockfiles, build output, or entire large modules without a reason.
- Keep durable product decisions here or in focused docs, not in chat history.
- Keep handoffs short: current state, exact next gate, commands, and known risks.
- Prefer structured artifacts and compact JSON over sending raw transcript,
  waveform, logs, or command output to an LLM.
- Hash and cache all expensive stage inputs. A retry with the same inputs must
  reuse the artifact whenever the stage is deterministic.

## Architecture Rules

- Pipeline: ingest -> normalize -> transcribe -> analyze -> suggest -> review ->
  edit plan -> render -> quality gate.
- LLMs propose semantic regions and editorial intent. Deterministic code resolves
  physical cut boundaries from words, VAD, waveform, scenes, and media timebase.
- LLM providers are replaceable adapters. CLI/subscription and local providers
  are allowed; API billing must never be required for the local-first path.
- Transcript analysis requests only Codex CLI (`gpt-5.5`, medium reasoning).
  Do not add an editorial fallback provider; surface Codex failures explicitly.
- Validate every model response against a versioned JSON Schema. Record provider,
  model, command/version, prompt hash, input hash, timestamps, and effective mode.
- Never silently fall back between LLM, heuristic, GPU/CPU, decoder, or encoder.
  Expose requested and effective engines in artifacts and UI.
- Run untrusted model output as data only. Never execute shell text produced by a
  model. Invoke CLIs with argument arrays, timeouts, cancellation, and bounded IO.

## Verification

- Backend: `.venv/bin/pytest -q`
- Frontend: `cd apps/web && npm run build`
- Formatting/sanity: `git diff --check`
- GPU/NVENC host gate: `./scripts/check_gpu.sh` with host permission when needed.
- Do not claim CUDA, NVENC, browser, or end-to-end media validation from a sandbox
  result that cannot access the relevant host resource.
