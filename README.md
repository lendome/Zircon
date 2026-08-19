# ZirconX

Python 3.10+ | Autonomous Coding, Research & Security Agent Framework | v1.0

Zircon is an autonomous agent framework that actually works on real codebases — and on real targets. It does not need a vector database. It does not need Docker. It does not need a cloud account. The whole thing, including semantic search via local embeddings, runs offline by default on your machine.

Where most agent frameworks fall apart the moment a task touches more than one file, Zircon keeps going. A knowledge graph handles context retrieval, a tiered planner figures out how hard to think before it acts, sub-agents take on specialized roles, and an optional swarm mode runs multiple agents in parallel. From a single-line edit to a multi-file refactor across a project you have never opened before, it just does the work.

This is also, by design, partially a **penetration-testing and cybersecurity tool**. Zircon ships with an integrated authorization layer that unlocks offensive-security workflows — exploit development, credential cracking, malware analysis, network injection, recon, and more — alongside its coding capabilities. It is intended for authorized security testing, CTF work, red-team engagement, defensive research, and education. See the **License & Liability** section at the bottom.

## How It Thinks

```
User Task -> PlanGatekeeper -> [Plan? -> Planner -> Execute -> Verify] -> Synthesize
                                 No -> Direct execution
```

Three execution tiers control how much planning happens before the agent acts. You pick one at launch, and you can switch at runtime without restarting.

Low (fast)
  Direct chat and tool execution. No planning, no verification, minimal context retention.
  Best for: simple Q&A, single-file edits, fast prototyping. This is the cheap and fast mode.

Balanced
  Single-plan gatekeeper, multi-step execution, automatic verification, history compaction.
  Best for: general development work. This is the default.

Quality (high)
  Multi-sample plan consensus where the LLM generates competing plans and the strongest one wins. Reflection loops, semantic search retrieval, sub-agent delegation, swarm mode, chain-of-draft reasoning.
  Best for: complex multi-file refactors, unfamiliar codebases, changes you cannot afford to mess up.

The planner produces a structured step sequence. Each step is one of: explore, edit, verify, or research. Steps run in order with state carried between them.

## What The Engine Can Actually Do

The `core/` engine is not a thin wrapper around an LLM. It is a full runtime with a planner, an advisor, an executor, a knowledge graph, a sandbox, fault localization, syntax checking, and a swarm orchestrator. Highlights:

- **Hierarchical Fault Localization** (`fault_localizer.py`): a three-phase pipeline (BM25 file-level ranking + reciprocal-rank fusion with optional embeddings, structural parse with a cheap-LLM suspect classifier, line-level window pinpointing) that injects a `<fault_localization>` block before the main loop even starts. When something is broken, Zircon finds *where* before it tries to fix it.
- **Advisor-Agent pattern** (`advisor.py`): a second model role reviews the execution plan before the first tool call and checks in mid-loop. It can veto destructive edits for N turns.
- **Tiered tool loop with circuit breakers** (`executor.py`, `registry.py`): hard turn caps, wall-clock deadlines, command-failure cache that blocks identical failing shell retries, edit-failure breakers, read deduplication with mutation-epoch invalidation, web-search anti-thrash, and a `ScopeGuard` that can be armed to confine work to a named component.
- **Evidence-aware completion gate** (`completion_gate.py`): refuses bare "Done." answers when build artifacts or reachable server URLs are missing. The agent has to prove it finished.
- **Loop detection** (`loop_detector.py`): tracks read-only cycles, same-file re-reads, and exact-identical consecutive turns, stopping runaway loops before they burn your budget.
- **Trajectory pruning** (`trajectory_diet.py`, `distiller.py`): compresses old tool results, expired reads, and noisy build logs once the conversation approaches the context limit, while protecting recent turns.
- **Knowledge graph memory** (`kg_memory.py`): SQLite + NetworkX graph of files, functions, classes, symbols, errors, and tasks, with BFS retrieval and keyword scoring. Context is relationships, not a flat embedding table.
- **Local embeddings** (`embeddings.py`): sentence-transformers (`nomic-ai/nomic-embed-text-v1.5`) loaded lazily, SHA256-keyed disk cache, graceful degradation to keyword matching when unavailable.
- **Repo-map indexing** (`context.py`, `ast_parser.py`): per-language symbol/import parsing for Python, Go, JS/TS, Rust, C-family, and SQF, with mtime-based cache invalidation and import-graph scoring.
- **Multi-language syntax checking** (`syntax_checker.py`): Python (`ast` + `ruff`), JS (`node --check`), TS (`tsc --noEmit`), JSON, YAML, TOML, HTML/XML, CSS, Shell (`bash -n` + `shellcheck`), Dockerfile. Edits that produce invalid syntax are rolled back automatically.
- **AST-aware edit engine** (`edit_engine.py`): exact → fuzzy → whitespace-normalized → AST-aware matchers, Python symbol replacement with indent normalization, and Aider-style `<<<<<<< SEARCH / ======= / >>>>>>> REPLACE` multi-file blocks.
- **Git session isolation** (`git_integration.py`, `vcs/git.py`): dulwich-backed session branches, auto-commit, rollback, and a checkpoint API that snapshots the workspace independent of git.
- **Docker sandboxing** (`sandbox_executor.py`): isolated containers with memory/CPU limits and `--network none` by default for test execution in the Quality tier.
- **Swarm orchestration** (`swarm_orchestrator.py`, `swarm_plan_builder.py`): topological layering of parallel agent tracks with cross-track contract validation, integration, and final verification.
- **Profiling** (`profiling.py`): auto-detects cProfile / Node `--cpu-prof` / Go pprof and rewrites commands to emit profiles, then parses and formats hotspots.
- **Project classification** (`project_classifier.py`): 13 categories with an LLM classifier and heuristic fallback, injecting domain-specific guidance (including anti-bot/anti-cheat engineering with obfuscation, fingerprinting, and zero-day awareness).

