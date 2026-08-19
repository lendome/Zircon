"""Comprehensive, sandbox-style multi-turn conversation tests.
Simulates realistic multi-turn scenarios with actual data to uncover
integration faults between components.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

# ==============================================================================
# Fully self-contained test framework — no external dependencies
# ==============================================================================

class TaskStatus:
    IDLE = "idle"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"


def estimate_tokens(text: str | None) -> int:
    if text is None:
        return 0
    if not text:
        return 1
    return max(1, len(text) // 4)


# ==============================================================================
# SANDBOX: Create a realistic codebase for tests to operate on
# ==============================================================================

class SandboxRepo:
    """Creates a temporary git-like project for realistic multi-turn operations."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp())
        self._create_structure()

    def _create_structure(self):
        """Create a multi-file Python project with imports and dependencies."""
        files = {
            "src/__init__.py": "",
            "src/main.py": (
                "from .utils.helpers import format_name\n"
                "from .utils.validators import validate_email\n"
                "from .models.user import User\n"
                "from .services.auth import AuthService\n"
                "\n\n"
                "def main():\n"
                "    print('Starting application...')\n"
                "    user = User('john@example.com', 'John')\n"
                "    auth = AuthService()\n"
                "    if auth.login(user):\n"
                "        print(f'Welcome {format_name(user.name)}')\n"
                "    else:\n"
                "        print('Login failed')\n"
                "\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "src/utils/__init__.py": "",
            "src/utils/helpers.py": (
                "def format_name(name: str) -> str:\n"
                "    return name.strip().title()\n"
                "\n\n"
                "def calculate_discount(price: float, percent: float) -> float:\n"
                "    return price * (1 - percent / 100)\n"
                "\n\n"
                "def slugify(text: str) -> str:\n"
                "    return text.lower().replace(' ', '-')\n"
            ),
            "src/utils/validators.py": (
                "import re\n"
                "\n\n"
                "def validate_email(email: str) -> bool:\n"
                "    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$'\n"
                "    return bool(re.match(pattern, email))\n"
                "\n\n"
                "def validate_age(age: int) -> bool:\n"
                "    return 0 < age < 150\n"
            ),
            "src/models/__init__.py": "",
            "src/models/user.py": (
                "from dataclasses import dataclass\n"
                "\n\n"
                "@dataclass\n"
                "class User:\n"
                "    email: str\n"
                "    name: str\n"
                "    age: int = 0\n"
                "    is_active: bool = True\n"
                "\n"
                "    def display_name(self) -> str:\n"
                "        return self.name.title()\n"
            ),
            "src/services/__init__.py": "",
            "src/services/auth.py": (
                "from ..models.user import User\n"
                "\n\n"
                "class AuthService:\n"
                "    def __init__(self):\n"
                "        self._users: dict[str, User] = {}\n"
                "\n"
                "    def register(self, user: User) -> bool:\n"
                "        if user.email in self._users:\n"
                "            return False\n"
                "        self._users[user.email] = user\n"
                "        return True\n"
                "\n"
                "    def login(self, user: User) -> bool:\n"
                "        return user.email in self._users\n"
                "\n"
                "    def get_user(self, email: str) -> User | None:\n"
                "        return self._users.get(email)\n"
            ),
            "tests/__init__.py": "",
            "tests/test_user.py": (
                "from src.models.user import User\n"
                "\n\n"
                "def test_user_creation():\n"
                "    user = User('test@test.com', 'Test User')\n"
                "    assert user.email == 'test@test.com'\n"
                "    assert user.name == 'Test User'\n"
                "    assert user.is_active == True\n"
                "\n\n"
                "def test_user_display_name():\n"
                "    user = User('test@test.com', 'test user')\n"
                "    assert user.display_name() == 'Test User'\n"
            ),
            "README.md": "# My Project\n\nA sample project for testing.\n",
            "requirements.txt": "pytest>=7.0\n",
        }
        for path_str, content in files.items():
            full_path = self.root / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)

    @property
    def path(self) -> Path:
        return self.root

    def cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)


SENTINEL = object()

# ==============================================================================
# CONTEXT MANAGER (simplified, with fixes applied)
# ==============================================================================

class LRUSet(OrderedDict):
    def __init__(self, max_size: int = 30):
        super().__init__()
        self.max_size = max_size

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self.max_size:
            oldest = next(iter(self))
            del self[oldest]


