# `subagents/` — Specialised Sub-Agents

Sub-agents are focused LLM agents that each handle one phase of the coding pipeline.

| Agent | Prompt | Tools | Purpose |
|-------|--------|-------|---------|
| `ExplorerSubAgent` | `SYSTEM_EXPLORER` | read, grep, find, glob, list | Answer questions, map the codebase |
| `EditorSubAgent` | `SYSTEM_EDITOR` | edit, create, delete | Apply precise code changes |
| `VerifierSubAgent` | `SYSTEM_VERIFIER` | run_command, read, glob | Run tests, lint, verify correctness |
| `ArchitectSubAgent` | `SYSTEM_ARCHITECT` | read, grep, find, glob | Produce implementation plans |
| `ResearcherSubAgent` | `SYSTEM_RESEARCHER` | fetch_url, run_command | Look up external docs/APIs |

**Swarm mode** (`subagents/swarm/`) adds domain-specific agents for parallel UI + API + backend work.