import asyncio
import shutil
import time

import pytest

from zirconAgent.core.fs_state import FilesystemStateTracker
from zirconAgent.tools.base import Tool
from zirconAgent.tools.registry import ToolRegistry


def _write(path, content):
    path.write_text(content, encoding="utf-8")


class TestSnapshotsAndDiff:
    def test_detects_created_file(self, tmp_path):
        tracker = FilesystemStateTracker(tmp_path)
        before = tracker.snapshot()
        _write(tmp_path / "new.txt", "hello")
        after = tracker.snapshot()
        changes = tracker.diff(before, after)
        kinds = {c.path: c.kind for c in changes}
        assert kinds.get("new.txt") == "created"

    def test_detects_modified_file(self, tmp_path):
        tracker = FilesystemStateTracker(tmp_path)
        _write(tmp_path / "a.txt", "short")
        before = tracker.snapshot()
        _write(tmp_path / "a.txt", "a much longer content than before")
        after = tracker.snapshot()
        changes = tracker.diff(before, after)
        kinds = {c.path: c.kind for c in changes}
        assert kinds.get("a.txt") == "modified"

    def test_detects_deleted_file(self, tmp_path):
        tracker = FilesystemStateTracker(tmp_path)
        _write(tmp_path / "gone.txt", "x")
        before = tracker.snapshot()
        (tmp_path / "gone.txt").unlink()
        after = tracker.snapshot()
        changes = tracker.diff(before, after)
        kinds = {c.path: c.kind for c in changes}
        assert kinds.get("gone.txt") == "deleted"

    def test_no_changes(self, tmp_path):
        tracker = FilesystemStateTracker(tmp_path)
        _write(tmp_path / "stable.txt", "same")
        before = tracker.snapshot()
        after = tracker.snapshot()
        assert tracker.diff(before, after) == []

    def test_cumulative_changed_files(self, tmp_path):
        tracker = FilesystemStateTracker(tmp_path)
        before = tracker.snapshot()
        _write(tmp_path / "one.txt", "1")
        tracker.diff(before, tracker.snapshot())
        _write(tmp_path / "two.txt", "2")
        tracker.diff(tracker.snapshot_cached(), tracker.snapshot())
        assert tracker.changed_files() == {"one.txt", "two.txt"}

    def test_reset_clears_state(self, tmp_path):
        tracker = FilesystemStateTracker(tmp_path)
        before = tracker.snapshot()
        _write(tmp_path / "z.txt", "z")
        tracker.diff(before, tracker.snapshot())
        assert tracker.changed_files()
        tracker.reset()
        assert tracker.changed_files() == set()
        assert tracker.last_changes == []

    def test_snapshot_cached_reuses_within_ttl(self, tmp_path):
        tracker = FilesystemStateTracker(tmp_path)
        first = tracker.snapshot()
        # Within TTL the cache is returned by reference.
        assert tracker.snapshot_cached() is first

    def test_skips_zircon_and_pycache_dirs(self, tmp_path):
        tracker = FilesystemStateTracker(tmp_path)
        (tmp_path / ".zircon-code").mkdir()
        _write(tmp_path / ".zircon-code" / "state.json", "{}")
        (tmp_path / "__pycache__").mkdir()
        _write(tmp_path / "__pycache__" / "x.pyc", "bytecode")
        _write(tmp_path / "real.py", "print('hi')")
        snap = tracker.snapshot()
        assert "real.py" in snap
        assert all(".zircon-code" not in p for p in snap)
        assert all("__pycache__" not in p for p in snap)


class TestSurfacing:
    def test_format_changes_note_lists_kinds(self, tmp_path):
        tracker = FilesystemStateTracker(tmp_path)
        from zirconAgent.core.fs_state import FileChange
        changes = [
            FileChange("a.py", "created", 0, 10),
            FileChange("b.py", "modified", 5, 9),
            FileChange("c.py", "deleted", 7, 0),
        ]
        note = tracker.format_changes_note(changes)
        assert "<filesystem_changes>" in note
        assert "created: a.py" in note
        assert "modified: b.py" in note
        assert "deleted: c.py" in note
        assert "</filesystem_changes>" in note

    def test_format_empty(self, tmp_path):
        tracker = FilesystemStateTracker(tmp_path)
        assert tracker.format_changes_note([]) == ""

    def test_note_caps_entries(self, tmp_path):
        tracker = FilesystemStateTracker(tmp_path)
        from zirconAgent.core.fs_state import FileChange
        changes = [FileChange(f"f{i}.py", "modified", i, i + 1) for i in range(30)]
        note = tracker.format_changes_note(changes)
        assert "more)" in note


class TestGitVerify:
    def test_verify_async_noop_without_git(self, tmp_path):
        # tmp_path is not a git repo: verify_async must not spawn a thread.
        tracker = FilesystemStateTracker(tmp_path)
        from zirconAgent.core.fs_state import FileChange
        tracker.verify_async([FileChange("a.py", "modified", 1, 2)])
        # Give any (should-be-none) thread a moment, then assert no confirmation.
        time.sleep(0.05)
        assert tracker.git_confirmed == set()

    def test_verify_with_git_confirms_real_change(self, tmp_path):
        if not shutil.which("git"):
            pytest.skip("git not installed")
        import subprocess
        cwd = str(tmp_path)
        subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=cwd, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=cwd, check=True)
        _write(tmp_path / "tracked.py", "a = 1")
        subprocess.run(["git", "add", "tracked.py"], cwd=cwd, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=cwd, check=True)
        tracker = FilesystemStateTracker(tmp_path, verify_enabled=True)
        # Baseline snapshot after the commit (tracked.py == "a = 1"), then
        # mutate via a real snapshot cycle so the cumulative set is populated.
        before = tracker.snapshot()
        _write(tmp_path / "tracked.py", "a = 2\nb = 3")
        _write(tmp_path / "fresh.py", "new = 1")
        changes = tracker.diff(before, tracker.snapshot())
        confirmed = tracker.verify_with_git(changes)
        assert "tracked.py" in confirmed
        assert "fresh.py" in confirmed
        # Git confirms both, so neither is disqualified from the surfaced set.
        assert "tracked.py" in tracker.changed_files()
        assert "fresh.py" in tracker.changed_files()


class TestRegistryIntegration:
    def test_shell_write_surfaces_actual_changes(self, tmp_path):
        target = tmp_path / "out.log"

        class FakeWriteTool(Tool):
            def __init__(self):
                self._name = "run_command"

            @property
            def name(self) -> str:
                return "run_command"

            @property
            def description(self) -> str:
                return "fake"

            @property
            def schema(self):
                return {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                }

            async def run(self, command="", **kwargs):
                target.write_text("command output\n", encoding="utf-8")
                return "done"

        reg = ToolRegistry()
        reg.register(FakeWriteTool())
        reg.fs_tracker = FilesystemStateTracker(tmp_path)

        result = asyncio.run(
            reg.execute("run_command", {"command": "echo hi > out.log"})
        )
        assert "<filesystem_changes>" in result
        assert "out.log" in result
        assert "out.log" in reg.fs_tracker.changed_files()