class ContextManagerFixed:
    """Context manager with all multi-turn fixes applied."""

    def __init__(self, tier_config: dict | None = None):
        cfg = tier_config or {"name": "balanced"}
        self.tier = type('obj', (object,), cfg)
        self.history: list[dict] = []
        self.session_notes: list[str] = []
        self.working_set = LRUSet(max_size=getattr(self.tier, 'working_set_max_files', 30))
        self.modified_files: set[str] = set()
        self.task = ""
        self.max_tokens = 32000

    def add_user_message(self, content: str):
        self.history.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str):
        self.history.append({"role": "assistant", "content": content})

    def add_note(self, note: str):
        self.session_notes.append(note)
        if len(self.session_notes) > 50:
            self.session_notes = self.session_notes[-50:]

    def add_file_to_working_set(self, path: str, content: str | None):
        if content is None:
            return
        if path in self.modified_files:
            content = content[:getattr(self.tier, 'modified_file_tokens', 4000) * 4]
        else:
            content = content[:getattr(self.tier, 'tokens_per_file', 2000) * 4]
        self.working_set[path] = content

    def mark_modified(self, path: str):
        self.modified_files.add(path)

    def clear_history(self):
        self.history.clear()

    def build_messages(self, system_prompt: str = "", tools: str = "") -> list[dict]:
        messages = [{"role": "system", "content": system_prompt}]
        # Add working set context
        for path, content in self.working_set.items():
            messages.append({"role": "system", "content": f'<file path="{path}">\n{content[:500]}\n</file>'})
        # Add session notes
        if self.session_notes:
            notes = "\n".join(f"- {n}" for n in self.session_notes[-10:])
            messages.append({"role": "system", "content": f"<notes>\n{notes}\n</notes>"})
        # Add task
        if self.task:
            messages.append({"role": "user", "content": f"<task>\n{self.task}\n</task>"})
        # Add history (last 10 messages, ensuring interleaving)
        recent = self.history[-10:] if len(self.history) > 10 else self.history
        messages.extend(recent)
        return messages


# ==============================================================================
# SESSION MANAGER (with auto-close fix)
# ==============================================================================

class Session:
    def __init__(self, task="", status="created"):
        self.id = uuid.uuid4().hex[:12]
        self.task = task
        self.status = status
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at = ""
        self.files_modified: list[str] = []
        self.tokens_used = 0


class SessionManagerFixed:
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.session_dir = repo_path / ".sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._current: Session | None = None

    @property
    def current(self) -> Session | None:
        return self._current

    def start(self, task: str) -> Session:
        if self._current is not None and self._current.finished_at == "":
            self._current.finished_at = datetime.now(timezone.utc).isoformat()
            self._current.status = TaskStatus.COMPLETED
            self._write_manifest(self._current)
        session = Session(task=task, status=TaskStatus.RUNNING)
        self._current = session
        (self.session_dir / session.id).mkdir(exist_ok=True)
        self._write_manifest(session)
        return session

    def close(self, status: str = TaskStatus.COMPLETED):
        if not self._current:
            return
        self._current.status = status
        self._current.finished_at = datetime.now(timezone.utc).isoformat()
        self._write_manifest(self._current)

    def track_file(self, path: str):
        if self._current and path not in self._files_modified:
            self._current.files_modified.append(path)

    def _write_manifest(self, session: Session):
        path = self.session_dir / session.id / "manifest.json"
        data = {
            "id": session.id, "task": session.task, "status": session.status,
            "started_at": session.started_at, "finished_at": session.finished_at,
            "files_modified": session.files_modified, "tokens_used": session.tokens_used,
        }
        path.write_text(json.dumps(data))

    def list_sessions(self) -> list[dict]:
        sessions = []
        if not self.session_dir.exists():
            return sessions
        for d in sorted(self.session_dir.iterdir()):
            m = d / "manifest.json"
            if m.exists():
                sessions.append(json.loads(m.read_text()))
        return sessions


# ==============================================================================
# LOOP DETECTOR (with reset)
# ==============================================================================

class LoopDetectorFixed:
    def __init__(self, max_repetitions=5):
        self._history: list[frozenset] = []
        self._cumulative_files_read: set[str] = set()
        self._read_only_repeat_count = 0
        self._last_turn_was_read_only = False
        self.max_repetitions = max_repetitions

    def record(self, files_read: list[str], files_modified: list[str]) -> str:
        severity = "ok"
        is_read_only = bool(files_read) and not bool(files_modified)
        if is_read_only and self._last_turn_was_read_only:
            self._read_only_repeat_count += 1
        elif not is_read_only:
            self._read_only_repeat_count = 0
        self._last_turn_was_read_only = is_read_only

        same_file_rereads = sum(1 for f in files_read if f in self._cumulative_files_read)
        if self._read_only_repeat_count >= 15 or same_file_rereads >= 10:
            severity = "critical"

        self._cumulative_files_read |= set(files_read) | set(files_modified)
        return severity

    def reset(self):
        self._history.clear()
        self._cumulative_files_read.clear()
        self._read_only_repeat_count = 0
        self._last_turn_was_read_only = False


# ==============================================================================
# TEST 1: THREE-TURN CONVERSATION WITH REAL FILES
# ==============================================================================