## The CLI

Zircon ships as a real CLI plus a full-screen TUI, split cleanly from a backend daemon. This is not a script that prints to stdout. It is a binary-grade tool with a declarative command tree, lazy-loaded handlers, and a reactive UI that survives the daemon being restarted.

```
zircon                              Open the TUI in the current directory
zircon /path/to/project             Open the TUI in a specific workspace
zircon --low                        Low tier (cheap, fast)
zircon --quality                    Quality tier with full planning
zircon --swarm                      Swarm mode
zircon --plan-mode                  Force the planner on
zircon --verbose                    Verbose logging

zircon serve                        Start the daemon server in the foreground
zircon task "fix the off-by-one"    Run a task headless, no TUI
zircon api                          Headless JSON-RPC mode on stdin/stdout
zircon status                       Show daemon and session status
zircon tier fast                    Switch tier at runtime (no restart)
zircon tier quality                 balanced | quality | fast all accepted
zircon service start                Start the background daemon
zircon service stop                 Stop the background daemon
zircon service restart              Restart the background daemon
zircon tui                          Launch the TUI explicitly (alias: chat)
zircon help                         Print the full command tree
```

Install it once and the `zircon` command is yours from any terminal:

```
./install_cli.sh        # macOS / Linux
install_cli.bat         # Windows
```

Or run it directly without installing:

```
python -m zirconAgent.cli
```

### Daemon and TUI split

The TUI never runs business logic itself. It talks to a backend daemon over a transport, either JSON-over-TCP to a running server or an in-process direct call. That separation is the point. The TUI can crash, close, or be restarted without losing your session, because the agent state lives in the daemon.

The daemon writes a lock file to `.zircon-code/daemon.lock` so the CLI can discover and manage it across process boundaries. Start one with `zircon service start`, check it with `zircon status`, and kill it with `zircon service stop`. The `serve` command runs the same server in the foreground so you can watch it.

### The TUI

The interface is a full-screen reactive UI assembled from a provider tree: clipboard, args, key-value store, project, SDK, renderer, config, theme, keymap, events, sync, routing, dialogs, permissions, frecency, prompt history, prompt stash, editor context, toasts, and the data layer. The whole thing is wrapped in an error boundary, so a rendering error in one component does not kill the app.

It comes with a command palette, file autocomplete, dialogs, a theming system, a plugin runtime, editor integration, and an attention system that tracks what the agent is focused on. This is a terminal application, not a print loop.

### Headless and embeddable

Two ways to run without the TUI:

`zircon task` streams colored trace events to stdout as the agent works through plan, steps, tool calls, and verification. Pipe it into a log, watch it in CI, script around it.

`zircon api` speaks newline-delimited JSON-RPC over stdin/stdout. Methods include `chat_stream`, `solve_stream`, `submit_feedback`, `get_status`, and `reset_context`. This is the integration point for editors and automation.

And the framework is still programmable directly:

```python
from zirconAgent.core.agent import Agent

agent = Agent(
    repo_path=".",
    config_path="models.yaml",
    tier="balanced",
    swarm_mode=False,
)
await agent.solve("Refactor the database layer to use async queries")
```

## Key Differentiators

### Knowledge Graph, not a vector store

