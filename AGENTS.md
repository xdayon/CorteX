# CorteX Agent Operating Guide

## Product Source Of Truth

- Read `docs/HANDOFF.md` before changing pipeline behavior.
- That file is the short current handoff. Do not preload `docs/history/`;
  historical instructions can be superseded. Use `docs/ARCHITECTURE.md` to find
  the module and tests for the selected gate, and `docs/REVIEW_DAYON_NEWS.md`
  for the rationale behind the current priorities.
- Treat `docs/ROADMAP.md` and executable tests as the delivery contract.
- `config/cortex.yaml` is the source of truth for models, efforts, durations and
  paths. Docs reference config keys instead of repeating their values.
- Preserve uncommitted user work. Never restore the PodCLI frontend or reimport
  PodCLI code.
- A feature is complete only when it produces a real persisted artifact and has
  focused automated verification. Mock UI is not completion evidence.

## Orchestration And Token Budget

- The development agent's model and the product's editorial model (`ai.*` in
  `config/cortex.yaml`) are independent settings; changing one never changes
  the other.
- Delegate only bounded work that has clear parallel value. Good delegation:
  one module, one test file, one audit question, or one mechanical migration.
- Never ask a subagent to read the whole repository or repeat documents already
  summarized by the orchestrator. Pass exact files, symbols, constraints, and
  acceptance commands.
- Every subagent prompt must include: read/write scope, expected deliverable,
  verification command, maximum response size, and a stop condition.
- Use read-only agents for discovery. Grant writes only with isolated file
  ownership; never let parallel agents edit the same files.
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
- Keep the current handoff short (about 80 lines), roadmap focused on open gates,
  and command/results in dated `docs/evidence/` files. Never append long session
  transcripts or a second competing queue of work to the handoff.

## Architecture Rules

- Pipeline: ingest -> normalize -> transcribe -> analyze -> suggest -> review ->
  edit plan -> render -> quality gate.
- LLMs propose semantic regions and editorial intent. Deterministic code resolves
  physical cut boundaries from words, VAD, waveform, scenes, and media timebase.
- LLM providers are replaceable adapters. CLI/subscription and local providers
  are allowed; API billing must never be required for the local-first path.
- Editorial AI runs through the Claude Code CLI (`claude -p`) on the user's
  Claude subscription — never API credits, never `--bare`. Model and effort are
  `ai.*` in config. One configured provider (`ai.provider`: `claude_code_cli`
  active, `codex_cli` kept as an explicit alternative); no fallback between
  providers; surface failures (including subscription usage limits) explicitly.
- Keep editorial token use small: one selection call per episode, with the
  editorial template + transcript in the cacheable system prompt file and only
  the brief in the message; slim output schema; cache keys contain only what
  changes the output (provider/model/effort, prompt, schema, inputs).
- The per-clip refine pass runs only for clips selected for export (export
  batch, or the export editor previewing a selected clip — same cached result);
  never for the episode-level suggestion list. It is skipped near the minimum
  duration and never bridges selection pieces.
- Validate every model response against a versioned JSON Schema. Record provider,
  model, command/version, prompt hash, input hash, timestamps, and effective mode.
- Never silently fall back between LLM, heuristic, GPU/CPU, decoder, or encoder.
  Expose requested and effective engines in artifacts and UI.
- Run untrusted model output as data only. Never execute shell text produced by a
  model. Invoke CLIs with argument arrays, timeouts, cancellation, and bounded IO.
- Work from the single broadcast master. A visible face is not proof of the
  acoustic speaker; confirm voice/face roles separately. Scope visual analysis
  to reviewed clip ranges; never scan every frame of a long episode by default.
- Preview parity: the export editor shows a draft render of the real edit plan
  with the backend-generated `.ass` overlay (JASSUB in the browser, libass in
  FFmpeg). Never re-implement caption/headline layout in React or CSS.
- Preserve absolute source clocks in artifacts. Input seeking, local filter
  clocks, J/L-cut and borrowed reaction footage need audio/pixel regressions.
- Keep the API on loopback. Private cloud access requires authenticated
  Tunnel/Access for UI, API and media; a hidden URL is not authentication.

## Operating The Local Service

- `cortex.service` (systemd user unit) runs API + worker; the worker reads
  `prompts/*.md` on every job, so prompt changes apply immediately while code
  and config changes need a restart. Restart only when no job is queued or
  running; `scripts/update_and_verify.sh` checks that and refuses otherwise.
- Agents must not restart the service themselves; ask the user.

## Verification

- Backend: `.venv/bin/pytest -q`
- Frontend: `cd apps/web && npm run build`
- Formatting/sanity: `git diff --check`
- Full local update (deps, Claude CLI, tests, UI build, safe restart):
  `./scripts/update_and_verify.sh`
- Visual changes: inspect real FFmpeg frames (PNG) of the affected styles, and
  the Studio at 390 px and desktop width, before calling them done.
- GPU/NVENC host gate: `./scripts/check_gpu.sh` with host permission when needed.
- Do not claim CUDA, NVENC, browser, or end-to-end media validation from a sandbox
  result that cannot access the relevant host resource.