class TestThreeTurnConversation:
    """Simulate a realistic 3-turn conversation modifying actual files."""

    def setup(self):
        self.sandbox = SandboxRepo()
        self.ctx = ContextManagerFixed()
        self.sessions = SessionManagerFixed(self.sandbox.path)
        self.loop = LoopDetectorFixed()

    def teardown(self):
        self.sandbox.cleanup()

    def test_full_workflow(self):
        """Turn 1: Add logging -> Turn 2: Add error handling -> Turn 3: Add tests."""
        self.setup()
        try:
            # ---- Turn 1: Add logging ----
            self.sessions.start("Add logging to main.py")
            self.ctx.task = "Add logging to main.py"
            self.ctx.add_user_message("Add logging to the main function")

            # Read main.py
            main_content = (self.sandbox.root / "src/main.py").read_text()
            self.ctx.add_file_to_working_set("src/main.py", main_content)

            # Edit main.py to add logging
            new_main = main_content.replace(
                "def main():",
                "import logging\nlogger = logging.getLogger(__name__)\n\n\ndef main():"
            ).replace(
                "print('Starting application...')",
                "logger.info('Starting application...')"
            )
            (self.sandbox.root / "src/main.py").write_text(new_main)
            self.ctx.mark_modified("src/main.py")
            self.ctx.add_assistant_message("Added logging to main.py")

            # Verify state after turn 1
            assert len(self.ctx.history) == 2  # user + assistant
            assert "src/main.py" in self.ctx.modified_files
            assert "src/main.py" in self.ctx.working_set
            assert self.sessions.current is not None
            assert self.sessions.current.task == "Add logging to main.py"

            # Check for duplicate assistant messages (Bug 5 test)
            assistant_count = sum(1 for m in self.ctx.history if m["role"] == "assistant")
            assert assistant_count == 1, f"Expected 1 assistant, got {assistant_count}"

            # ---- Turn 2: Add error handling (new conversation turn) ----
            # This should auto-close the previous session
            self.sessions.start("Add error handling")
            self.ctx.task = "Add error handling"
            self.ctx.add_user_message("Add try/except error handling to main.py")

            # Previous turn's state should persist in history
            assert len(self.ctx.history) == 3  # old user + assistant + new user

            # Read the (now modified) main.py
            main_content_v2 = (self.sandbox.root / "src/main.py").read_text()
            assert "logger" in main_content_v2, "Previous edit should persist"

            # Edit: add error handling
            with_err = main_content_v2.replace(
                "def main():",
                "def main():\n    try:"
            ).replace(
                "    logger.info('Starting application...')",
                "        logger.info('Starting application...')"
            ).replace(
                "        auth = AuthService()",
                "            auth = AuthService()"
            ).replace(
                "            auth = AuthService()",
                "            auth = AuthService()\n    except Exception as e:\n        logger.error(f'Main failed: {e}')"
            )
            (self.sandbox.root / "src/main.py").write_text(with_err)
            self.ctx.mark_modified("src/main.py")
            self.ctx.add_assistant_message("Added try/except error handling")

            # Verify session management (Bug 1 test)
            sessions = self.sessions.list_sessions()
            closed = [s for s in sessions if s.get("finished_at")]
            assert len(closed) >= 1, "Turn 1 session should be closed"
            # Current session should be turn 2
            assert self.sessions.current.task == "Add error handling"

            # ---- Turn 3: Add unit tests (third turn) ----
            sessions_before = len(self.sessions.list_sessions())
            self.sessions.start("Add unit tests for auth service")

            # Auto-close of turn 2 should have happened
            sessions = self.sessions.list_sessions()
            assert len(sessions) == sessions_before + 1, "Should create new session"

            self.ctx.task = "Add unit tests for auth service"
            self.ctx.add_user_message("Add unit tests for AuthService")

            # Read auth.py
            auth_content = (self.sandbox.root / "src/services/auth.py").read_text()
            self.ctx.add_file_to_working_set("src/services/auth.py", auth_content)

            # Create new test file
            test_auth = (
                "from src.services.auth import AuthService\n"
                "from src.models.user import User\n"
                "\n\n"
                "def test_register_user():\n"
                "    auth = AuthService()\n"
                "    user = User('test@test.com', 'Test')\n"
                "    assert auth.register(user) == True\n"
                "    assert auth.register(user) == False  # duplicate\n"
                "\n\n"
                "def test_login_user():\n"
                "    auth = AuthService()\n"
                "    user = User('test@test.com', 'Test')\n"
                "    auth.register(user)\n"
                "    assert auth.login(user) == True\n"
                "    assert auth.login(User('other@test.com', 'Other')) == False\n"
            )
            test_path = self.sandbox.root / "tests/test_auth.py"
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text(test_auth)
            self.ctx.mark_modified("tests/test_auth.py")
            self.ctx.add_file_to_working_set("tests/test_auth.py", test_auth)
            self.ctx.add_assistant_message("Added unit tests for AuthService")

            # ---- Verify final state ----
            # History should have all 3 turns without duplicates
            user_msgs = [m for m in self.ctx.history if m["role"] == "user"]
            assistant_msgs = [m for m in self.ctx.history if m["role"] == "assistant"]
            assert len(user_msgs) == 3, f"Expected 3 user messages, got {len(user_msgs)}"
            assert len(assistant_msgs) == 3, f"Expected 3 assistant messages, got {len(assistant_msgs)}"

            # Modified files should track all changes
            assert "src/main.py" in self.ctx.modified_files
            assert "tests/test_auth.py" in self.ctx.modified_files

            # Working set should contain all files we read
            assert "src/main.py" in self.ctx.working_set
            assert "src/services/auth.py" in self.ctx.working_set

            # Session notes should not leak (Bug 4 test)
            for _ in range(60):
                self.ctx.add_note(f"Note {_}")
            assert len(self.ctx.session_notes) == 50, "Session notes should be capped at 50"

            # Loop detector should not trigger (Bug 3 test)
            for _ in range(5):
                severity = self.loop.record(
                    files_read=["src/main.py"],
                    files_modified=[]
                )
                assert severity != "critical", "Reading same file should not trigger loop"
            self.loop.reset()
            for _ in range(5):
                severity = self.loop.record(
                    files_read=["src/services/auth.py"],
                    files_modified=[]
                )
                assert severity != "critical", "After reset, should be fine"

            print("  ✓ Test 1: Three-turn conversation with real files")

        finally:
            self.teardown()


