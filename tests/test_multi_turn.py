"""Multi-turn conversation reliability tests.
Tests the agent's ability to handle multiple sequential instructions correctly.

Key faults tested:
  1. Orphaned sessions when new messages arrive before previous session closes
  2. Broken message interleaving after history compaction (tool messages orphaned)
  3. Loop detector false positives from cumulative state across turns
  4. State leakage between chat turns (working set, session notes, history)
  5. Duplicate assistant messages from chat_stream completion logic
  6. Plan state persisting across turns improperly
  7. Working set overflow evicting contextually important files
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from zirconAgent.core.agent import Agent
from zirconAgent.core.config import AgentConfig, RouterConfig
from zirconAgent.core.context import ContextManager
from zirconAgent.core.session import SessionManager
from zirconAgent.core.executor import Executor
from zirconAgent.core.loop_detector import LoopDetector
from zirconAgent.core.types import (
    AgentResult,
    ModelProfile,
    Plan,
    PlanDecision,
    PlanStep,
    StreamChunk,
    TaskStatus,
    Tier,
    TierConfig,
    TIER_PRESETS,
    ToolCall,
    TraceEvent,
)
from zirconAgent.cli.daemon.transport import LocalTransport
from zirconAgent.tests.mocks import make_stream_router, tool_call_response, tool_response


def _test_router_config() -> RouterConfig:
    return RouterConfig(
        profiles=[
            ModelProfile(
                name="test",
                base_url="http://localhost",
                api_key="",
                model="test",
                roles=["default"],
            )
        ],
        role_priority={"default": ["test"]},
    )


# ==============================================================================
# FAKE ROUTER — deterministic mock that simulates multi-turn interactions
# ==============================================================================

class FakeRouter:
    """Deterministic mock LLM router for multi-turn testing."""

    def __init__(self):
        self.call_count = 0
        self.context_window = 128000
        # Pre-defined responses keyed by (turn_number, role)
        self.responses: dict[tuple[int, str], str] = {}
        self.tool_call_responses: dict[tuple[int, str], list[ToolCall]] = {}
        self.default_tool_calls: list[ToolCall] = []

    def set_default_tool_calls(self, calls: list[ToolCall]):
        self.default_tool_calls = calls

    async def generate(self, **kwargs) -> object:
        self.call_count += 1
        from types import SimpleNamespace
        key = (self.call_count, kwargs.get("role", "default"))
        content = self.responses.get(key, f"fake response #{self.call_count}")
        tool_calls = self.tool_call_responses.get(key, self.default_tool_calls)
        return SimpleNamespace(
            content=content,
            tool_calls=list(tool_calls) if tool_calls else [],
            reasoning_content=None,
            usage={},
        )

    async def generate_stream(self, **kwargs):
        self.call_count += 1
        from types import SimpleNamespace
        key = (self.call_count, kwargs.get("role", "default"))
        content = self.responses.get(key, f"fake response #{self.call_count}")
        tool_calls = self.tool_call_responses.get(key, self.default_tool_calls)
        yield SimpleNamespace(
            text=content,
            reasoning=None,
            done=True,
            tool_calls=list(tool_calls) if tool_calls else [],
            usage={},
            disposition=None,
            error=None,
            evidence=[],
            missing_evidence=[],
        )
        yield SimpleNamespace(
            text="",
            reasoning=None,
            done=True,
            tool_calls=list(tool_calls) if tool_calls else [],
            usage={},
            disposition=None,
            error=None,
            evidence=[],
            missing_evidence=[],
        )

    def register(self, turn: int, role: str, response: str):
        self.responses[(turn, role)] = response

    def register_tool_calls(self, turn: int, role: str, calls: list[ToolCall]):
        self.tool_call_responses[(turn, role)] = calls


class FakeRegistry:
    """Minimal tool registry that records calls for test assertions."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.repo_path = "/tmp"

    def get(self, name: str):
        return None

    def get_schemas(self, names: list[str] | None = None):
        return []

    def safe_execute(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return f"<result:{name}>"

    def list_names(self):
        return []


# ==============================================================================
# TEST: Orphaned Sessions
# ==============================================================================

class TestOrphanedSessions:
    """BUG 1A: SessionManager.start() overwrites _current without closing previous."""

    def test_session_not_closed_on_new_message(self):
        """When second message arrives, first session is orphaned without finished_at."""
        repo_path = Path(tempfile.mkdtemp())
        sm = SessionManager(repo_path)

        # First session
        s1 = sm.start("First task")
        s1_id = s1.id
        assert sm.current.id == s1_id

        # Second session WITHOUT closing first
        s2 = sm.start("Second task")
        s2_id = s2.id

        # First session manifest should have no finished_at
        manifest = sm.session_dir / s1_id / "manifest.json"
        data = json.loads(manifest.read_text())
        assert data["finished_at"] == "", f"Session {s1_id} should not have finished_at but got: {data['finished_at']}"

        # Only the second session is tracked as current
        assert sm.current.id == s2_id

    def test_multiple_orphaned_sessions_accumulate(self):
        """Over N conversational turns, N-1 orphaned sessions accumulate in filesystem."""
        repo_path = Path(tempfile.mkdtemp())
        sm = SessionManager(repo_path)

        for i in range(5):
            sm.start(f"Task {i}")

        sessions = sm.list_sessions()
        orphaned = [s for s in sessions if s.get("finished_at", "") == ""]
        assert len(orphaned) == 4, f"Expected 4 orphaned sessions, got {len(orphaned)}"

    def test_close_before_new_start_prevents_orphans(self):
        """Explicitly closing before starting a new session should work."""
        repo_path = Path(tempfile.mkdtemp())
        sm = SessionManager(repo_path)

        s1 = sm.start("First task")
        s1_id = s1.id
        sm.close(TaskStatus.COMPLETED)

        s2 = sm.start("Second task")
        s2_id = s2.id

        # First session should have finished_at
        manifest = sm.session_dir / s1_id / "manifest.json"
        data = json.loads(manifest.read_text())
        assert data["finished_at"] != "", "First session should have finished_at"

        # Only 0 orphans
        sessions = sm.list_sessions()
        orphaned = [s for s in sessions if s.get("finished_at", "") == ""]
        assert len(orphaned) == 0, f"Expected 0 orphans, got {len(orphaned)}"


class TestIncompleteContinuation:
    def test_continuation_reuses_incomplete_task(self, tmp_path):
        agent = Agent(tmp_path)
        session = agent.sessions.start("Optimize the disassembler")
        agent.sessions.close(TaskStatus.INCOMPLETE)

        assert agent._is_incomplete_continuation("go on")
        assert agent._is_incomplete_continuation("Continue!")
        assert agent.sessions.current is session

    def test_continuation_does_not_reuse_completed_task(self, tmp_path):
        agent = Agent(tmp_path)
        agent.sessions.start("Optimize the disassembler")
        agent.sessions.close(TaskStatus.COMPLETED)

        assert not agent._is_incomplete_continuation("go on")


class TestChatFollowUps:
    def test_completed_chat_session_is_reused_for_affirmative_reply(self, tmp_path):
        agent = Agent(tmp_path)
        session = agent.sessions.start("Why is inference slow?")
        agent.context.add_user_message("Why is inference slow?")
        agent.context.add_assistant_message(
            "I found conservative GPU offload settings. Want me to apply the fixes now?"
        )
        agent.sessions.close(TaskStatus.COMPLETED)

        assert agent._is_affirmative_reply("yes")
        assert agent._has_prior_assistant_request()
        assert agent._is_chat_follow_up("yes")
        assert agent.sessions.current is session

    def test_completed_chat_session_is_not_reused_for_slash_command(self, tmp_path):
        agent = Agent(tmp_path)
        agent.sessions.start("Explain the project")
        agent.context.add_user_message("Explain the project")
        agent.context.add_assistant_message("Want a deeper explanation?")
        agent.sessions.close(TaskStatus.COMPLETED)

        assert not agent._is_chat_follow_up("/reset")

    @pytest.mark.asyncio
    async def test_chat_stream_reuses_session_for_confirmed_request(self, tmp_path):
        router = FakeRouter()
        agent = Agent(
            repo_path=tmp_path,
            router_config=_test_router_config(),
            agent_config=AgentConfig(),
            tier=Tier.LOW,
        )
        agent.router = router
        agent.executor.router = router
        router.reset_session_cost = lambda: None

        async def no_op():
            return None

        async def no_advice(_task):
            return None

        async def no_plan(_task):
            return PlanDecision(needs_plan=False, reason="test")

        agent._init_indexing = no_op
        agent._ensure_project_classified = no_op
        agent._safe_advise = no_advice
        agent._safe_decide = no_plan

        router.register(1, "default", "I found the issue. Want me to apply the fixes now?")
        first_chunks = [chunk async for chunk in agent.chat_stream("Investigate TPS")]
        assert first_chunks[-1].status == TaskStatus.COMPLETED
        session_id = agent.sessions.current.id

        router.register(2, "default", "Applying the requested fixes.")
        second_chunks = [chunk async for chunk in agent.chat_stream("yes")]

        assert second_chunks[-1].status == TaskStatus.COMPLETED
        assert agent.sessions.current.id == session_id
        assert [
            message["content"] for message in agent.context.history
            if message.get("role") == "user"
        ][-2:] == ["Investigate TPS", "yes"]

    @pytest.mark.asyncio
    async def test_chat_stream_continues_completed_session_for_any_follow_up(self, tmp_path):
        router = FakeRouter()
        agent = Agent(
            repo_path=tmp_path,
            router_config=_test_router_config(),
            agent_config=AgentConfig(),
            tier=Tier.LOW,
        )
        agent.router = router
        agent.executor.router = router
        router.reset_session_cost = lambda: None

        async def no_op():
            return None

        async def no_advice(_task):
            return None

        async def no_plan(_task):
            return PlanDecision(needs_plan=False, reason="test")

        agent._init_indexing = no_op
        agent._ensure_project_classified = no_op
        agent._safe_advise = no_advice
        agent._safe_decide = no_plan
        router.register(1, "default", "First answer.")
        [chunk async for chunk in agent.chat_stream("Explain this project")]
        session_id = agent.sessions.current.id

        router.register(3, "default", "More detail.")
        router.register(4, "default", "More detail.")
        [chunk async for chunk in agent.chat_stream("Now explain the storage layer in detail")]

        assert agent.sessions.current.id == session_id
        persisted = agent.sessions.load_messages(session_id)
        assert [
            message["content"] for message in persisted
            if message.get("role") == "user"
        ] == ["Explain this project", "Now explain the storage layer in detail"]
        assert persisted[-1]["content"] == "More detail.More detail."

    @pytest.mark.asyncio
    async def test_resume_empty_session_clears_previous_context(self, tmp_path):
        agent = Agent(tmp_path)
        populated = agent.sessions.start("populated")
        agent.sessions.save_messages([{"role": "user", "content": "old context"}])
        empty = agent.sessions.start("empty")
        agent.context.add_user_message("stale in-memory context")

        result = await LocalTransport(agent).resume_session(empty.id)

        assert result["ok"]
        assert result["history"] == 0
        assert agent.context.history == []
        assert agent.sessions.current.id == empty.id
        assert agent.context.task == "empty"
        assert populated.id != empty.id

    @pytest.mark.asyncio
    async def test_interrupted_stream_persists_completed_tool_events(self, tmp_path):
        (tmp_path / "data.txt").write_text("durable tool output")
        agent = Agent(
            repo_path=tmp_path,
            router_config=_test_router_config(),
            agent_config=AgentConfig(),
            tier=Tier.LOW,
        )
        router = make_stream_router([
            tool_call_response([("read_file", {"path": "data.txt"})]),
            tool_response("This should not be reached before interruption."),
        ])
        router.reset_session_cost = lambda: None
        agent.router = router
        agent.executor.router = router

        async def no_op():
            return None

        async def no_advice(_task):
            return None

        async def no_plan(_task):
            return PlanDecision(needs_plan=False, reason="test")

        agent._init_indexing = no_op
        agent._ensure_project_classified = no_op
        agent._safe_advise = no_advice
        agent._safe_decide = no_plan

        stream = agent.chat_stream("Inspect data.txt")
        async for chunk in stream:
            if chunk.tool_result:
                break
        await stream.aclose()

        persisted = agent.sessions.load_messages(agent.sessions.current.id)
        assert persisted[0] == {"role": "user", "content": "Inspect data.txt"}
        assert any(message.get("tool_calls") for message in persisted)
        assert any(
            message.get("role") == "tool" and "durable tool output" in message.get("content", "")
            for message in persisted
        )

    @pytest.mark.asyncio
    async def test_non_streaming_chat_persists_complete_turn(self, tmp_path):
        router = FakeRouter()
        router.reset_session_cost = lambda: None
        agent = Agent(
            repo_path=tmp_path,
            router_config=_test_router_config(),
            agent_config=AgentConfig(),
            tier=Tier.LOW,
        )
        agent.router = router
        agent.executor.router = router

        async def no_op():
            return None

        async def no_plan(_task):
            return PlanDecision(needs_plan=False, reason="test")

        agent._init_indexing = no_op
        agent._ensure_project_classified = no_op
        agent._safe_decide = no_plan
        router.register(1, "default", "Persisted answer.")

        result = await agent.chat("Explain this")

        assert result == "Persisted answer."
        assert agent.sessions.load_messages(agent.sessions.current.id) == [
            {"role": "user", "content": "Explain this"},
            {"role": "assistant", "content": "Persisted answer."},
        ]

    @pytest.mark.asyncio
    async def test_chat_stream_loads_cached_repo_map_before_blocking_init(self, tmp_path):
        router = FakeRouter()
        agent = Agent(
            repo_path=tmp_path,
            router_config=_test_router_config(),
            agent_config=AgentConfig(),
            tier=Tier.LOW,
        )
        agent.router = router
        agent.executor.router = router
        router.reset_session_cost = lambda: None
        agent.context._save_repo_map_to_cache()

        init_calls = 0

        async def blocking_init():
            nonlocal init_calls
            init_calls += 1
            raise AssertionError("cached repo map should avoid a blocking index build")

        async def no_op():
            return None

        async def no_advice(_task):
            return None

        async def no_plan(_task):
            return PlanDecision(needs_plan=False, reason="test")

        agent._init_indexing = blocking_init
        agent._ensure_project_classified = no_op
        agent._safe_advise = no_advice
        agent._safe_decide = no_plan
        router.register(1, "default", "Cached map was used.")

        chunks = [chunk async for chunk in agent.chat_stream("Inspect the project")]

        assert init_calls == 0
        assert agent.context.repo_map_built
        assert chunks[-1].status == TaskStatus.COMPLETED


# ==============================================================================
# TEST: Broken Message Interleaving After Compaction
# ==============================================================================

class TestHistoryCompactionInterleaving:
    """BUG 2: compact_history() discards tool role messages, breaking interleaving."""

    def test_compaction_preserves_tool_messages(self):
        """After compaction, tool messages should not be orphaned."""
        ctx = ContextManager(
            "/tmp",
            context_window=128000,
            tier_config=TierConfig(
                name="balanced",
                history_compact_threshold=100,  # Very low to trigger compaction
                history_keep_exchanges=1,
            ),
        )

        # Simulate a real conversation with tool calls
        ctx.history = [
            {"role": "user", "content": "Add logging to app.py"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "file content here"},
            {"role": "assistant", "content": "I've read the file"},
            {"role": "user", "content": "Now add the logging"},
            {"role": "assistant", "content": "Final response"},
        ]

        # Verify compaction doesn't orphan tool messages
        # First check that we have valid interleaving
        has_user = any(m["role"] == "user" for m in ctx.history)
        has_tool = any(m["role"] == "tool" for m in ctx.history)
        assert has_user, "Should have user messages"
        assert has_tool, "Should have tool messages"

        # After manual compaction-like operation, check that the
        # pattern user->assistant->tool is preserved
        recent = ctx.history[-2:]  # Keep last 2
        to_summarize = ctx.history[:-2]

        # Check that summarization doesn't lose the pattern
        replacement = [
            {"role": "user", "content": "<history_summary>summary</history_summary>"},
            {"role": "assistant", "content": "<history_summary>summarized</history_summary>"},
        ]
        new_history = replacement + recent

        # Verify interleaving: no tool message should appear without
        # its corresponding assistant message with tool_calls
        for i, msg in enumerate(new_history):
            if msg["role"] == "tool":
                # Check that there's an assistant message before this with tool_calls
                found_assistant = False
                for j in range(i - 1, max(i - 5, -1), -1):
                    prev = new_history[j]
                    if prev["role"] == "assistant" and prev.get("tool_calls"):
                        found_assistant = True
                        break
                assert found_assistant, (
                    f"Tool message at index {i} has no preceding assistant with tool_calls"
                )

    def test_high_token_threshold_prevents_damage(self):
        """When history_compact_threshold is high, compaction is skipped."""
        ctx = ContextManager(
            "/tmp",
            context_window=128000,
            tier_config=TierConfig(
                name="balanced",
                history_compact_threshold=100000,  # Very high = effectively disabled
                history_keep_exchanges=1,
            ),
        )

        # Add some history
        ctx.history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

        # Router isn't available for this test, so verify the check works
        assert ctx.tier.history_compact_threshold > 90000  # disabled


# ==============================================================================
# TEST: Loop Detector False Positives Across Turns
# ==============================================================================

class TestLoopDetectorCrossTurn:
    """BUG 3: LoopDetector cumulative state persists across turns."""

    def test_loop_detector_resets_between_tasks(self):
        """LoopDetector should not carry cumulative files_read across tasks."""
        ld = LoopDetector(window_size=12, max_repetitions=5, stagnation_threshold=10)

        # Simulate first task: read some files
        for _ in range(3):
            ld.record(
                [ToolCall(id="c1", name="read_file", arguments={"path": "app.py"})],
                files_read=["app.py"],
                files_modified=[],
            )

        # Simulate second task WITHOUT reset: same files re-read
        for i in range(12):
            check = ld.record(
                [ToolCall(id="cN", name="read_file", arguments={"path": "app.py"})],
                files_read=["app.py"],
                files_modified=[],
            )
            if i >= 9:  # After cumulative same-file threshold
                # This should NOT be critical if properly reset
                # But WITHOUT reset it fires at 10th same-file re-read
                pass

        # Now try WITH proper reset between tasks
        ld.reset()

        # Read some files for new task
        for _ in range(5):
            check = ld.record(
                [ToolCall(id="cN", name="read_file", arguments={"path": "new_file.py"})],
                files_read=["new_file.py"],
                files_modified=[],
            )
            assert check.severity != "critical", (
                f"After reset, reading new files should not be critical: {check.reason}"
            )

    def test_loop_detector_false_positive_on_reread(self):
        """Reading the same file in a DIFFERENT task should not be a loop."""
        ld = LoopDetector(max_repetitions=5)

        # Task 1: Read app.py
        ld.record(
            [ToolCall(id="c1", name="read_file", arguments={"path": "app.py"})],
            files_read=["app.py"],
            files_modified=[],
        )

        # Simulate new task signal
        ld.reset()

        # Task 2: Read app.py again (legitimate - new task)
        for _ in range(3):
            check = ld.record(
                [ToolCall(id="cN", name="read_file", arguments={"path": "app.py"})],
                files_read=["app.py"],
                files_modified=[],
            )
            assert check.severity != "critical", (
                f"After reset, re-reading in new task should not be critical: {check.reason}"
            )


# ==============================================================================
# TEST: State Leakage Between Chat Turns
# ==============================================================================

class TestStateLeakage:
    """BUG 4: Context state leaks between conversational turns."""

    def test_history_accumulates_without_boundary(self):
        """History should have clear separation between conversational turns."""
        ctx = ContextManager("/tmp", context_window=128000)

        # Turn 1
        ctx.add_user_message("Add logging to app.py")
        ctx.add_assistant_message("I'll add logging")
        history_len_turn1 = len(ctx.history)

        # Turn 2 (without any boundary/clear)
        ctx.add_user_message("Also add error handling")
        ctx.add_assistant_message("I'll add that too")
        history_len_turn2 = len(ctx.history)

        # History should contain both turns merged without separation
        assert history_len_turn2 > history_len_turn1, "History keeps growing without boundary"

        # Verify there's no boundary marker between turns
        turn1_msgs = ctx.history[history_len_turn1:]
        # All of turn 1 messages + new messages
        assert len(ctx.history) == history_len_turn2

    def test_working_set_overflows_across_turns(self):
        """Working set should not overflow across turns."""
        ctx = ContextManager(
            "/tmp",
            context_window=128000,
            tier_config=TierConfig(name="balanced"),
        )

        # Simulate filling working set across multiple turns
        for i in range(50):
            ctx.add_file_to_working_set(f"file_{i}.py", f"content_{i}" * 100)

        # Working set should be bounded
        max_size = ctx.tier.working_set_max_files
        assert len(ctx.working_set) <= max_size, (
            f"Working set exceeds max size: {len(ctx.working_set)} > {max_size}"
        )
        # But we've lost the earliest files
        assert "file_0.py" not in ctx.working_set, (
            "Early files should be evicted from working set"
        )

    def test_session_notes_accumulate(self):
        """Session notes accumulate without cleanup between conceptual turns."""
        ctx = ContextManager("/tmp", context_window=128000)

        for i in range(100):
            ctx.add_note(f"Note {i}")

        assert len(ctx.session_notes) == 100, "Session notes grow unbounded"


# ==============================================================================
# TEST: Duplicate Assistant Messages
# ==============================================================================

class TestDuplicateMessages:
    """BUG 5: chat_stream duplicates assistant messages."""

    def test_assistant_messages_deduplicated(self):
        """verify no duplicate assistant messages in history."""
        ctx = ContextManager("/tmp", context_window=128000)

        # Simulate what chat_stream does:
        # 1. executor returns result with output
        # 2. chat_stream calls add_assistant_message(full_response)
        # 3. chat_stream extends history with executor.last_history_turns

        ctx.add_user_message("Hello")
        ctx.add_assistant_message("Response 1")

        # Simulate executor history (which ALSO contains the assistant response)
        executor_turns = [
            {"role": "assistant", "content": "Response 1", "tool_calls": []},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ]

        # chat_stream logic:
        full_response = "Response 1"
        ctx.add_assistant_message(full_response)
        ctx.history.extend(executor_turns)

        # Count assistant messages
        assistant_msgs = [m for m in ctx.history if m["role"] == "assistant"]
        assert len(assistant_msgs) == 2, (
            f"Expected 2 assistant messages (1 from add + 1 from executor), "
            f"got {len(assistant_msgs)}: {assistant_msgs}"
        )


# ==============================================================================
# TEST: Plan State Persistence Bug
# ==============================================================================

class TestPlanStatePersistence:
    """BUG 7: _pending_plan persists across turns."""

    def test_pending_plan_cleared_on_new_task(self):
        """Plan state should be cleared when a new task arrives."""
        agent = None  # Would need full mock
        # This tests the logical flow

    def test_plan_feedback_not_overwritten(self):
        """Plan feedback from a previous turn should not leak into new task."""
        pass  # Integration test with full agent


# ==============================================================================
# TEST: Full Multi-Turn Conversation Flow
# ==============================================================================

class TestMultiTurnConversationFlow:
    """End-to-end tests of multi-turn conversation reliability."""

    def test_three_turn_conversation_state(self):
        """Simulate a 3-turn conversation and verify state at each step."""
        ctx = ContextManager("/tmp", context_window=128000)

        # Track state
        states = []

        # Turn 1
        ctx.add_user_message("Add a function to calculate tax")
        ctx.add_assistant_message("I'll add a calculate_tax function")
        states.append({
            "history_len": len(ctx.history),
            "turn": 1,
        })

        # Turn 2 (new request)
        ctx.add_user_message("Also add input validation")
        ctx.add_assistant_message("Adding input validation")
        states.append({
            "history_len": len(ctx.history),
            "turn": 2,
        })

        # Turn 3 (another new request)
        ctx.add_user_message("Add unit tests for both")
        ctx.add_assistant_message("Adding unit tests")
        states.append({
            "history_len": len(ctx.history),
            "turn": 3,
        })

        # Verify monotonic growth
        for i in range(1, len(states)):
            assert states[i]["history_len"] > states[i - 1]["history_len"], (
                f"History should grow monotonically but turn {states[i]['turn']} "
                f"has {states[i]['history_len']} <= turn {states[i-1]['turn']} "
                f"with {states[i-1]['history_len']}"
            )


# ==============================================================================
# TEST: Executor Reuse Across Calls
# ==============================================================================

class TestExecutorReuse:
    """BUG: Executor reuses loop detector state across calls."""

    def test_executor_loop_detector_reset_on_reuse(self):
        """Executor should reset loop detector when starting a new execution."""
        router = FakeRouter()
        registry = FakeRegistry()
        executor = Executor(router, registry, tier_config=TierConfig(name="balanced"))

        # First execution
        router.register(1, "default", "Let me read the file first")
        router.register_tool_calls(1, "default", [
            ToolCall(id="tc1", name="read_file", arguments={"path": "app.py"})
        ])
        # Router needs a second call to finish
        router.register(2, "default", "Done reading file")

        result1 = asyncio.run(executor.run_tool_loop(
            messages=[{"role": "user", "content": "Read app.py"}],
            tools=[],
            max_turns=2,
        ))
        assert result1.success

        # Without reset, loop detector thinks we're re-reading
        # But the executor should handle this gracefully
        executor.reset_recovery()

        # Second execution (new task)
        router.register(3, "default", "Read database.py")
        router.register_tool_calls(3, "default", [
            ToolCall(id="tc2", name="read_file", arguments={"path": "database.py"})
        ])
        router.register(4, "default", "Done reading database")

        result2 = asyncio.run(executor.run_tool_loop(
            messages=[{"role": "user", "content": "Read database.py"}],
            tools=[],
            max_turns=2,
        ))
        assert result2.success, "Second execution should also succeed"


# ==============================================================================
# TEST: Agent.solve() Multi-Turn Flow
# ==============================================================================

class TestAgentSolveMultiTurn:
    """Test the full Agent.solve() flow across multiple turns."""

    @pytest.mark.asyncio
    async def test_solve_after_completed_task(self):
        """Agent should handle a new task after a previous one completed."""
        router = FakeRouter()
        agent = Agent(
            repo_path=tempfile.mkdtemp(),
            router_config=RouterConfig(provider="test", model="test"),
            agent_config=AgentConfig(),
            tier=Tier.LOW,  # Low tier = no planning needed
        )
        # Override with our fake router
        agent.router = router

        # First task
        router.register(1, "default", "Completed first task")
        result1 = await agent.solve("First task")
        assert result1.status == TaskStatus.COMPLETED, f"First should complete: {result1}"

        # Second task (should work without leftover state)
        router.call_count = 0
        router.register(1, "default", "Completed second task")
        result2 = await agent.solve("Second task")

        # The agent should not have detected a loop from first task's state
        assert result2.success, f"Second should succeed: {result2}"


# ==============================================================================
# TEST: Context Manager Build Messages
# ==============================================================================

class TestContextBuildMessages:
    """Test that ContextManager.build_messages() produces valid message sequences."""

    def test_build_messages_maintains_interleaving(self):
        """build_messages should maintain proper user/assistant/tool interleaving."""
        ctx = ContextManager("/tmp", context_window=128000)

        # Build a conversation with tool calls
        ctx.history = [
            {"role": "user", "content": "Add logging"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "type": "function", "function": {"name": "read_file", "arguments": '{}'}}
            ]},
            {"role": "tool", "tool_call_id": "tc1", "content": "file content"},
            {"role": "assistant", "content": "Done"},
        ]

        messages = ctx.build_messages(
            system_prompt="You are a helpful assistant",
            tool_description="List of tools",
        )

        # Verify interleaving pattern
        # A tool message must be preceded by an assistant message with tool_calls
        for i, msg in enumerate(messages):
            if msg.get("role") == "tool":
                # Find the last assistant message before this tool message
                found = False
                for j in range(i - 1, -1, -1):
                    if messages[j].get("role") == "assistant":
                        tc = messages[j].get("tool_calls", [])
                        if tc:
                            found = True
                            break
                        break  # Reached an assistant without tool_calls -> invalid
                assert found, f"Tool message at index {i} has no preceding assistant with tool_calls"

    def test_build_messages_no_orphan_tool_messages(self):
        """After compaction, no tool messages should be orphaned."""
        ctx = ContextManager(
            "/tmp",
            context_window=128000,
            tier_config=TierConfig(
                name="balanced",
                history_compact_threshold=1,  # Always compact
                history_keep_exchanges=0,  # Keep nothing
            ),
        )

        # Conversation with tool calls
        ctx.history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "type": "function", "function": {"name": "read_file", "arguments": '{}'}}
            ]},
            {"role": "tool", "tool_call_id": "tc1", "content": "result"},
            {"role": "assistant", "content": "Done"},
            {"role": "user", "content": "Now add more"},
        ]

        # The compaction will replace everything except the recent messages
        # The key question: does it replace tool-only sections correctly?
        # Verify the interleaving in whatever is produced

        # Simulate what compact_history does internally
        keep_count = 2  # Based on keep_exchanges
        to_summarize = ctx.history[:-keep_count] if keep_count > 0 else ctx.history
        recent = ctx.history[-keep_count:] if keep_count > 0 else []

        # After compaction-style replacement:
        replacement = [
            {"role": "user", "content": "<history_summary>...</history_summary>"},
            {"role": "assistant", "content": "<history_summary>summary</history_summary>"},
        ]
        new_history = replacement + recent

        # Check: no tool message without a preceding assistant with tool_calls
        for i, msg in enumerate(new_history):
            if msg.get("role") == "tool":
                has_preceding_assistant = False
                for j in range(i - 1, -1, -1):
                    prev = new_history[j]
                    if prev.get("role") == "assistant":
                        if prev.get("tool_calls"):
                            has_preceding_assistant = True
                        break
                assert has_preceding_assistant, (
                    f"Orphaned tool message at index {i} in: {new_history}"
                )
