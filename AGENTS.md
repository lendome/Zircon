# Zircon Agent — Project Source of Truth

## Tech Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Language | Python | >=3.10 |
| AI Models | OpenAI / Anthropic / Local (Ollama) | Configurable via `models.yaml` |
| Graph Storage | SQLite + NetworkX | Built-in |
| Embeddings | Local `sentence-transformers` | Lazy-loaded |
| Framework | Zircon (custom) | 1.0.0 |

## Architecture Overview

Zircon is an autonomous coding agent framework with three execution tiers:

1. **Low** — Simple chat + tool use, no planning, no verification, minimal context
2. **Balanced** — Planning gatekeeper, multi-step execution, verification, history compaction
3. **Quality** — Full planning with LLM consensus, reflection loops, semantic search, swarm mode, chain-of-draft reasoning

### Core Loop

```
User Task → PlanGatekeeper → [PlanRequired? → Planner → HumanApproval] → Execute Steps → Verify → Synthesize
```

Each step is one of: `explore`, `edit`, `verify`, `research`.

## Directory Structure

| Path | Purpose |
|------|---------|
| `AGENTS.md` | This file — agent's internal manual |
| `cli/` | Primary CLI entry point and terminal UI |
| `core/` | Core engine: agent, context, planning, execution, knowledge graph |
| `subagents/` | Specialised sub-agents (explorer, editor, verifier, architect, researcher) |
| `subagents/swarm/` | Swarm mode agents (API builder, frontend builder, backend builder, coordinator, integration) |
| `tools/` | Tool implementations (file ops, edit ops, search, shell, web) |
| `llm/` | LLM router, prompt library, structured output parsing |
| `parsers/` | AST and edit-format parsers |
| `vcs/` | Git integration |
| `core/fault_localizer.py` | Hierarchical Fault Localization (FL) pipeline |
| `tests/` | Test suite |
| `sandbox/` | Sandbox execution environment |
| `benchmark/` | Benchmarking suite |
| `.zircon-code/` | Runtime data directory (auto-created) |

## Architectural Decision Records

### ADR-001: SQLite + NetworkX for Knowledge Graph
**Why:** SQLite provides persistent graph storage with zero external dependencies. NetworkX enables BFS traversal for context retrieval. This avoids needing Neo4j or other external graph DBs.

### ADR-002: Tier-Based Execution
**Why:** Different tasks have different cost/quality requirements. The three-tier system allows cheap "low" mode for simple Q&A, balanced for normal coding, and quality for complex multi-file refactors.

### ADR-003: Sub-Agent Delegation for Quality Tier
**Why:** Instead of one monolithic prompt, specialised sub-agents (explorer, editor, verifier) each get focused prompts. This reduces context pressure and improves output quality on complex tasks.

### ADR-004: Local Embeddings via sentence-transformers
**Why:** Semantic search must work offline and without API costs. `sentence-transformers` provides reasonable quality with small models (~100MB).

### ADR-005: Swarm Mode for Multi-Domain Tasks
**Why:** Parallel agent execution with artifact registry enables full-stack/backend+frontend tasks to complete faster by working in parallel on independent domains.

### ADR-006: Hierarchical Fault Localization (FL) as a pre-loop stage
**Why:** Traditional agents burn up to 80% of their token budget locating bugs via
free-form grep/file navigation. FL replaces that with a strict top-down pipeline
that runs BEFORE the active repair loop:

1. **Phase 1 — File-level IR**: BM25 + local embeddings (reciprocal rank fusion)
   rank the top 3–5 relevant files. No LLM involved.
2. **Phase 2 — Structural parse**: only file signatures (not implementations) are
   sent to a cheap `localize` role LLM, which identifies suspect functions.
3. **Phase 3 — Line-level edit window**: a cheap LLM pinpoints exact start/end
   lines, and a ~50-line surgical snippet is produced.

The repair agent then receives that snippet as context instead of the whole
codebase. It is auto-invoked from `Agent.solve` for bug-fix-shaped tasks, and is
also available on demand via the `fault_localize` tool. If no router/embedder is
available, FL degrades gracefully to BM25 + heuristic/whole-file fallbacks.

### ADR-007: Advisor-Agent Pattern for Quality Tier
**Why:** A large "advisor" model (the `advisor` role in `models.yaml`) generates
a concise, structured Execution Plan once per fresh task, and the smaller worker
model executes with strict adherence. This offloads the heavy reasoning
(deconstruction, sequencing, constraints) to the capable model while the bulk of
generation stays on the fast/cheap one. Implemented as context note injection
(`core/advisor.py` + `Agent._safe_advise`/`_inject_advisor_note`) rather than a
pipeline change, so the gatekeeper/planner/tool-loop are untouched and failure
degrades to no guidance. Enabled via `TierConfig.advisor_enabled` (quality
preset only).