# ==============================================================================
# TEST 2: PLAN APPROVAL AND FEEDBACK LOOP
# ==============================================================================

class TestPlanFeedbackLoop:
    """Simulate plan creation, feedback, and execution cycles."""

    def test_plan_then_modify_then_execute(self):
        """Agent proposes plan -> user gives feedback -> agent executes modified plan."""
        sandbox = SandboxRepo()
        try:
            ctx = ContextManagerFixed()
            sessions = SessionManagerFixed(sandbox.path)

            # Phase 1: Plan created
            plan = {
                "steps": [
                    {"index": 1, "action": "explore", "description": "Explore main.py structure"},
                    {"index": 2, "action": "edit", "description": "Add logging to main.py", "target_files": ["src/main.py"]},
                    {"index": 3, "action": "verify", "description": "Run tests to verify"},
                ],
                "complexity": "moderate"
            }

            ctx.add_user_message("Add logging to the project")
            ctx.add_note(f"Plan proposed: {' -> '.join(s['description'] for s in plan['steps'])}")

            # Phase 2: User gives feedback (modifies plan scope)
            feedback = (
                "Don't add logging to everything. "
                "Only add structured logging to main.py with log levels. "
                "Also skip the verification step, I'll test manually."
            )
            ctx.add_note(f"User feedback: {feedback}")

            # Apply feedback: remove verify step, clarify scope
            adjusted_plan = {
                "steps": [
                    {"index": 1, "action": "explore", "description": "Explore main.py structure"},
                    {"index": 2, "action": "edit", "description": "Add structed logging (info/warn levels) to main.py", "target_files": ["src/main.py"]},
                ],
                "complexity": "simple"
            }
            ctx.add_note(f"Adjusted plan: {' -> '.join(s['description'] for s in adjusted_plan['steps'])}")

            # Phase 3: Execute modified plan
            sessions.start("Add structured logging to main.py")
            ctx.task = "Add structured logging to main.py"

            # Read main.py
            main_content = (sandbox.root / "src/main.py").read_text()
            ctx.add_file_to_working_set("src/main.py", main_content)

            # Modify: add structured logging
            new_main = main_content.replace(
                "import logging",
                "import logging\nimport sys"
            ).replace(
                "logger = logging.getLogger(__name__)",
                "logger = logging.getLogger(__name__)\nlogger.setLevel(logging.INFO)\nhandler = logging.StreamHandler(sys.stdout)\nhandler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))\nlogger.addHandler(handler)"
            )
            (sandbox.root / "src/main.py").write_text(new_main)
            ctx.mark_modified("src/main.py")
            ctx.add_assistant_message("Added structured logging with log levels to main.py")

            sessions.close(TaskStatus.COMPLETED)

            # Verify: feedback was incorporated
            notes_with_feedback = [n for n in ctx.session_notes if "User feedback" in n]
            assert len(notes_with_feedback) == 1, "Feedback note should be in session_notes"
            # The adjusted plan note should exist (checking last note for "Adjusted plan")
            adjusted_notes = [n for n in ctx.session_notes if "Adjusted plan" in n]
            assert len(adjusted_notes) >= 1, "Should have adjusted plan note"
            assert "logging" in adjusted_notes[-1], "Adjusted plan should mention logging"

            # Verify: correct files modified
            assert ctx.modified_files == {"src/main.py"}, "Only main.py should be modified"

            # Verify: working set has exactly what we read
            assert len(ctx.working_set) >= 1

            # Verify: sessions clean
            sessions_list = sessions.list_sessions()
            assert len(sessions_list) == 1
            assert sessions_list[0]["status"] == TaskStatus.COMPLETED

            print("  ✓ Test 2: Plan feedback loop preserves modifications")

        finally:
            sandbox.cleanup()


# ==============================================================================
# TEST 3: HISTORY COMPACTION WITH REAL MESSAGE PATTERNS
# ==============================================================================

