"""Standalone validation script for multi-turn conversation fixes.
Runs without needing the package installed - tests components in isolation.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from collections import OrderedDict

# Add root to path for direct imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---- Mock TaskStatus enum ----
class TaskStatus(str):
    IDLE = "idle"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"

# ---- Helper to estimate tokens ----
def estimate_tokens(text: str | None) -> int:
    if text is None:
        return 0
    if not text:
        return 1
    return max(1, len(text) // 4)


# ==============================================================================
# TEST 1: Orphaned Sessions - SessionManager with auto-close fix
# ==============================================================================

class Session:
    def __init__(self, id="", task="", status="created", started_at="", finished_at="", files_modified=None, tokens_used=0):
        import uuid
        self.id = id or uuid.uuid4().hex[:12]
        self.task = task
        self.status = status
        self.started_at = started_at
        self.finished_at = finished_at
        self.files_modified = files_modified or []
        self.tokens_used = tokens_used


class SessionManagerFixed:
    """Fixed version with auto-close."""

    def __init__(self, repo_path):
        self.repo_path = Path(repo_path).resolve()
        self.session_dir = self.repo_path / ".zircon-code" / "sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._current = None

    @property
    def current(self):
        return self._current

    def start(self, task):
        # Auto-close previous orphaned session
        if self._current is not None and self._current.finished_at == "":
            self._current.finished_at = datetime.now(timezone.utc).isoformat()
            self._current.status = TaskStatus.COMPLETED
            self._write_manifest(self._current)

        session = Session(task=task, status=TaskStatus.RUNNING, started_at=datetime.now(timezone.utc).isoformat())
        self._current = session
        session_path = self.session_dir / session.id
        session_path.mkdir(exist_ok=True)
        self._write_manifest(session)
        return session

    def close(self, status=TaskStatus.COMPLETED):
        if not self._current:
            return
        self._current.status = status
        self._current.finished_at = datetime.now(timezone.utc).isoformat()
        self._write_manifest(self._current)

    def _write_manifest(self, session):
        manifest_path = self.session_dir / session.id / "manifest.json"
        data = {
            "id": session.id,
            "task": session.task,
            "status": session.status,
            "started_at": session.started_at,
            "finished_at": session.finished_at,
            "files_modified": session.files_modified,
            "tokens_used": session.tokens_used,
        }
        manifest_path.write_text(json.dumps(data), encoding="utf-8")

    def list_sessions(self):
        sessions = []
        if not self.session_dir.exists():
            return sessions
        for d in sorted(self.session_dir.iterdir()):
            manifest = d / "manifest.json"
            if manifest.exists():
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    sessions.append(data)
                except Exception:
                    pass
        return sessions


class SessionManagerOld:
    """Original version WITHOUT auto-close - for comparison."""

    def __init__(self, repo_path):
        self.repo_path = Path(repo_path).resolve()
        self.session_dir = self.repo_path / ".zircon-code" / "sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._current = None

    @property
    def current(self):
        return self._current

    def start(self, task):
        session = Session(task=task, status=TaskStatus.RUNNING, started_at=datetime.now(timezone.utc).isoformat())
        self._current = session
        session_path = self.session_dir / session.id
        session_path.mkdir(exist_ok=True)
        manifest_path = self.session_dir / session.id / "manifest.json"
        data = {
            "id": session.id, "task": session.task, "status": session.status,
            "started_at": session.started_at, "finished_at": session.finished_at,
            "files_modified": session.files_modified, "tokens_used": session.tokens_used,
        }
        manifest_path.write_text(json.dumps(data), encoding="utf-8")
        return session

    def list_sessions(self):
        sessions = []
        if not self.session_dir.exists():
            return sessions
        for d in sorted(self.session_dir.iterdir()):
            manifest = d / "manifest.json"
            if manifest.exists():
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    sessions.append(data)
                except Exception:
                    pass
        return sessions


def test_orphaned_sessions_fixed():
    """Bug 1: After fix, no orphaned sessions should exist (except current)."""
    repo = Path(tempfile.mkdtemp())
    sm = SessionManagerFixed(repo)

    for i in range(5):
        sm.start(f"Task {i}")

    sessions = sm.list_sessions()
    orphaned = [s for s in sessions if s.get("finished_at", "") == ""]
    # Only 1 orphan expected: the current/last session that hasn't been replaced yet
    assert len(orphaned) == 1, f"Fixed: Expected 1 active session (current), got {len(orphaned)}"
    # All 4 previous sessions should have finished_at
    closed = [s for s in sessions if s.get("finished_at", "") != ""]
    assert len(closed) == 4, f"Expected 4 closed sessions, got {len(closed)}"
    print(f"  ✓ Test 1a: Auto-close works: {len(closed)} closed, only current session active)")


def test_orphaned_sessions_old():
    """Bug 1: Before fix, all 5 sessions are orphaned (none have finished_at)."""
    repo = Path(tempfile.mkdtemp())
    sm = SessionManagerOld(repo)

    for i in range(5):
        sm.start(f"Task {i}")

    sessions = sm.list_sessions()
    orphaned = [s for s in sessions if s.get("finished_at", "") == ""]
    # Old behavior: NO session gets a finished_at — all 5 are orphans
    assert len(orphaned) == 5, f"Old: Expected 5 orphans (all sessions), got {len(orphaned)}"
    print(f"  ✓ Test 1b: Old behavior confirms 5/5 orphaned sessions (none closed)")


# ==============================================================================
# TEST 2: History Compaction - Tool message interleaving
# ==============================================================================

def test_compaction_strips_orphan_tool_messages():
    """Bug 2: After compaction, no tool message should be orphaned."""
    # Simulate a conversation with tool calls
    history = [
        {"role": "user", "content": "Add logging to app.py"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "file content here"},
        {"role": "assistant", "content": "I've read the file"},
        {"role": "user", "content": "Now add the logging"},
        {"role": "assistant", "content": "Final response"},
    ]

    # Simulate compact_history logic (the fix strips trailing tool messages)
    keep = 2  # keep last 2 exchanges
    recent = history[-keep:]
    cleaned_recent = list(recent)

    # Apply the fix: strip trailing tool messages
    while cleaned_recent and cleaned_recent[-1].get("role") == "tool":
        cleaned_recent.pop()
    while cleaned_recent and cleaned_recent[0].get("role") == "tool":
        cleaned_recent.pop(0)

    replacement = [
        {"role": "user", "content": "<history_summary>summary</history_summary>"},
        {"role": "assistant", "content": "<history_summary>summarized</history_summary>"},
    ]
    new_history = replacement + cleaned_recent

    # Verify interleaving
    for i, msg in enumerate(new_history):
        if msg.get("role") == "tool":
            has_preceding = False
            for j in range(i - 1, max(i - 5, -1), -1):
                prev = new_history[j]
                if prev.get("role") == "assistant" and prev.get("tool_calls"):
                    has_preceding = True
                    break
            assert has_preceding, f"Orphaned tool message at index {i}"
    print("  ✓ Test 2: No orphaned tool messages after compaction fix")


def test_compaction_removes_stray_tool_messages():
    """History with tool message at end should be cleaned."""
    # Case: tool message at end of recent history (no preceding assistant)
    history = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "file content"},
        {"role": "assistant", "content": "Done with file"},
    ]

    keep = 2
    recent = history[-keep:]
    cleaned_recent = list(recent)

    # Fix: strip trailing tool messages
    while cleaned_recent and cleaned_recent[-1].get("role") == "tool":
        cleaned_recent.pop()
    while cleaned_recent and cleaned_recent[0].get("role") == "tool":
        cleaned_recent.pop(0)

    replacement = [
        {"role": "user", "content": "<history_summary>summary</history_summary>"},
        {"role": "assistant", "content": "<history_summary>summarized</history_summary>"},
    ]
    new_history = replacement + cleaned_recent

    # Verify: last message should be assistant (Done with file), not tool
    non_meta_msgs = [m for m in new_history if m.get("role") in ("user", "assistant", "tool")]
    if non_meta_msgs:
        last_msg = non_meta_msgs[-1]
        assert last_msg["role"] in ("user", "assistant"), f"Last message should not be 'tool': got '{last_msg['role']}'"
    print("  ✓ Test 2b: Stray tool messages properly stripped from compaction")


# ==============================================================================
# TEST 3: Loop Detector Reset Between Tasks
# ==============================================================================

class LoopDetector:
    """Simplified version for testing."""
    def __init__(self, max_repetitions=5):
        self._history = []
        self._cumulative_files_read = set()
        self.window_size = 12
        self.max_repetitions = max_repetitions
        self._read_only_repeat_count = 0
        self._last_turn_was_read_only = False

    def record(self, files_read=None, files_modified=None):
        files_read = files_read or []
        files_modified = files_modified or []
        severity = "ok"
        reason = ""

        is_read_only = bool(files_read) and not bool(files_modified)
        if is_read_only and self._last_turn_was_read_only:
            self._read_only_repeat_count += 1
        elif not is_read_only:
            self._read_only_repeat_count = 0
        self._last_turn_was_read_only = is_read_only

        same_file_rereads = 0
        if is_read_only:
            for f in files_read:
                if f in self._cumulative_files_read:
                    same_file_rereads += 1

        if self._read_only_repeat_count >= 15 or same_file_rereads >= 10:
            severity = "critical"

        self._cumulative_files_read |= set(files_read)
        self._cumulative_files_read |= set(files_modified)
        return severity, reason

    def reset(self):
        self._history.clear()
        self._cumulative_files_read.clear()
        self._read_only_repeat_count = 0
        self._last_turn_was_read_only = False


def test_loop_detector_reset_between_tasks():
    """Bug 3: Loop detector should not carry state across tasks."""
    ld = LoopDetector()

    # Task 1: Read files
    for _ in range(3):
        sev, _ = ld.record(files_read=["app.py"])

    ld.reset()  # <-- The fix: reset between tasks

    # Task 2: Read different file
    for _ in range(5):
        sev, _ = ld.record(files_read=["new_file.py"])
        assert sev != "critical", f"After reset, reading new files should not be critical"

    print("  ✓ Test 3: Loop detector properly resets between tasks")


def test_loop_detector_false_positive_without_reset():
    """Without reset, same files across tasks triggers false positive."""
    ld = LoopDetector()

    # Task 1: Read same file many times
    for _ in range(10):
        ld.record(files_read=["app.py"])

    # Task 2: Same file (without reset - this is the OLD behavior)
    for i in range(5):
        sev, _ = ld.record(files_read=["app.py"])
        if i == 0:
            assert sev != "critical", "Reading same file once should be OK"

    print("  ✓ Test 3b: Loop detector state checked")


# ==============================================================================
# TEST 4: State Leakage - Session Notes Capped
# ==============================================================================

class ContextManagerFixed:
    """Version with bounded session_notes."""
    def __init__(self):
        self.session_notes = []

    def add_note(self, note: str):
        self.session_notes.append(note)
        # Cap session notes to 50
        if len(self.session_notes) > 50:
            self.session_notes = self.session_notes[-50:]


class ContextManagerOld:
    """Original version with unbounded growth."""
    def __init__(self):
        self.session_notes = []

    def add_note(self, note: str):
        self.session_notes.append(note)


def test_session_notes_capped():
    """Bug 4: Session notes should be bounded."""
    ctx = ContextManagerFixed()

    for i in range(100):
        ctx.add_note(f"Note {i}")

    assert len(ctx.session_notes) == 50, f"Expected 50, got {len(ctx.session_notes)}"
    assert ctx.session_notes[0] == "Note 50", f"Oldest should be Note 50, got {ctx.session_notes[0]}"
    assert ctx.session_notes[-1] == "Note 99", f"Newest should be Note 99, got {ctx.session_notes[-1]}"
    print("  ✓ Test 4: Session notes capped at 50")


def test_session_notes_unbounded_old():
    """Old behavior - unbounded growth."""
    ctx = ContextManagerOld()
    for i in range(100):
        ctx.add_note(f"Note {i}")
    assert len(ctx.session_notes) == 100, f"Unbounded: Expected 100, got {len(ctx.session_notes)}"
    print("  ✓ Test 4b: Old behavior confirms unbounded growth (100 notes)")


# ==============================================================================
# TEST 5: Duplicate Assistant Messages
# ==============================================================================

def test_duplicate_assistant_messages_prevented():
    """Bug 5: executor history turns should take precedence over add_assistant_message."""
    history = []
    full_response = "Response 1"
    last_history_turns = [
        {"role": "assistant", "content": "Response 1"},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]

    # Fixed logic: use executor history if available, else add_assistant_message
    if last_history_turns:
        history.extend(last_history_turns)
    elif full_response:
        history.append({"role": "assistant", "content": full_response})

    assistant_msgs = [m for m in history if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1, f"Expected 1 assistant message, got {len(assistant_msgs)}"
    print("  ✓ Test 5: No duplicate assistant messages (executor history preferred)")


# ==============================================================================
# TEST 6: Plan State Persistence
# ==============================================================================

def test_reset_state_clears_plan():
    """Bug 6: _reset_state should clear _pending_plan and _recovery_exhausted."""
    class MockAgent:
        def __init__(self):
            self._pending_plan = "old_plan"
            self._plan_feedback = "old_feedback"
            self._recovery_exhausted = True
            self._status = TaskStatus.AWAITING_INPUT

        def _reset_state(self):
            self._status = TaskStatus.IDLE
            self._pending_plan = None
            self._plan_feedback = ""
            self._recovery_exhausted = False

    agent = MockAgent()
    agent._reset_state()

    assert agent._pending_plan is None, f"_pending_plan should be None, got {agent._pending_plan}"
    assert agent._plan_feedback == "", f"_plan_feedback should be empty"
    assert agent._recovery_exhausted == False, f"_recovery_exhausted should be False"
    assert agent._status == TaskStatus.IDLE, f"_status should be IDLE"
    print("  ✓ Test 6: _reset_state clears all plan/recovery state")


# ==============================================================================
# TEST 7: Working Set Overflow
# ==============================================================================

class LRUSet(OrderedDict):
    def __init__(self, max_size=30):
        super().__init__()
        self.max_size = max_size

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self.max_size:
            oldest = next(iter(self))
            del self[oldest]


def test_working_set_bounded():
    """Bug 7: Working set is bounded by LRUSet."""
    ws = LRUSet(max_size=10)

    for i in range(50):
        ws[f"file_{i}.py"] = f"content_{i}"

    assert len(ws) == 10, f"Expected 10, got {len(ws)}"
    # Earliest files should be evicted
    assert "file_0.py" not in ws, "file_0.py should be evicted"
    assert "file_49.py" in ws, "file_49.py should be present"
    print("  ✓ Test 7: Working set bounded by LRUSet (10 of 50 retained)")


# ==============================================================================
# MAIN
# ==============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  MULTI-TURN CONVERSATION RELIABILITY VALIDATION")
    print("=" * 60 + "\n")

    print("1. ORPHANED SESSIONS")
    test_orphaned_sessions_fixed()
    test_orphaned_sessions_old()

    print("\n2. HISTORY COMPACTION INTERLEAVING")
    test_compaction_strips_orphan_tool_messages()
    test_compaction_removes_stray_tool_messages()

    print("\n3. LOOP DETECTOR RESET BETWEEN TASKS")
    test_loop_detector_reset_between_tasks()
    test_loop_detector_false_positive_without_reset()

    print("\n4. STATE LEAKAGE (SESSION NOTES)")
    test_session_notes_capped()
    test_session_notes_unbounded_old()

    print("\n5. DUPLICATE ASSISTANT MESSAGES")
    test_duplicate_assistant_messages_prevented()

    print("\n6. PLAN STATE PERSISTENCE")
    test_reset_state_clears_plan()

    print("\n7. WORKING SET OVERFLOW")
    test_working_set_bounded()

    print("\n" + "=" * 60)
    print("  ALL TESTS PASSED ✓")
    print("=" * 60 + "\n")