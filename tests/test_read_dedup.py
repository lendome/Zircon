import pytest

from zirconAgent.tools.registry import ReadDeduplicator, ToolRegistry
from zirconAgent.tools.file_ops import CreateFileTool, ReadFileTool


class TestReadDeduplicator:
    def test_first_call_never_intercepted(self):
        dedup = ReadDeduplicator()
        assert dedup.check("read_file", {"path": "a.py", "start": 1, "end": 20}) is None

    def test_exact_repeat_intercepted(self):
        dedup = ReadDeduplicator()
        args = {"path": "a.py", "start": 1, "end": 20}
        dedup.check("read_file", args)
        dedup.record("read_file", args, "file contents here")
        msg = dedup.check("read_file", args)
        assert msg is not None
        assert msg.startswith("SYSTEM ERROR (dedup):")
        assert "call #1" in msg
        assert "Do not repeat tool calls" in msg

    def test_different_args_pass(self):
        dedup = ReadDeduplicator()
        dedup.check("read_file", {"path": "a.py", "start": 1, "end": 20})
        dedup.record("read_file", {"path": "a.py", "start": 1, "end": 20}, "contents")
        # Different line range is a different call.
        assert dedup.check("read_file", {"path": "a.py", "start": 21, "end": 40}) is None
        # Different file is a different call.
        assert dedup.check("read_file", {"path": "b.py", "start": 1, "end": 20}) is None
        # Different tool is a different call.
        assert dedup.check("grep_code", {"pattern": "a.py", "start": 1, "end": 20}) is None

    def test_whitespace_variation_still_deduped(self):
        dedup = ReadDeduplicator()
        dedup.check("grep_code", {"pattern": "def foo"})
        dedup.record("grep_code", {"pattern": "def foo"}, "hits")
        msg = dedup.check("grep_code", {"pattern": "  def   foo  "})
        assert msg is not None

    def test_mutation_allows_reread(self):
        dedup = ReadDeduplicator()
        args = {"path": "a.py"}
        dedup.check("read_file", args)
        dedup.record("read_file", args, "old contents")
        dedup.note_mutation()  # an edit succeeded — file may have changed
        assert dedup.check("read_file", args) is None
        # After the allowed re-read, the entry is refreshed at the new epoch.
        dedup.record("read_file", args, "new contents")
        assert dedup.check("read_file", args) is not None

    def test_failed_read_not_recorded(self):
        dedup = ReadDeduplicator()
        args = {"path": "missing.py"}
        dedup.check("read_file", args)
        dedup.record("read_file", args, "Error: file not found: missing.py")
        # Retrying an error must always be allowed.
        assert dedup.check("read_file", args) is None


class TestRegistryDedupIntegration:
    @pytest.mark.asyncio
    async def test_duplicate_read_intercepted(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n" * 50)
        registry = ToolRegistry()
        registry.register(ReadFileTool(str(tmp_path)))
        args = {"path": "a.py", "start": 1, "end": 10}
        first = await registry.execute("read_file", args)
        assert "x = 1" in first
        second = await registry.execute("read_file", args)
        assert second.startswith("SYSTEM ERROR (dedup):"), second
        assert "call #1" in second

    @pytest.mark.asyncio
    async def test_reread_allowed_after_mutation(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        registry = ToolRegistry()
        registry.register_all([ReadFileTool(str(tmp_path)), CreateFileTool(str(tmp_path))])
        args = {"path": "a.py"}
        first = await registry.execute("read_file", args)
        assert "x = 1" in first
        # A successful mutation bumps the epoch — re-reads must execute.
        created = await registry.execute("create_file", {"path": "b.py", "content": "y = 2\n"})
        assert not created.startswith("Error")
        second = await registry.execute("read_file", args)
        assert not second.startswith("SYSTEM ERROR (dedup):"), second
        assert "x = 1" in second

    @pytest.mark.asyncio
    async def test_dedup_disabled_flag(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        registry = ToolRegistry()
        registry.read_dedup_enabled = False
        registry.register(ReadFileTool(str(tmp_path)))
        args = {"path": "a.py"}
        await registry.execute("read_file", args)
        second = await registry.execute("read_file", args)
        assert not second.startswith("SYSTEM ERROR (dedup):")

    @pytest.mark.asyncio
    async def test_intercept_not_recorded_as_success(self, tmp_path):
        # An intercepted call must not refresh the entry, so a third repeat
        # still reports the ORIGINAL call number.
        (tmp_path / "a.py").write_text("x = 1\n")
        registry = ToolRegistry()
        registry.register(ReadFileTool(str(tmp_path)))
        args = {"path": "a.py"}
        await registry.execute("read_file", args)
        second = await registry.execute("read_file", args)
        third = await registry.execute("read_file", args)
        assert second.startswith("SYSTEM ERROR (dedup):")
        assert third.startswith("SYSTEM ERROR (dedup):")
        assert "call #1" in third