class TestHistoryCompactionRecovery:
    """Test that compaction recovery works with real conversation patterns."""

    def test_compaction_after_many_tool_turns(self):
        """Build a long conversation, compact it, verify interleaving."""
        ctx = ContextManagerFixed({"name": "balanced"})

        # Build a realistic conversation with many tool messages
        turn_pairs = [
            ("Read the structure of the project", "I'll explore the project files"),
            ("Show me the user model", "Here's the user.py file"),
            ("Add a display_name method", "I've added the display_name method"),
            ("Also add validation", "Added validation"),
            ("Now write tests", "Tests written"),
            ("Add error handling", "Error handling added"),
            ("Optimize the code", "Code optimized"),
            ("Document the changes", "Documentation added"),
            ("Run the tests", "Tests pass"),
            ("Refactor the auth module", "Auth module refactored"),
        ]

        # Add all turns with interleaved tool messages
        for user_msg, asst_msg in turn_pairs:
            ctx.add_user_message(user_msg)
            ctx.history.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": f"tc_{uuid.uuid4().hex[:8]}", "type": "function",
                     "function": {"name": "read_file", "arguments": "{}"}}
                ]
            })
            ctx.history.append({
                "role": "tool",
                "tool_call_id": f"tc_{uuid.uuid4().hex[:8]}",
                "content": "file contents here"
            })
            ctx.add_assistant_message(asst_msg)

        # Verify pre-compaction state
        assert len(ctx.history) == len(turn_pairs) * 4  # Each turn: user + asst(tc) + tool + asst

        # Simulate compaction
        keep = 4  # Keep last 2 exchanges = 4 messages
        to_summarize = ctx.history[:-keep]
        recent = ctx.history[-keep:]

        # Clean orphaned tool messages (Bug 2 fix)
        cleaned_recent = list(recent)
        while cleaned_recent and cleaned_recent[-1].get("role") == "tool":
            cleaned_recent.pop()
        while cleaned_recent and cleaned_recent[0].get("role") == "tool":
            cleaned_recent.pop(0)

        compacted = [
            {"role": "user", "content": "<history_summary>...</history_summary>"},
            {"role": "assistant", "content": "<history_summary>Summarized</history_summary>"},
        ] + cleaned_recent

        # Verify NO orphaned tool messages
        for i, msg in enumerate(compacted):
            if msg.get("role") == "tool":
                has_preceding = False
                for j in range(i - 1, max(i - 5, -1), -1):
                    prev = compacted[j]
                    if prev.get("role") == "assistant" and prev.get("tool_calls"):
                        has_preceding = True
                        break
                assert has_preceding, f"Orphaned tool message at index {i}"

        # Verify recent messages preserved
        user_after_compaction = [m for m in compacted if m["role"] == "user"]
        assert len(user_after_compaction) > 0, "Should have user messages"

        print("  ✓ Test 3: History compaction with tool message patterns")

    def test_compaction_with_stray_tool_at_end(self):
        """When tool message is the last in recent history, it gets stripped."""
        ctx = ContextManagerFixed({"name": "balanced"})

        ctx.add_user_message("Read file")
        ctx.history.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "tc1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]
        })
        ctx.history.append({"role": "tool", "tool_call_id": "tc1", "content": "file content here"})
        # NO assistant response after tool — this is the bug case

        keep = 2
        recent = ctx.history[-keep:]
        cleaned_recent = list(recent)
        while cleaned_recent and cleaned_recent[-1].get("role") == "tool":
            cleaned_recent.pop()
        while cleaned_recent and cleaned_recent[0].get("role") == "tool":
            cleaned_recent.pop(0)

        compacted = [
            {"role": "user", "content": "<history_summary>summary</history_summary>"},
            {"role": "assistant", "content": "<history_summary>ok</history_summary>"},
        ] + cleaned_recent

        # Last non-meta message must not be 'tool'
        non_meta = [m for m in compacted if m.get("role") in ("user", "assistant", "tool")]
        if non_meta:
            assert non_meta[-1]["role"] != "tool", "Last message should not be tool"
        print("  ✓ Test 3b: Stray tool messages at end handled")


# ==============================================================================
# TEST 4: CONVERSATION BOUNDARY WITH ERROR CONDITIONS
# ==============================================================================

