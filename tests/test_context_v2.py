import pytest
from pathlib import Path

from zirconAgent.core.context import ContextManager, estimate_tokens
from zirconAgent.core.context_guard import MAX_INGRESS_TOKENS, TRUNCATED_PREVIEW_TOKENS, guard_messages
from zirconAgent.core.types import Plan, PlanStep


@pytest.fixture
def ctx(tmp_path):
    return ContextManager(tmp_path, context_window=32000, safety_margin=400)


class TestBuildRepoMap:
    def test_builds_map(self, ctx, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("def hello(): pass\n\nclass Foo:\n    def bar(self): pass\n")
        ctx.build_repo_map()
        assert ctx.repo_map_built
        assert "src/app.py" in ctx.repo_map
        assert "hello" in ctx.repo_map_text
        assert "Foo" in ctx.repo_map_text

    def test_skips_hidden_dirs(self, ctx, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("def secret(): pass\n")
        (tmp_path / "visible.py").write_text("def visible(): pass\n")
        ctx.build_repo_map()
        assert "visible" in ctx.repo_map_text
        assert "secret" not in ctx.repo_map_text

    def test_symbol_index(self, ctx, tmp_path):
        (tmp_path / "app.py").write_text("def my_func(): pass\n")
        ctx.build_repo_map()
        results = ctx.find_symbol("my_func")
        assert len(results) >= 1
        assert "app.py" in results[0][0]

    def test_skips_files_over_40_mib_during_automatic_indexing(self, ctx, tmp_path):
        oversized = tmp_path / "generated.py"
        with oversized.open("wb") as file:
            file.truncate(ctx.MAX_AUTO_INDEX_FILE_BYTES + 1)
        (tmp_path / "small.py").write_text("def included(): pass\n")

        ctx.build_repo_map()

        assert "generated.py" not in ctx.repo_map
        assert "small.py" in ctx.repo_map


class TestEpisodicMemory:
    def test_save_and_load(self, ctx, tmp_path):
        ctx.save_episodic_memory("User prefers pytest with -v flag")

        ctx2 = ContextManager(tmp_path, context_window=32000, safety_margin=400)
        assert "pytest" in ctx2.episodic_memory[0]

    def test_persists_to_disk(self, ctx, tmp_path):
        ctx.save_episodic_memory("learning 1")
        path = tmp_path / ".zircon-code" / "learnings.json"
        assert path.exists()


class TestProjectMemory:
    def test_loads_project_memory_and_agents_at_start(self, tmp_path):
        (tmp_path / "PROJECT_MEMORY.md").write_text("Run `pytest -q` before finishing.")
        (tmp_path / "AGENTS.md").write_text("Use dataclasses for DTOs.")

        context = ContextManager(tmp_path, context_window=32000, safety_margin=400)
        messages = context.build_messages("system prompt")
        content = "\n".join(str(message.get("content", "")) for message in messages)

        assert "<project_memory>" in content
        assert "pytest -q" in content
        assert "dataclasses" in content

    def test_reload_picks_up_changes_between_sessions(self, tmp_path):
        memory = tmp_path / "PROJECT_MEMORY.md"
        memory.write_text("old guidance")
        context = ContextManager(tmp_path)
        memory.write_text("new guidance")

        context.reload_project_memory()

        assert "new guidance" in context.project_memory
        assert "old guidance" not in context.project_memory


class TestDistillObservation:
    def test_masks_by_focus(self, ctx):
        data = "cats are great\ndogs are ok\ncats again\nmore cats\ndogs too"
        result = ctx.distill_observation(data, "cats")
        assert "cats" in result

    def test_distills_without_focus(self, ctx):
        data = "x" * 5000
        result = ctx.distill_observation(data)
        assert len(result) < len(data)


class TestBuildMessages:
    def test_repo_map_included(self, ctx, tmp_path):
        (tmp_path / "app.py").write_text("def hello(): pass\n")
        ctx.build_repo_map()
        ctx.set_task("test")
        messages = ctx.build_messages("system prompt")
        all_content = " ".join(m["content"] for m in messages)
        assert "repo_map" in all_content

    def test_episodic_memory_included(self, ctx, tmp_path):
        ctx.save_episodic_memory("Always use type hints")
        ctx.set_task("test")
        messages = ctx.build_messages("system prompt")
        all_content = " ".join(m["content"] for m in messages)
        assert "type hints" in all_content

    def test_kg_context_included_when_available(self, tmp_path):
        from zirconAgent.core.kg_memory import KnowledgeGraphMemory
        kg = KnowledgeGraphMemory(str(tmp_path))
        kg.add_node("file:auth.py", "file", {"path": "auth.py"})
        kg.add_node("function:auth.py:login", "function", {"name": "login", "file": "auth.py", "line": 5})
        kg.add_edge("file:auth.py", "function:auth.py:login", "contains")

        ctx = ContextManager(tmp_path, context_window=32000, safety_margin=400, kg_memory=kg)
        ctx.set_task("fix login in auth.py")
        messages = ctx.build_messages("system prompt")
        all_content = " ".join(m["content"] for m in messages)
        assert "auth.py" in all_content

    def test_token_budget_respected(self, tmp_path):
        ctx = ContextManager(tmp_path, context_window=500, safety_margin=50)
        ctx.set_task("test")
        ctx.add_file_to_working_set("big.py", "x" * 10000)
        messages = ctx.build_messages("system prompt")
        total = sum(estimate_tokens(m["content"]) for m in messages)
        assert total <= 500

    def test_oversized_file_context_is_replaced_with_navigable_preview(self, ctx):
        ctx.add_file_to_working_set("huge.py", "x" * ((MAX_INGRESS_TOKENS + 1) * 4))

        content = ctx.working_set["huge.py"]

        assert "context guard" in content
        assert "huge.py" in content
        assert "narrower line range" in content
        assert estimate_tokens(content) <= TRUNCATED_PREVIEW_TOKENS + 100

    def test_guard_messages_limits_oversized_tool_result(self):
        messages = [{"role": "tool", "content": "x" * ((MAX_INGRESS_TOKENS + 1) * 4)}]

        guarded = guard_messages(messages)

        assert "context guard: tool result" in guarded[0]["content"]
        assert estimate_tokens(guarded[0]["content"]) <= TRUNCATED_PREVIEW_TOKENS + 100
        assert len(messages[0]["content"]) > len(guarded[0]["content"])


class TestToolExchangeDistillation:
    def test_long_result_distilled(self, ctx):
        long_result = "line\n" * 500
        ctx.add_tool_exchange("run_command", {"command": "test"}, long_result, distill=True)
        assert len(ctx.history[-1]["content"]) < len(long_result)

    def test_short_result_unchanged(self, ctx):
        short = "just a few chars"
        ctx.add_tool_exchange("read_file", {"path": "x"}, short, distill=True)
        assert ctx.history[-1]["content"] == short