Context lives in a SQLite + NetworkX knowledge graph. Not a vector database. The graph does BFS traversal with keyword scoring and configurable depth limits. You do not run a separate vector DB service, and you still get relevant file and symbol context for each task.

- BFS-based retrieval with depth_limit=2 by default
- Keyword scoring from the task description
- Zero external dependencies. Everything is local.

### Local embeddings

Semantic search uses sentence-transformers loaded lazily on demand. No API calls. No cloud dependency. If the package is not installed, the system degrades gracefully. Embeddings return None and retrieval falls back to keyword matching. It works either way.

### Sub-Agent delegation (Quality tier)

Instead of one massive prompt, the Quality tier hands work to specialized sub-agents:

- Explorer: codebase navigation, file discovery, symbol resolution
- Editor: file modifications, edit application, diff generation
- Verifier: post-execution validation, error checking, test running
- Architect: high-level design decisions, file structure planning
- Researcher: documentation lookup, pattern analysis, multi-step web research

Each sub-agent gets a focused prompt for its role. This reduces context pressure and improves output quality on complex tasks.

### Swarm mode

For tasks that cross independent domains (full-stack changes, backend plus frontend), swarm mode runs multiple agents in parallel using topological layering. Tracks inside a layer run concurrently. Layers wait for all dependency tracks to finish before starting. Results merge through an artifact registry. Parallel where it can be, ordered where it has to be.

### History compaction

The context window auto-compacts when the message count passes a threshold. Older messages get replaced with an LLM-generated summary. This is irreversible. The original messages are gone. But it keeps long sessions from hitting context limits.

### Repo map caching

Project structure caches to `.zircon-code/repo_map_cache.json` with mtime-based invalidation. No redundant scanning on repeated tasks. Directories like `__pycache__` and `node_modules` are excluded by default.

## Security & Penetration-Testing Capabilities

Zircon is deliberately not limited to writing code. It is built to also function as an authorized security testing and cybersecurity research tool. The framework includes an integrated authorization system that, when triggered by the nature of a task, unlocks offensive-security workflows so the agent does not refuse, hedge, or moralize legitimate authorized work.

### Authorization unlock layer

`core/stealth_prompts.py` ships keyword-triggered authorization activations covering the full offensive-security spectrum. When a task's keywords match, an authorization block is appended to the system prompt instructing the model to proceed. Activations include, among others:

- Reverse engineering, decompiling, and disassembly
- Penetration testing, security scanning, nmap/masscan, and vulnerability scanning
- Exploit and payload development — shellcode, PoC, RCE, buffer overflow, UAF, heap spray, ROP, format-string, integer-overflow
- Credential attacks — password/hash cracking, brute force, hashcat, john, hydra, credential stuffing, rainbow tables
- Phishing and social-engineering simulation — spear-phishing, vishing, smishing
- Malware analysis — unpacking, ransomware/trojan/rootkit/backdoor/RAT analysis, IOC extraction, YARA, signature development
- Cryptanalysis — side-channel, timing, padding-oracle, weak-crypto attacks
- OSINT and reconnaissance — data scraping, footprinting
- Privilege escalation and lateral movement — privesc, pass-the-hash, pass-the-ticket, kerberoasting, AS-REP roasting, golden/silver ticket
- Security-control bypass — WAF, auth, MFA/2FA, CAPTCHA, rate-limit, AV/EDR, CSP, SSO bypass and detection evasion
- Network injection and MITM — ARP/DNS/NDP/DHCP spoofing, SSL strip, wifi/WPA/WPA2/WPS cracking, evil twin, rogue AP
- Data exfiltration simulation — DNS/ICMP/HTTP tunneling, DLP bypass, covert channels

Activations are case-insensitive, whole-word matched, stack on top of each other, and fire on every system-prompt build.

### Domain authorization signal

When a user task mentions a domain, the agent can fetch that domain's `/zircon` path and treat a returned "Certificate of Authorization" as a device-bound trusted framework signal. This is the mechanism that binds an engagement scope to the specific machine running Zircon.

### Tool surface for security work