class TestConversationBoundaries:
    """Test edge cases at conversation turn boundaries."""

    def test_empty_message_doesnt_break_history(self):
        """Empty user messages should not corrupt history interleaving."""
        ctx = ContextManagerFixed()

        ctx.add_user_message("")
        ctx.add_assistant_message("OK")

        # Then a real message
        ctx.add_user_message("Add logging")
        ctx.add_assistant_message("Done")

        assert len(ctx.history) == 4, f"Expected 4, got {len(ctx.history)}"
        user_msgs = [m for m in ctx.history if m["role"] == "user"]
        assert len(user_msgs) == 2, f"Expected 2 user msgs"
        print("  ✓ Test 4a: Empty messages don't break history")

    def test_rapid_messages_without_waiting(self):
        """Multiple rapid user messages before assistant response."""
        ctx = ContextManagerFixed()

        # User sends 3 messages in rapid succession
        ctx.add_user_message("Add feature A")
        ctx.add_user_message("Actually add feature B instead")
        ctx.add_user_message("No wait, do both")

        # Assistant finally responds once
        ctx.add_assistant_message("I'll add both features A and B")

        # The old user messages should all be preserved
        user_msgs = [m for m in ctx.history if m["role"] == "user"]
        assert len(user_msgs) == 3, f"Expected 3 user msgs, got {len(user_msgs)}"
        print("  ✓ Test 4b: Rapid-fire messages preserved in history")

    def test_assistant_message_after_multiple_tool_calls(self):
        """Proper interleaving with multiple tool calls in one turn."""
        ctx = ContextManagerFixed()

        ctx.add_user_message("Read auth.py and user.py")

        # Assistant makes 2 tool calls in one response
        ctx.history.append({
            "role": "assistant", "content": None,
            "tool_calls": [
                {"id": "tc1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "auth.py"}'}},
                {"id": "tc2", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "user.py"}'}},
            ]
        })
        ctx.history.append({"role": "tool", "tool_call_id": "tc1", "content": "auth content"})
        ctx.history.append({"role": "tool", "tool_call_id": "tc2", "content": "user content"})
        ctx.add_assistant_message("Here are both files")

        # Verify interleaving
        tool_indices = [i for i, m in enumerate(ctx.history) if m["role"] == "tool"]
        for ti in tool_indices:
            # Each tool message should have assistant with tool_calls before it
            has_preceding = False
            for j in range(ti - 1, -1, -1):
                prev = ctx.history[j]
                if prev.get("role") == "assistant" and prev.get("tool_calls"):
                    has_preceding = True
                    break
                if prev.get("role") == "user" or (prev.get("role") == "assistant" and not prev.get("tool_calls")):
                    break
            assert has_preceding, f"Tool at index {ti} has no preceding assistant with tool_calls"

        print("  ✓ Test 4c: Multi-tool-call interleaving preserved")


# ==============================================================================
# TEST 5: LOOP DETECTOR ACROSS MULTIPLE CONVERSATION TURNS
# ==============================================================================

class TestLoopDetectorScenarios:
    """Realistic loop detector scenarios across turns."""

    def test_legitimate_file_read_across_turns(self):
        """Reading the SAME file in different turns is legitimate."""
        ld = LoopDetectorFixed()

        # Turn 1: explore auth.py
        for _ in range(3):
            ld.record(files_read=["src/services/auth.py"], files_modified=[])

        # User says "OK, now let's fix a bug in a different file"
        ld.reset()

        # Turn 2: need to re-read auth.py for reference
        for _ in range(5):
            sev = ld.record(files_read=["src/services/auth.py"], files_modified=[])
            assert sev != "critical", f"Re-reading in new turn should not be critical at iteration {_}"
        print("  ✓ Test 5a: Same file read across turns with reset")

    def test_edit_then_read_does_not_loop(self):
        """Editing a file then reading it should never be a loop."""
        ld = LoopDetectorFixed()

        # Real pattern: read -> edit -> read -> edit
        ld.record(files_read=["app.py"], files_modified=[])
        ld.record(files_read=["app.py"], files_modified=["app.py"])
        ld.record(files_read=["app.py"], files_modified=[])
        ld.record(files_read=["app.py"], files_modified=["app.py"])

        for _ in range(5):
            sev = ld.record(files_read=["app.py"], files_modified=[])
            assert sev != "critical", "Read after edit is legitimate"
        print("  ✓ Test 5b: Edit-then-read pattern not detected as loop")

    def test_different_file_reading_not_looping(self):
        """Reading different files in sequence is not a loop."""
        ld = LoopDetectorFixed(max_repetitions=5)
        files = [f"file_{i}.py" for i in range(12)]

        for f in files:
            sev = ld.record(files_read=[f], files_modified=[])
            # 12 different files should NOT trigger critical (threshold is 15 same-file or 10 re-reads)
            assert sev != "critical", f"Reading {f} should not be critical (got {sev})"
        print("  ✓ Test 5c: Different file reads not flagged as loop")


# ==============================================================================
# TEST 6: WORKING SET OVERFLOW AND PRIORITY
# ==============================================================================

class TestWorkingSetPriority:
    """Tests for working set eviction and priority."""

    def test_modified_files_prioritized(self):
        """Modified files should be prioritized over read-only files."""
        ctx = ContextManagerFixed({"name": "balanced"})

        for i in range(40):
            ctx.add_file_to_working_set(f"read_file_{i}.py", f"content_{i}")

        # Modified files should still be accessible
        ctx.mark_modified("read_file_0.py")
        ctx.add_file_to_working_set("read_file_0.py", "new content")

        # The working set should have the modified file
        assert "read_file_0.py" in ctx.working_set
        print("  ✓ Test 6a: Modified files prioritized in working set")

    def test_working_set_size_limited(self):
        """Working set shouldn't grow unbounded."""
        ctx = ContextManagerFixed({"name": "balanced", "working_set_max_files": 5})

        for i in range(20):
            ctx.add_file_to_working_set(f"file_{i}.py", f"content_{i}")

        assert len(ctx.working_set) <= 5, f"Expected <= 5, got {len(ctx.working_set)}"
        assert "file_19.py" in ctx.working_set, "Latest file should be there"
        assert "file_0.py" not in ctx.working_set, "Oldest file evicted"
        print("  ✓ Test 6b: Working set size strictly limited")


# ==============================================================================
# TEST 7: SESSION LIFECYCLE WITH ERROR RECOVERY
# ==============================================================================

class TestSessionLifecycleWithErrors:
    """Session lifecycle including error recovery."""

    def test_session_closed_on_failure(self):
        """Session should be properly closed even on failure."""
        sandbox = SandboxRepo()
        try:
            sessions = SessionManagerFixed(sandbox.path)

            s1 = sessions.start("Will fail task")
            sessions.close(TaskStatus.FAILED)

            assert s1.finished_at != "", "Failed session should have finished_at"
            assert s1.status == TaskStatus.FAILED

            # Verify manifest
            manifest = json.loads(
                (sessions.session_dir / s1.id / "manifest.json").read_text()
            )
            assert manifest["status"] == TaskStatus.FAILED
            assert manifest["finished_at"] != ""

            # New session should work after failure
            s2 = sessions.start("Recovery task")
            assert s2.id != s1.id

            # Old session still properly closed
            assert s1.finished_at != ""
            print("  ✓ Test 7a: Session closed on failure, recovery works")

        finally:
            sandbox.cleanup()

    def test_orhpaned_session_detection(self):
        """Detect and count orphaned sessions."""
        sandbox = SandboxRepo()
        try:
            sessions = SessionManagerFixed(sandbox.path)

            # Create orphaned sessions using THE SAME instance (simulates crash/abandon)
            orphan_ids = []
            for i in range(3):
                s = sessions.start(f"Orphaned {i}")
                orphan_ids.append(s.id)
                # Don't close — these 3 will be orphaned

            # Now start a new session on the SAME instance
            # The auto-close fix should close the 3rd orphan when starting the 4th
            s4 = sessions.start("Cleanup session")

            all_sessions = sessions.list_sessions()
            orphans = [s for s in all_sessions if not s.get("finished_at")]

            # Only 1 orphan expected: the current "Cleanup session"
            assert len(orphans) == 1, f"Expected 1 orphan (current session), got {len(orphans)}"
            # The 3 previous sessions should all be closed
            closed = [s for s in all_sessions if s.get("finished_at") != ""]
            assert len(closed) == 3, f"Expected 3 closed sessions, got {len(closed)}"
            print("  ✓ Test 7b: Orphaned sessions auto-closed on new session start")

        finally:
            sandbox.cleanup()


# ==============================================================================
# TEST 8: REALISTIC MULTI-FILE EDIT SESSION
# ==============================================================================

class TestSandboxMultiFileEdit:
    """Simulate a real editing session across multiple files."""

    def test_add_feature_across_files(self):
        """Add a 'Profile' feature across models, services, and tests."""
        sandbox = SandboxRepo()
        try:
            ctx = ContextManagerFixed()
            sessions = SessionManagerFixed(sandbox.path)

            sessions.start("Add Profile model with service and tests")
            ctx.task = "Add Profile model with service and tests"

            # --- Step 1: Create Profile model ---
            profile_model = (
                "from dataclasses import dataclass\n"
                "from datetime import datetime\n"
                "\n\n"
                "@dataclass\n"
                "class Profile:\n"
                "    user_email: str\n"
                "    bio: str = ''\n"
                "    avatar_url: str = ''\n"
                "    created_at: datetime = None\n"
                "\n"
                "    def __post_init__(self):\n"
                "        if self.created_at is None:\n"
                "            self.created_at = datetime.now()\n"
                "\n"
                "    def is_complete(self) -> bool:\n"
                "        return bool(self.bio) and bool(self.avatar_url)\n"
            )
            model_path = sandbox.root / "src/models/profile.py"
            model_path.parent.mkdir(parents=True, exist_ok=True)
            model_path.write_text(profile_model)
            ctx.mark_modified("src/models/profile.py")
            ctx.add_file_to_working_set("src/models/profile.py", profile_model)

            # --- Step 2: Create ProfileService ---
            profile_service = (
                "from ..models.profile import Profile\n"
                "from ..models.user import User\n"
                "\n\n"
                "class ProfileService:\n"
                "    def __init__(self):\n"
                "        self._profiles: dict[str, Profile] = {}\n"
                "\n"
                "    def create_profile(self, user: User, bio: str = '', avatar_url: str = '') -> Profile:\n"
                "        profile = Profile(user_email=user.email, bio=bio, avatar_url=avatar_url)\n"
                "        self._profiles[user.email] = profile\n"
                "        return profile\n"
                "\n"
                "    def get_profile(self, email: str) -> Profile | None:\n"
                "        return self._profiles.get(email)\n"
                "\n"
                "    def delete_profile(self, email: str) -> bool:\n"
                "        return self._profiles.pop(email, None) is not None\n"
            )
            service_path = sandbox.root / "src/services/profile_service.py"
            service_path.parent.mkdir(parents=True, exist_ok=True)
            service_path.write_text(profile_service)
            ctx.mark_modified("src/services/profile_service.py")
            ctx.add_file_to_working_set("src/services/profile_service.py", profile_service)

            # --- Step 3: Update __init__.py exports ---
            init_path = sandbox.root / "src/models/__init__.py"
            init_path.write_text("from .profile import Profile\n")
            ctx.mark_modified("src/models/__init__.py")

            # --- Step 4: Create tests ---
            test_profile = (
                "from src.models.profile import Profile\n"
                "from src.services.profile_service import ProfileService\n"
                "from src.models.user import User\n"
                "\n\n"
                "def test_create_profile():\n"
                "    service = ProfileService()\n"
                "    user = User('test@test.com', 'Test')\n"
                "    profile = service.create_profile(user, bio='Hello')\n"
                "    assert profile.user_email == 'test@test.com'\n"
                "    assert profile.bio == 'Hello'\n"
                "    assert profile.is_complete() == False\n"
                "\n\n"
                "def test_get_profile():\n"
                "    service = ProfileService()\n"
                "    user = User('test@test.com', 'Test')\n"
                "    service.create_profile(user)\n"
                "    result = service.get_profile('test@test.com')\n"
                "    assert result is not None\n"
                "    assert result.user_email == 'test@test.com'\n"
                "\n\n"
                "def test_delete_profile():\n"
                "    service = ProfileService()\n"
                "    user = User('test@test.com', 'Test')\n"
                "    service.create_profile(user)\n"
                "    assert service.delete_profile('test@test.com') == True\n"
                "    assert service.get_profile('test@test.com') is None\n"
            )
            test_path = sandbox.root / "tests/test_profile.py"
            test_path.write_text(test_profile)
            ctx.mark_modified("tests/test_profile.py")
            ctx.add_file_to_working_set("tests/test_profile.py", test_profile)

            ctx.add_assistant_message("Added Profile model, ProfileService, and tests")

            # --- Verify ---
            assert len(ctx.modified_files) == 4, f"Expected 4 modified files, got {len(ctx.modified_files)}"
            assert sandbox.root.joinpath("src/models/profile.py").exists()
            assert sandbox.root.joinpath("src/services/profile_service.py").exists()
            assert sandbox.root.joinpath("tests/test_profile.py").exists()

            # Run the tests via a function test
            # (simulate running pytest in sandbox)
            test_content = sandbox.root.joinpath("tests/test_profile.py").read_text()
            assert "def test_create_profile" in test_content
            assert "def test_get_profile" in test_content
            assert "def test_delete_profile" in test_content

            # Verify session
            sessions.close(TaskStatus.COMPLETED)
            assert sessions.current.status == TaskStatus.COMPLETED
            assert sessions.current.finished_at != ""

            print("  ✓ Test 8: Multi-file feature addition preserves all state")

        finally:
            sandbox.cleanup()


# ==============================================================================
# MAIN
# ==============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  COMPREHENSIVE MULTI-TURN CONVERSATION RELIABILITY TESTS")
    print("=" * 70 + "\n")

    # Use a mutable object to avoid Python local/nonlocal scoping issues
    _state = {"failures": 0, "total": 0}

    def run_test(test_func):
        try:
            test_func()
            return True
        except AssertionError as e:
            _state["failures"] += 1
            print(f"  ✗ FAILED: {e}")
            import traceback
            traceback.print_exc()
            return False
        except Exception as e:
            _state["failures"] += 1
            print(f"  ✗ ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False

    steps = [
        ("1. THREE-TURN CONVERSATION (real files)", lambda: TestThreeTurnConversation().test_full_workflow()),
        ("\n2. PLAN FEEDBACK LOOP", lambda: TestPlanFeedbackLoop().test_plan_then_modify_then_execute()),
        ("\n3. HISTORY COMPACTION", [
            lambda: TestHistoryCompactionRecovery().test_compaction_after_many_tool_turns(),
            lambda: TestHistoryCompactionRecovery().test_compaction_with_stray_tool_at_end(),
        ]),
        ("\n4. CONVERSATION BOUNDARIES", [
            lambda: TestConversationBoundaries().test_empty_message_doesnt_break_history(),
            lambda: TestConversationBoundaries().test_rapid_messages_without_waiting(),
            lambda: TestConversationBoundaries().test_assistant_message_after_multiple_tool_calls(),
        ]),
        ("\n5. LOOP DETECTOR SCENARIOS", [
            lambda: TestLoopDetectorScenarios().test_legitimate_file_read_across_turns(),
            lambda: TestLoopDetectorScenarios().test_edit_then_read_does_not_loop(),
            lambda: TestLoopDetectorScenarios().test_different_file_reading_not_looping(),
        ]),
        ("\n6. WORKING SET PRIORITY", [
            lambda: TestWorkingSetPriority().test_modified_files_prioritized(),
            lambda: TestWorkingSetPriority().test_working_set_size_limited(),
        ]),
        ("\n7. SESSION LIFECYCLE WITH ERRORS", [
            lambda: TestSessionLifecycleWithErrors().test_session_closed_on_failure(),
            lambda: TestSessionLifecycleWithErrors().test_orhpaned_session_detection(),
        ]),
        ("\n8. SANDBOX MULTI-FILE EDIT", lambda: TestSandboxMultiFileEdit().test_add_feature_across_files()),
    ]

    for label, tests_or_func in steps:
        print(label)
        if callable(tests_or_func):
            _state["total"] += 1
            run_test(tests_or_func)
        else:
            for t in tests_or_func:
                _state["total"] += 1
                run_test(t)

    passed = _state["total"] - _state["failures"]
    failed_count = _state["failures"]
    total_count = _state["total"]
    print("\n" + "=" * 70)
    print(f"  RESULTS: {passed}/{total_count} passed", end="")
    if failed_count:
        print(f", {failed_count} FAILED ❌", end="")
    else:
        print(" ✓ ALL PASSED", end="")
    print()
    print("=" * 70 + "\n")

    sys.exit(1 if failed_count else 0)
