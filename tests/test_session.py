import json
import pytest
from pathlib import Path

from zirconAgent.core.session import AdmissionConflictError, SessionManager, Session
from zirconAgent.core.types import TaskStatus


class TestSession:
    def test_auto_id(self):
        s = Session(task="test")
        assert s.id
        assert len(s.id) == 12

    def test_custom_id(self):
        s = Session(id="abc123", task="test")
        assert s.id == "abc123"


class TestSessionManager:
    @pytest.fixture
    def manager(self, tmp_path):
        return SessionManager(str(tmp_path))

    def test_start(self, manager):
        s = manager.start("fix the bug")
        assert s.status == TaskStatus.RUNNING
        assert s.task == "fix the bug"
        assert s.id

    def test_session_dir_created(self, manager, tmp_path):
        manager.start("test")
        assert (tmp_path / ".zircon-code" / "sessions").exists()

    def test_journal_created(self, manager):
        s = manager.start("test task")
        journal = Path(manager.session_dir) / s.id / "journal.jsonl"
        assert journal.exists()
        lines = journal.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["type"] == "session_start"

    def test_append_journal(self, manager):
        s = manager.start("test")
        manager.append_journal("tool_call", {"tool": "read_file", "path": "a.py"})
        journal = Path(manager.session_dir) / s.id / "journal.jsonl"
        lines = journal.read_text().strip().splitlines()
        assert len(lines) == 2
        entry = json.loads(lines[1])
        assert entry["type"] == "tool_call"
        assert entry["payload"]["tool"] == "read_file"

    def test_close(self, manager):
        s = manager.start("test")
        manager.close(TaskStatus.COMPLETED, tokens_used=500)
        assert s.status == TaskStatus.COMPLETED
        assert s.tokens_used == 500
        assert s.finished_at

    def test_set_status(self, manager):
        s = manager.start("test")
        manager.set_status(TaskStatus.AWAITING_INPUT)
        assert s.status == TaskStatus.AWAITING_INPUT

    def test_track_file(self, manager):
        manager.start("test")
        manager.track_file("a.py")
        manager.track_file("b.py")
        manager.track_file("a.py")
        assert len(manager.current.files_modified) == 2

    def test_manifest_persisted(self, manager):
        s = manager.start("my task")
        manifest = Path(manager.session_dir) / s.id / "manifest.json"
        assert manifest.exists()
        data = json.loads(manifest.read_text())
        assert data["task"] == "my task"
        assert data["status"] == TaskStatus.RUNNING
        assert data["schema_version"] == 2

    def test_messages_round_trip_atomically(self, manager):
        session = manager.start("test")
        messages = [{"role": "user", "content": "hello"}]

        manager.save_messages(messages)

        assert manager.load_messages(session.id) == messages
        assert not (Path(manager.session_dir) / session.id / "messages.json.tmp").exists()

    def test_append_messages_preserves_exact_tool_output(self, manager):
        session = manager.start("test")
        exact_output = "x" * 20_000

        manager.append_messages([{"role": "user", "content": "inspect"}])
        manager.append_messages([{
            "role": "tool",
            "tool_call_id": "call_1",
            "content": exact_output,
        }])

        persisted = manager.load_messages(session.id)
        assert persisted[1]["content"] == exact_output

    @pytest.mark.parametrize("session_id", ["../outside", "nested/session", "", "."])
    def test_invalid_session_ids_are_rejected(self, manager, session_id):
        assert manager.load_session(session_id) is None
        assert manager.load_messages(session_id) == []

    def test_close_updates_manifest(self, manager):
        s = manager.start("test")
        manager.close(TaskStatus.COMPLETED)
        manifest = Path(manager.session_dir) / s.id / "manifest.json"
        data = json.loads(manifest.read_text())
        assert data["status"] == TaskStatus.COMPLETED

    def test_list_sessions(self, manager):
        first = manager.start("task 1")
        second = manager.start("task 2")
        sessions = manager.list_sessions()
        assert len(sessions) == 2
        assert [session["id"] for session in sessions] == [second.id, first.id]

    def test_current_property(self, manager):
        assert manager.current is None
        s = manager.start("test")
        assert manager.current == s

    def test_admission_retries_are_reconciled_exactly(self, manager):
        manager.start("test")
        first = manager.admit_prompt("fix it", admission_id="request-1", delivery="queue")
        retry = manager.admit_prompt("fix it", admission_id="request-1", delivery="queue")
        assert retry == first
        assert len(manager.list_admissions()) == 1

    def test_admission_id_conflict_rejects_different_input(self, manager):
        manager.start("test")
        manager.admit_prompt("fix it", admission_id="request-1")
        with pytest.raises(AdmissionConflictError):
            manager.admit_prompt("fix something else", admission_id="request-1")

    def test_promote_steers_in_order_and_one_queued_item_when_idle(self, manager):
        manager.start("test")
        manager.admit_prompt("queue first", admission_id="q1", delivery="queue")
        manager.admit_prompt("steer one", admission_id="s1", delivery="steer")
        manager.admit_prompt("steer two", admission_id="s2", delivery="steer")
        manager.admit_prompt("queue second", admission_id="q2", delivery="queue")

        steers = manager.promote_prompts()
        assert [item.content for item in steers] == ["steer one", "steer two"]
        queued = manager.promote_prompts(include_queued=True)
        assert [item.content for item in queued] == ["queue first"]
        assert [item.status for item in manager.list_admissions()] == ["promoted", "promoted", "promoted", "pending"]

    def test_live_drain_is_single_owner(self, manager):
        manager.start("test")
        assert manager.begin_drain()
        assert not manager.begin_drain()
        manager.end_drain()
        assert manager.begin_drain()

    def test_resume_history_compacts_oversized_tool_result(self, manager):
        content = "start\n" + ("x" * 20_000) + "\nend"
        restored = manager.to_history_messages([{
            "role": "tool",
            "tool_call_id": "call_1",
            "content": content,
        }])

        assert restored[0]["tool_call_id"] == "call_1"
        assert restored[0]["content"].startswith("start\n")
        assert restored[0]["content"].endswith("\nend")
        assert "[session resume:" in restored[0]["content"]
        assert len(restored[0]["content"]) <= 12_100

    def test_resume_history_preserves_small_tool_result(self, manager):
        message = {"role": "tool", "tool_call_id": "call_1", "content": "short result"}

        assert manager.to_history_messages([message]) == [message]

    def test_reopen_keeps_session_id_and_clears_finished_at(self, manager):
        session = manager.start("test")
        manager.close(TaskStatus.COMPLETED)

        manager.reopen()

        assert manager.current is session
        assert manager.current.status == TaskStatus.RUNNING
        assert manager.current.finished_at == ""
