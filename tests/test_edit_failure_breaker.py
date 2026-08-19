import pytest

from zirconAgent.tools.registry import EditFailureBreaker, ToolRegistry
from zirconAgent.tools.edit_ops import EditFileTool


def _failing_args():
    return {"path": "a.py", "search": "def missing():", "replace": "def fixed():"}


class TestEditFailureBreaker:
    def test_first_failure_never_intercepted(self):
        breaker = EditFailureBreaker()
        breaker.record("edit_file", _failing_args(), "Edit failed: search text not found")
        assert breaker.check("edit_file", _failing_args()) is None

    def test_intercepted_after_two_failures(self):
        breaker = EditFailureBreaker()
        breaker.record("edit_file", _failing_args(), "Edit failed: search text not found")
        breaker.record("edit_file", _failing_args(), "Edit failed: search text not found")
        # Two recorded failures -> the NEXT identical attempt is intercepted.
        msg = breaker.check("edit_file", _failing_args())
        assert msg is not None
        assert msg.startswith("CIRCUIT-BREAKER:")
        assert "Re-read the target region" in msg

    def test_different_search_passes(self):
        breaker = EditFailureBreaker()
        breaker.record("edit_file", _failing_args(), "Edit failed: search text not found")
        breaker.record("edit_file", _failing_args(), "Edit failed: search text not found")
        other = {"path": "a.py", "search": "def other():", "replace": "def fixed():"}
        assert breaker.check("edit_file", other) is None

    def test_success_clears_entry(self):
        breaker = EditFailureBreaker()
        breaker.record("edit_file", _failing_args(), "Edit failed: search text not found")
        breaker.record("edit_file", _failing_args(), "Applied search/replace to a.py (verified=True)")
        assert breaker.check("edit_file", _failing_args()) is None

    def test_mutation_clears_all_entries(self):
        breaker = EditFailureBreaker()
        breaker.record("edit_file", _failing_args(), "Edit failed: search text not found")
        breaker.record("edit_file", _failing_args(), "Edit failed: search text not found")
        breaker.note_mutation()  # some other edit fixed the file
        assert breaker.check("edit_file", _failing_args()) is None

    def test_edit_lines_keyed_separately(self):
        breaker = EditFailureBreaker()
        breaker.record("edit_file", _failing_args(), "Edit failed: search text not found")
        breaker.record("edit_file", _failing_args(), "Edit failed: search text not found")
        lines_args = {"path": "a.py", "start": 1, "end": 5, "content": "x"}
        assert breaker.check("edit_lines", lines_args) is None


class TestEditBreakerRegistryIntegration:
    @pytest.mark.asyncio
    async def test_identical_failing_edit_intercepted(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        registry = ToolRegistry()
        registry.register(EditFileTool(str(tmp_path)))
        args = _failing_args()
        first = await registry.execute("edit_file", args)
        assert first.startswith("Edit failed:"), first
        second = await registry.execute("edit_file", args)
        assert second.startswith("Edit failed:"), second
        third = await registry.execute("edit_file", args)
        assert third.startswith("CIRCUIT-BREAKER:"), third

    @pytest.mark.asyncio
    async def test_successful_edit_clears_state(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        registry = ToolRegistry()
        registry.register(EditFileTool(str(tmp_path)))
        fail_args = _failing_args()
        await registry.execute("edit_file", fail_args)
        ok_args = {"path": "a.py", "search": "x = 1", "replace": "x = 2"}
        applied = await registry.execute("edit_file", ok_args)
        assert applied.startswith("Applied"), applied
        # The earlier failing edit's state was cleared by the mutation.
        retry = await registry.execute("edit_file", fail_args)
        assert retry.startswith("Edit failed:"), retry

    @pytest.mark.asyncio
    async def test_breaker_disabled_flag(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        registry = ToolRegistry()
        registry.edit_failure_breaker_enabled = False
        registry.register(EditFileTool(str(tmp_path)))
        args = _failing_args()
        await registry.execute("edit_file", args)
        await registry.execute("edit_file", args)
        third = await registry.execute("edit_file", args)
        assert third.startswith("Edit failed:"), third
