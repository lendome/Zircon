# `core/` — Engine

The core engine implements Zircon's agent lifecycle:

| Module | Purpose |
|--------|---------|
| `agent.py` | Main Agent class — orchestrates task lifecycle, tier switching, streaming |
| `context.py` | Context Manager v3 — tier-aware token budgeting, repo map, working set |
| `planner.py` | PlanGatekeeper + Planner — decides if planning is needed, generates/replans |
| `executor.py` | Tool-loop executor — runs LLM+tool interactions with loop detection |
| `types.py` | All dataclasses and enums (Plan, TierConfig, SwarmResult, etc.) |
| `kg_memory.py` | Knowledge Graph — SQLite + NetworkX for dependency-aware context |
| `project_classifier.py` | LLM-based + heuristic project classification with adaptive prompts |
| `session.py` | Session tracking and journaling per task |
| `config.py` | Configuration loader from `models.yaml` |
| `constants.py` | `.zircon-code` directory structure constants |
| `distiller.py` | Observation masking and history distillation |
| `embeddings.py` | Local sentence-transformers embedder |
| `swarm_orchestrator.py` | Parallel agent execution with topological layers |
| `swarm_plan_builder.py` | Decomposes tasks into parallel swarm tracks |
| `artifact_registry.py` | Inter-agent artifact sharing for swarm mode |
| `tool_search.py` | Tool description optimizer — selects relevant tool schemas |
| `git_context.py` | Git convention analyzer (quality tier only) |
| `loop_detector.py` | Anti-loop detection — spots repeated failed patterns |
| `shell_env.py` | Shell detection (Git Bash → pwsh → cmd) + unified command capture — see [environment-discovery.md](environment-discovery.md) |
| `profiling.py` | Profiler wrapping/parsing (cProfile, node --cpu-prof, go pprof) |
| `diff_display.py` | Diff rendering for edit results |
| `edit_engine.py` | Edit operation engine (SEARCH/REPLACE, line-range) |
| `logging_config.py` | Structured logging configuration |
| `task_manager.py` | Fire-and-forget background task runner |
| `agent_writer.py` | Auto-generates AGENTS.md from project context (quality tier) |