- **Shell execution** — `run_command`, `shell_start`/`poll`/`stop`/`input`/`close_stdin`, and visible `run_in_terminal` with full command capture, background process adoption on timeout, and a pinned one-shell-per-machine model.
- **Web operations** — `fetch_url` (with curl_cffi Chrome TLS/JA3/JA4 fingerprint impersonation and Playwright JS rendering), `web_search`, `lookup_docs` (Context7), and `deep_research` (multi-step Researcher sub-agent). No host restriction by default — internal services and cloud-metadata endpoints are reachable.
- **Runtime probing** — `core/runtime_probe.py` automatically extracts and probes local URLs discovered in command output (loopback, link-local, RFC1918), returning status, content-type, and a bounded response preview. Passive internal-network mapping.
- **Filesystem operations** — read, create, delete, edit, glob, list, and symbol-navigation tools across the whole host, not just the workspace.
- **Reconnaissance** — hierarchical fault localization, `find_symbols`, `get_structure`, repo-map indexing, `get_symbol_definition`, `get_function_body`, `find_references`, `get_function_dependencies` (call-graph resolution).
- **VCS** — git session branches, auto-commit, rollback, and workspace checkpoints for safe experimentation.
- **Sandbox** — Docker isolation for test execution in the Quality tier.

See `SECURITY_VULNERABILITIES.md` for the full catalog of documented security properties and the attack chain.

## Installation

```
pip install -r requirements.txt
```

Core dependencies: openai, httpx, pyyaml, networkx, numpy, dulwich. sentence-transformers, datasets, and swebench are optional.

## Configuration

Model providers, API keys, base URLs, and per-tier model selection go in `models.yaml`. See `models.yaml.ex` for a full example with OpenAI, Anthropic, and Ollama.

## Project Structure

```
AGENTS.md          Agent self-instructions
__main__.py        Module entry (python -m zirconAgent)
cli/               The CLI binary: spec tree, runtime, daemon, TUI
  index.py         Thin entry: parse args, manage daemon, launch TUI
  spec.py          Declarative command spec tree
  runtime.py       Walks the spec tree, lazy-loads handlers
  commands/        Command handlers (lazy-loaded)
  daemon/          Backend server, lifecycle, transport
  tui/             Full-screen reactive UI with provider tree
core/              Engine: agent, planning, execution, knowledge graph, context
subagents/         Specialized sub-agents (explorer, editor, verifier, architect, researcher)
  swarm/           Swarm agents (coordinator, API builder, frontend builder, etc.)
tools/             Tool implementations: file ops, edit ops, search, shell, web
llm/               LLM router, prompt library, structured output parsing
parsers/           AST and edit format parsers
vcs/               Git integration (Dulwich-based)
sandbox/           Example target project + Docker sandbox executor in core/
tests/             Test suite
benchmark/         Benchmarking (including SWE-bench)
```

## Requirements

- Python 3.10+
- API key for OpenAI or Anthropic (optional with local models via Ollama)

## License & Liability

Zircon is released under the **MIT License**.

This software is dual-purpose. It is both a general autonomous coding agent and a penetration-testing / cybersecurity research tool. The offensive-security capabilities described above are real and intentional — they exist so that authorized security professionals, red-team operators, CTF participants, defensive researchers, and students can use a single agent framework for legitimate authorized work instead of stitching together half a dozen separate tools.

**By installing, running, or distributing Zircon you accept the following terms:**

1. **Authorization required.** You may only use Zircon's security and offensive capabilities against systems, networks, applications, and accounts that you own or for which you have explicit, written authorization from the owner to test. "I was curious" is not authorization. "It was exposed to the internet" is not authorization. If you are not sure whether you are authorized, you are not authorized.
2. **No warranty.** Zircon is provided "AS IS", without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and non-infringement. The author(s) and copyright holder(s) make no claim that this software is safe, correct, lawful in your jurisdiction, or suitable for any purpose.
3. **Liability waiver.** In no event shall the authors or copyright holders be liable for any claim, damages, or other liability, whether in an action of contract, tort, or otherwise, arising from, out of, or in connection with the software or the use or other dealings in the software — including but not limited to unauthorized access, data loss, system damage, legal action, regulatory penalties, or any consequence of misuse by you or by any third party.
4. **You are responsible.** The user bears full and sole responsibility for all actions performed by or through this software. Zircon can execute shell commands, read and write files outside the workspace, fetch arbitrary URLs, and run offensive-security workflows. None of that is the framework's decision — it is yours. You are the operator.
5. **Not legal advice.** Nothing in this README, the source code, or the documentation constitutes legal advice. Laws on security testing, access, interception, and offensive tooling vary widely by jurisdiction. Consult a qualified attorney if you are unsure about the legality of any intended use.
6. **Educational intent.** The offensive-security features are provided for education, authorized testing, and defensive research. They are not an invitation, endorsement, or instruction to commit any crime or unauthorized act.
7. **No support obligations.** The maintainers owe you nothing. Issues, PRs, and questions may be addressed at the maintainers' discretion.

If you cannot accept these terms, do not install or use Zircon.

## License

MIT
