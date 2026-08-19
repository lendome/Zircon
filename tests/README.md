# `tests/` — Test Suite

Test framework: `pytest` with async support.

| File | Tests |
|------|-------|
| `test_ast_parser.py` | AST symbol extraction |
| `test_benchmark.py` | Benchmark runner |
| `test_config.py` | Config / model loading |
| `test_context.py`, `test_context_v2.py` | ContextManager budget & assembly |
| `test_diff_display.py` | Diff rendering |
| `test_distiller.py` | Observation distillation |
| `test_e2e.py` | End-to-end agent flows |
| `test_edit_engine.py` | Edit operation engine |
| `test_edit_ops.py`, `test_edit_parser.py` | Edit tool + format parsing |
| `test_executor.py`, `test_executor_stream.py` | Tool-loop executor |
| `test_file_ops.py` | File read/create/delete tools |
| `test_git.py`, `test_git_context.py`, `test_git_dulwich.py` | Git integration |
| `test_kg_memory.py` | Knowledge Graph persistence + query |
| `test_logging_config.py` | Logging setup |
| `test_loop_detector.py` | Anti-loop detection |
| `test_planner.py` | PlanGatekeeper + Planner |
| `test_registry.py` | Tool registry registration/deregistration |
| `test_router.py` | LLM router (multi-provider) |
| `test_search_ops.py` | Grep/find/glob tools |
| `test_session.py` | Session tracking |
| `test_shell_ops.py` | Shell command execution |
| `test_structured.py` | Structured output parsing |
| `test_subagents.py` | Explorer/Editor/Verifier sub-agents |
| `test_swarm.py` | Swarm orchestrator |
| `test_tool_search.py` | Tool description optimizer |
| `test_types.py` | Type definitions |

**Run:** `pytest tests/ -x -v`