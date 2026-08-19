import pytest
from pathlib import Path

from zirconAgent.core.context import ContextManager, estimate_tokens
from zirconAgent.core.types import Plan, PlanStep


@pytest.fixture
def ctx(tmp_path):
    return ContextManager(tmp_path, context_window=32000, safety_margin=400)


class TestEstimateTokens:
    def test_short_string(self):
        assert estimate_tokens("hello") == 1  # 5//4=1

    def test_empty_string(self):
        assert estimate_tokens("") == 1

    def test_long_string(self):
        text = "a" * 1000
        assert estimate_tokens(text) == 250


class TestTaskManagement:
    def test_set_task(self, ctx):
        ctx.set_task("fix the bug in app.py")
        assert ctx.task == "fix the bug in app.py"

    def test_set_plan(self, ctx):
        plan = Plan(steps=[PlanStep(index=0, description="do thing", action="edit")])
        ctx.set_plan(plan)
        assert ctx.plan is not None
        assert len(ctx.plan.steps) == 1

    def test_set_current_step(self, ctx):
        step = PlanStep(index=0, description="test", action="explore")
        ctx.set_current_step(step)
        assert ctx.current_step == step

    def test_task_attaches_referenced_file_content(self, ctx):
        (ctx.repo_path / "README.md").write_text("project instructions", encoding="utf-8")

        ctx.set_task("Review @README.md")

        assert "Review @README.md" in ctx.task
        assert '<prompt_path_file path="README.md">' in ctx.task
        assert "project instructions" in ctx.task

    def test_task_attaches_referenced_directory_listing(self, ctx):
        source = ctx.repo_path / "src"
        source.mkdir()
        (source / "main.py").write_text("pass", encoding="utf-8")
        (source / "nested").mkdir()

        ctx.set_task("Inspect @src/")

        assert '<prompt_path_directory path="src">' in ctx.task
        assert "main.py" in ctx.task
        assert "nested/" in ctx.task

    def test_task_ignores_paths_outside_workspace(self, ctx, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("do not expose", encoding="utf-8")

        ctx.set_task(f"Inspect @{outside}")

        assert "do not expose" not in ctx.task

    def test_task_truncates_referenced_file_content(self, ctx):
        (ctx.repo_path / "large.txt").write_text("x" * 40_000, encoding="utf-8")

        ctx.set_task("Inspect @large.txt")

        assert "[context guard: prompt path `large.txt`" in ctx.task


class TestWorkingSet:
    def test_add_file(self, ctx):
        ctx.add_file_to_working_set("app.py", "print('hello')")
        assert "app.py" in ctx.working_set

    def test_lru_eviction(self, tmp_path):
        c = ContextManager(tmp_path, context_window=32000, safety_margin=400)
        c.working_set = type(c.working_set)(max_size=3)
        for i in range(5):
            c.add_file_to_working_set(f"file_{i}.py", f"content {i}")
        assert len(c.working_set) == 3
        assert "file_0.py" not in c.working_set
        assert "file_4.py" in c.working_set

    def test_mark_modified(self, ctx):
        ctx.mark_modified("app.py")
        assert "app.py" in ctx.modified_files

    def test_files_modified_list(self, ctx):
        ctx.mark_modified("a.py")
        ctx.mark_modified("b.py")
        assert "a.py" in ctx.files_modified_list
        assert "b.py" in ctx.files_modified_list

    def test_files_modified_list_empty(self, ctx):
        assert ctx.files_modified_list == "No files modified"


class TestNotes:
    def test_add_note(self, ctx):
        ctx.add_note("discovered: uses Flask")
        assert len(ctx.session_notes) == 1

    def test_working_set_summary(self, ctx):
        ctx.add_file_to_working_set("a.py", "content")
        summary = ctx.working_set_summary()
        assert "a.py" in summary

    def test_working_set_summary_empty(self, ctx):
        assert "No files" in ctx.working_set_summary()


class TestBuildMessages:
    def test_system_prompt_included(self, ctx):
        ctx.set_task("test")
        messages = ctx.build_messages("You are a helper.")
        assert messages[0]["role"] == "system"
        assert "helper" in messages[0]["content"]

    def test_task_included(self, ctx):
        ctx.set_task("fix the login bug")
        messages = ctx.build_messages("system")
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert any("fix the login bug" in m["content"] for m in user_msgs)

    def test_plan_included(self, ctx):
        ctx.set_task("test")
        ctx.set_plan(Plan(steps=[
            PlanStep(index=0, description="explore code", action="explore"),
            PlanStep(index=1, description="make edit", action="edit"),
        ]))
        messages = ctx.build_messages("system")
        system_msgs = [m for m in messages if m["role"] == "system"]
        plan_msgs = [m for m in system_msgs if "<plan>" in m["content"]]
        assert len(plan_msgs) == 1
        assert "explore code" in plan_msgs[0]["content"]

    def test_working_set_files_included(self, ctx):
        ctx.set_task("test")
        ctx.add_file_to_working_set("app.py", "print('hello world')")
        messages = ctx.build_messages("system")
        all_content = " ".join(m["content"] for m in messages)
        assert "app.py" in all_content
        assert "hello world" in all_content

    def test_modified_files_prioritized(self, ctx):
        ctx.set_task("test")
        ctx.add_file_to_working_set("regular.py", "x" * 20000)
        ctx.mark_modified("important.py")
        ctx.add_file_to_working_set("important.py", "critical content")
        messages = ctx.build_messages("system")
        all_content = " ".join(m["content"] for m in messages)
        assert "important.py" in all_content

    def test_notes_included(self, ctx):
        ctx.set_task("test")
        ctx.add_note("key finding: database uses SQLite")
        messages = ctx.build_messages("system")
        all_content = " ".join(m["content"] for m in messages)
        assert "SQLite" in all_content


class TestHistory:
    def test_add_tool_exchange(self, ctx):
        ctx.add_tool_exchange("read_file", {"path": "a.py"}, "file contents here")
        assert len(ctx.history) == 2

    def test_add_assistant_message(self, ctx):
        ctx.add_assistant_message("I read the file")
        assert len(ctx.history) == 1
        assert ctx.history[0]["role"] == "assistant"

    def test_clear_history(self, ctx):
        ctx.add_assistant_message("msg")
        ctx.clear_history()
        assert len(ctx.history) == 0

    def test_promoted_inputs_are_added_once_in_order(self, ctx):
        ctx.add_promoted_inputs(["first input", "second input"])
        assert [message["content"] for message in ctx.history] == ["first input", "second input"]

    @pytest.mark.asyncio
    async def test_compaction_ignores_none_content(self, ctx):
        class Router:
            async def generate(self, **kwargs):
                return type("Response", (), {"content": "summary"})()

        ctx.tier.history_compact_threshold = 1
        ctx.tier.history_keep_exchanges = 1
        ctx.history.extend([
            {"role": "assistant", "content": None},
            {"role": "user", "content": "older message"},
            {"role": "assistant", "content": "recent message"},
        ])

        await ctx.compact_history(Router())

        assert ctx.history[1]["content"] == "<history_summary>\nsummary\n</history_summary>"


class TestTokenBudget:
    def test_messages_fit_budget(self, tmp_path):
        c = ContextManager(tmp_path, context_window=1000, safety_margin=100)
        c.set_task("test")
        messages = c.build_messages("system prompt here")
        total = sum(estimate_tokens(m["content"]) for m in messages)
        assert total <= 1000

    def test_truncation_on_large_files(self, tmp_path):
        c = ContextManager(tmp_path, context_window=500, safety_margin=100)
        c.set_task("test")
        c.add_file_to_working_set("big.py", "x" * 10000)
        messages = c.build_messages("system")
        total = sum(estimate_tokens(m["content"]) for m in messages)
        assert total <= 500