### ADR-008: Framework-Level Interception Layer in the Tool Registry
**Why:** Prompt-level rules ("don't repeat tool calls") are advisory; models
under pressure ignore them. Deterministic waste — byte-identical read calls,
byte-identical failing edits, byte-identical failing commands — can instead be
intercepted at the single choke point every tool call passes through:
`ToolRegistry.execute`. `ReadDeduplicator`, `EditFailureBreaker`, and
`ScopeGuard` live in `tools/registry.py` next to `CommandFailureCache`, sharing
its two proven patterns: mutation-epoch invalidation (any successful edit
re-enables previously intercepted calls, because the world changed) and
synthetic error strings that read as system messages the model acts on. All
interceptions are fail-visible (prefixed `SYSTEM ERROR (dedup):`,
`CIRCUIT-BREAKER:`, `SCOPE-GUARD:`), never silent, and each is independently
disableable via `TierConfig`. Loop detection stays advisory and unchanged —
the interception layer handles exact duplicates; the `LoopDetector` handles
near-repeats and stagnation.

## Contextual Gotchas

1. **DO NOT use shell commands to write files.** Always use `edit_file()`, `edit_lines()`, or `create_file()` tools. Shell-based writes are now tracked (a snapshot-based filesystem state tracker in `core/fs_state.py`, wired on the `ToolRegistry`, diffs the working tree before/after mutating shell tools and verifies real changes against `git` in the background — surfacing only actual byte-level mutations as a `<filesystem_changes>` note and invalidating the read-dedup mutation epoch), but they still bypass the edit-history/VCS semantics and should be avoided. Prefer the explicit edit tools for clarity.
2. **The Knowledge Graph is BFS-based, not weighted.** `get_context_for_task()` uses keyword scoring + BFS traversal with depth_limit=2. It does not use PageRank or path-weighting.
3. **Embeddings are lazy-loaded.** The `embedder` property on `Agent` will silently return `None` if `sentence-transformers` is not installed. Code must handle this gracefully.
4. **History compaction is irreversible.** When `compact_history()` runs, older messages are replaced with an LLM-generated summary. The original messages are lost.
5. **Swarm mode uses topological layers.** Tracks within a layer run in parallel; layers wait for all dependency tracks to complete before starting.
6. **The repo map cache uses mtime-based invalidation.** If files are modified via git operations that don't update mtime (e.g., `git checkout`), the cache may be stale. Use `--no-cache` flag or delete `.zircon-code/repo_map_cache.json`.
7. **SQF support is limited.** The SQF parser only detects `TAG_fnc_name` and `#include` patterns. Other Arma scripting constructs may not be indexed.
8. **Project classification is LLM-dependent.** If the LLM call fails, it falls back to keyword heuristics in `project_classifier.py`.
9. **The `__pycache__` directories and `node_modules` are always skipped** in repo map scanning to avoid OOM on large projects.
10. **Multi-sample consensus in quality tier** generates multiple plans and picks the one with the most steps. This costs more tokens but produces better plans.
11. **Fault Localization is conservative.** `Agent._looks_like_bugfix` only triggers FL for tasks with ≥2 bug-related signals (or a signal plus a stack trace). FL injects a `<fault_localization>` context note and seeds the working set with the pinpointed file; it never edits files and is skipped silently on failure.
12. **Loop detection never hard-stops exploration.** The `LoopDetector` only escalates to a critical (incomplete) stop for a genuine infinite loop — the EXACT same tool-call set emitted `identical_turns_critical` (default 5) consecutive times. Re-reading files, reading different line ranges of the same file, and repeated-but-varied calls only ever produce soft warnings FROM THE LOOP DETECTOR — but see gotcha 19: the registry's `ReadDeduplicator` DOES hard-intercept byte-identical read calls before they execute. Note: chunk tracking keys off `read_file`'s real `start`/`end` args, so different line ranges are distinct chunks. The tool fingerprint also includes a hash of `search`/`replace`/`content` for edit/create calls, so two different edits to the same file are distinct fingerprints — never collapsed into a false "identical call".
13. **The advisor runs once per fresh quality-tier task, then checks in periodically.** The initial plan fires after fault localization and before the gatekeeper in `Agent.solve`/`solve_stream`; swarm-mode routing and AWAITING_INPUT resume paths return earlier and never see it. Mid-loop, the executor calls `advisor_callback` every `advisor_checkin_interval` (default 10) turns in BOTH `run_tool_loop` and `run_tool_loop_stream`, injecting the feedback memo as an `<advisor_feedback>` system note. Only the Agent's main executor has the callback wired — sub-agent/research executors never check in. Neither path blocks the loop: a failed/timed-out advisor call just means no note is injected. The `advisor` role falls back to the `default` profile if the advisor profile is removed from `models.yaml`.
14. **Use `run_in_terminal` for servers/long-running commands, never `run_command`.** `run_in_terminal` opens a separate visible console window, waits `wait_seconds`, then returns the output so far while the command keeps running; poll with `terminal_output`, kill with `terminal_stop`. It writes the command verbatim into a `.cmd` body (plus an `__ZIRCON_DONE__ exit_code=N` marker line) and tees output to a UTF-16 log under `.zircon-code/terminals/` that `read_log()` BOM-sniffs. `run_command` only times out on process exit; on timeout the process is now *adopted* (not restarted) as a `shell_*` background job, and its post-exit pipe drain is bounded so a detached grandchild holding the stdout pipe can no longer hang the call forever.
15. **The shell is pinned per machine and named in tool descriptions.** `core/shell_env.resolve_shell()` detects ONE working shell (Git Bash → pwsh → cmd on Windows, probe-verified; the WindowsApps WSL stub is excluded) and `run_command`/`shell_start`/`run_task`/`verify_determinism`/`run_profiler` all execute through it. The active shell's syntax family is appended to those tools' descriptions, so always read the description before composing redirects or path syntax. `run_in_terminal` is intentionally still cmd-based (visible `.cmd` windows). Disable via `TierConfig.shell_pinning_enabled=False` to restore platform-default behavior.
16. **Identical failing commands are circuit-broken at the registry.** `CommandFailureCache` (tools/registry.py) intercepts byte-identical repeats of `run_command`/`run_task`: shell-syntax failures (exit 127, "not recognized", parser errors) are hard-blocked forever until the command text changes; other failures are blocked from the 3rd consecutive repeat unless a mutation tool succeeded since (and every 3rd intercepted attempt still passes through as a flaky-escape valve). Successful execution clears the entry. Disable via `TierConfig.command_circuit_breaker_enabled=False`.
17. **The advisor can VETO tools, not just advise.** `SYSTEM_ADVISOR_CHECKIN` defines a ```` ```veto ```` block (`tool`/`turns`/`reason`); the executor parses it, clamps to `advisor_veto_max_turns` (default 3), and strips the tool from offered schemas AND denies it in `_execute_batch`. Whitelist is mutation/search tools only (`edit_file`, `edit_lines`, `create_file`, `aider_edit`, `delete_file`, `web_search`) — read/nav tools and `run_command` can never be gated. Vetoes auto-expire and the tool then enters a 5-turn cooldown before it can be vetoed again. Only the main executor applies vetoes (sub-agents/research are exempt). Disable via `TierConfig.advisor_veto_enabled=False`.
18. **Prefer the dev-workflow tools over shell gymnastics.** `run_task(command, save_output_to="golden.txt")` captures stdout/stderr separately and saves LF-normalized output without redirects; `verify_determinism(command, runs=3)` replaces manual run-diff-compare loops; `run_profiler(command)` replaces hand-placed timers (auto-detects cProfile for `python x.py`, `--cpu-prof` for `node x.js`, pprof flags for `go test`; anything else returns the benchmark-harness recipe — it cannot instrument `go run` or prebuilt binaries). For code navigation, `get_function_body(symbol)` / `find_references(symbol)` / `get_symbol_definition(symbol)` / `get_function_dependencies(symbol)` / `get_callers(symbol)` / `get_ast_range(path, start_line, end_line)` (tools/nav_ops.py over parsers/symbol_nav.py) replace guessed read_file line ranges; Python uses real `ast` end lines, Go/JS/TS/Rust use brace matching that ignores strings/comments. `get_function_dependencies` maps a function's call graph — every callee resolved to file:line via a one-pass repo-wide definition index — so the agent can jump straight to the dependency that matters instead of reading whole files. `get_callers` is the reverse: every function/method that calls a symbol, each resolved to file:line (the answer to "who calls X?" before refactoring/deleting). `get_ast_range` expands a grep/read line range to its tightest enclosing AST block (function/class/if/for/while/try for Python via real `ast`; enclosing definition for Go/JS/TS/Rust) with the scope chain, so the agent can understand control flow and variable scope around a hit without reading whole files.
19. **Exact-duplicate read calls are hard-intercepted at the registry.** `ReadDeduplicator` (tools/registry.py, alongside `CommandFailureCache`) keys read-only calls (`read_file`, `grep_code`, `glob_files`, `list_dir`, `find_symbols`, `get_structure`, and the four nav tools) by (tool, whitespace-normalized args) and returns `SYSTEM ERROR (dedup): you already ran … in call #N … Do not repeat tool calls` for byte-identical repeats with an unchanged mutation epoch. Any successful mutation tool bumps the epoch, so a re-read after an edit always executes and refreshes the entry. Failed reads are never recorded (retrying an error is always allowed), and an intercepted repeat does not refresh the original call number. Web tools, `run_command`, and the stateful scroll tools are NOT covered. Disable via `TierConfig.read_dedup_enabled=False`.
20. **Identical failing edits are circuit-broken like failing commands.** `EditFailureBreaker` (tools/registry.py) tracks (tool, path, payload-hash) for `edit_file`/`edit_lines`/`aider_edit`; after 2 consecutive byte-identical failures it intercepts the next identical attempt with a CIRCUIT-BREAKER message telling the agent to re-read the region and rebuild the SEARCH text from the actual current code. One real retry is always allowed (transient file locks happen); any successful mutation clears all entries. Disable via `TierConfig.edit_failure_breaker_enabled=False`.
21. **Component-scoped tasks arm the ScopeGuard.** When the user's task matches `the <name> (engine|module|component|parser|disassembler|decompiler|service|layer|pipeline|subsystem)` (`Agent._arm_scope_guard`, in `solve`/`solve_stream` after classification), the component name is resolved against the repo map by file stem, directory segment (armed as a directory so new files inside it are in scope), and indexed symbol name (e.g. "the EditEngine component" resolves edit_engine.py via its class). Zero hits leaves the guard DISARMED — it never blocks on a bad guess. Modes via `TierConfig.scope_guard_mode`: `"warn"` (default) executes outside-scope edits but prefixes the result with a SCOPE-GUARD warning; `"block"` denies them and names the component's files; `"off"` disables. For `aider_edit` the path is parsed from the first content line; an unparseable path is never blocked. The guard is disarmed in `_reset_state`, and the armed label adds a `## COMPONENT SCOPE` section to the system prompt.
22. **"Risk knob" tasks get a bold-execution persona, and prompts assume a VCS safety net only when one exists.** `core/stealth_prompts.py` activation #13 fires on user phrases like "nothing is off limits" / "do whatever it takes" / "be aggressive" / "rewrite from scratch" and appends a `## BOLD EXECUTION MODE` block that explicitly SUSPENDS the minimal-edit rules in favor of radical, algorithmic change (it lands before `## PLATFORM`, i.e. after all cautious rules, so it takes precedence). Separately, `_get_system_prompt` appends `SYSTEM_BIAS_TOWARD_ACTION` and `SYSTEM_SPIRIT_CHECK` (the `<spirit>` literal_request/underlying_intent/cheap_ways_out_to_avoid block) for the balanced/thorough styles, and `SYSTEM_SAFETY_NET` only when `GitIntegration.is_available()` confirms a real git repo — the prompt never claims an undo button that doesn't exist. The planner JSON schema also carries an optional `spirit` object, surfaced as `Plan.spirit`. At 80% of the tool-turn budget the main executor injects a one-time `TURN BUDGET` system note in both loop paths (sub-agents already had one; `_budget_nudged` resets in `reset_recovery`).

## Version History

- **1.0.0** — Initial release. Low/Balanced/Quality tiers, Knowledge Graph, Swarm mode, Project classification, Sub-agent delegation.

## Background Async Tasks

The system supports fire-and-forget background tasks that run after the main response is sent to the user. These tasks:

- Are created via `create_background_task(coroutine, name="")`
- Run concurrently with no dependency on the main response
- Are tracked in `.zircon-code/tasks/` for status monitoring
- Auto-generate AGENTS.md in quality tier after 10+ message sessions

## Coding Conventions

- Use `from __future__ import annotations` in all Python files
- Type hints everywhere
- Logger name format: `"agent.core.<module>"` for core, `"agent.subagents.<module>"` for subagents
- Dataclasses for all data transfer objects (see `core/types.py`)
- Any and callables for function injection rather than abstract base classes
