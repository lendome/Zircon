from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from zirconAgent.core.advisor import Advisor
from zirconAgent.core.agent import Agent
from zirconAgent.core.config import RouterConfig, load_config
from zirconAgent.core.executor import Executor
from zirconAgent.core.types import (
    LLMResponse,
    PlanDecision,
    StreamChunk,
    TaskStatus,
    Tier,
    TierConfig,
    TIER_PRESETS,
)
from zirconAgent.llm.router import ModelRouter
from zirconAgent.tests.mocks import (
    make_router,
    make_stream_router,
    tool_call_response,
    tool_response,
)
from zirconAgent.tools.registry import ToolRegistry
from zirconAgent.tools.file_ops import GlobFilesTool

_PLAN = (
    "### Execution Plan ###\n"
    "- **Objective**: Add a health endpoint\n"
    "- **Target Tone/Style**: terse, idiomatic Python\n"
    "- **Step-by-Step Instructions**:\n"
    "  1. Open app.py\n"
    "  2. Add /health route\n"
    "  3. Return {\"status\": \"ok\"}\n"
    "- **Key Constraints**: no new dependencies; keep under 20 lines"
)


def _enabled_tier() -> TierConfig:
    return TierConfig(name="quality", advisor_enabled=True)


class TestAdvisor:
    @pytest.mark.asyncio
    async def test_disabled_returns_none_without_calling_router(self):
        router = make_router()
        advisor = Advisor(router, tier_config=TierConfig(name="balanced"))
        result = await advisor.advise("add a health endpoint")
        assert result is None
        router.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_enabled_returns_plan_and_uses_advisor_role(self):
        router = make_router()
        router.generate = AsyncMock(return_value=LLMResponse(content=_PLAN))
        tier = _enabled_tier()
        advisor = Advisor(router, tier_config=tier)

        result = await advisor.advise("add a health endpoint", context_summary="repo map here")

        assert result == _PLAN
        router.generate.assert_awaited_once()
        kwargs = router.generate.await_args.kwargs
        assert kwargs["role"] == "advisor"
        assert kwargs["max_tokens"] == tier.advisor_max_tokens
        system_msg = kwargs["messages"][0]["content"]
        assert "repo map here" in system_msg

    @pytest.mark.asyncio
    async def test_context_summary_is_trimmed(self):
        router = make_router()
        router.generate = AsyncMock(return_value=LLMResponse(content=_PLAN))
        tier = _enabled_tier()
        tier.advisor_context_max_chars = 100
        advisor = Advisor(router, tier_config=tier)

        await advisor.advise("task", context_summary="x" * 10000)

        system_msg = router.generate.await_args.kwargs["messages"][0]["content"]
        assert "x" * 100 in system_msg
        assert "x" * 101 not in system_msg

    @pytest.mark.asyncio
    async def test_empty_response_returns_none(self):
        router = make_router()
        router.generate = AsyncMock(return_value=LLMResponse(content="   "))
        advisor = Advisor(router, tier_config=_enabled_tier())
        assert await advisor.advise("task") is None

    @pytest.mark.asyncio
    async def test_router_error_propagates(self):
        router = make_router()
        router.generate = AsyncMock(side_effect=RuntimeError("provider down"))
        advisor = Advisor(router, tier_config=_enabled_tier())
        with pytest.raises(RuntimeError):
            await advisor.advise("task")


class TestAdvisorCheckin:
    @pytest.mark.asyncio
    async def test_disabled_returns_none_without_calling_router(self):
        router = make_router()
        advisor = Advisor(router, tier_config=TierConfig(name="balanced"))
        assert await advisor.check_in("task", turn=10, trajectory_digest="digest") is None
        router.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_enabled_returns_feedback_with_task_and_digest(self):
        feedback = "### Advisor Feedback ###\n- **Approved**: good file targeting"
        router = make_router()
        router.generate = AsyncMock(return_value=LLMResponse(content=feedback))
        advisor = Advisor(router, tier_config=_enabled_tier())

        result = await advisor.check_in("build the thing", turn=10, trajectory_digest="-> read_file(app.py)")

        assert result == feedback
        kwargs = router.generate.await_args.kwargs
        assert kwargs["role"] == "advisor"
        user_msg = kwargs["messages"][1]["content"]
        assert "build the thing" in user_msg
        assert "turn 10" in user_msg
        assert "-> read_file(app.py)" in user_msg

    @pytest.mark.asyncio
    async def test_empty_feedback_returns_none(self):
        router = make_router()
        router.generate = AsyncMock(return_value=LLMResponse(content=""))
        advisor = Advisor(router, tier_config=_enabled_tier())
        assert await advisor.check_in("task", turn=10, trajectory_digest="d") is None


class TestExecutorAdvisorCheckin:
    def _executor(self, tmp_path, router, interval: int) -> Executor:
        registry = ToolRegistry()
        registry.register_all([GlobFilesTool(str(tmp_path))])
        tier = TierConfig(
            name="quality",
            advisor_enabled=True,
            advisor_checkin_interval=interval,
        )
        return Executor(router, registry, tier_config=tier)

    def _loop_responses(self, tool_turns: int) -> list[LLMResponse]:
        # Distinct patterns per turn so the loop detector never escalates.
        patterns = ["*.py", "*.md", "*.txt", "*.json", "*.yaml", "*.toml"]
        responses = [
            tool_call_response([("glob_files", {"pattern": patterns[i % len(patterns)]})])
            for i in range(tool_turns)
        ]
        responses.append(tool_response("final answer"))
        return responses

    @pytest.mark.asyncio
    async def test_checkin_fires_at_interval_and_injects_note(self, tmp_path):
        router = make_router(self._loop_responses(tool_turns=6))
        executor = self._executor(tmp_path, router, interval=3)
        callback = AsyncMock(return_value="- **Approved**: systematic exploration")
        executor.advisor_callback = callback

        result = await executor.run_tool_loop(
            [{"role": "user", "content": "survey the repo"}], max_turns=10
        )

        assert result.success and result.output == "final answer"
        fired_at = [call.args[0] for call in callback.await_args_list]
        assert fired_at == [3, 6]
        # Task text is passed through for the advisor prompt.
        assert callback.await_args_list[0].args[1] == "survey the repo"
        notes = [m for m in result.history_turns
                 if m.get("role") == "system" and "<advisor_feedback" in (m.get("content") or "")]
        assert len(notes) == 2
        assert 'turn="3"' in notes[0]["content"]
        assert "systematic exploration" in notes[0]["content"]
        # Trace surfaces the check-in for the TUI.
        assert any(e.phase == "advisor_checkin" for e in result.trace)

    @pytest.mark.asyncio
    async def test_checkin_failure_does_not_break_loop(self, tmp_path):
        router = make_router(self._loop_responses(tool_turns=4))
        executor = self._executor(tmp_path, router, interval=2)
        executor.advisor_callback = AsyncMock(side_effect=RuntimeError("advisor down"))

        result = await executor.run_tool_loop(
            [{"role": "user", "content": "survey the repo"}], max_turns=10
        )

        assert result.success and result.output == "final answer"
        notes = [m for m in result.history_turns
                 if m.get("role") == "system" and "<advisor_feedback" in (m.get("content") or "")]
        assert notes == []

    @pytest.mark.asyncio
    async def test_no_callback_means_no_checkin(self, tmp_path):
        router = make_router(self._loop_responses(tool_turns=4))
        executor = self._executor(tmp_path, router, interval=2)

        result = await executor.run_tool_loop(
            [{"role": "user", "content": "survey the repo"}], max_turns=10
        )

        assert result.success
        assert not any(e.phase == "advisor_checkin" for e in result.trace)

    @pytest.mark.asyncio
    async def test_stream_loop_emits_advisor_chunk(self, tmp_path):
        """run_tool_loop_stream must surface the full memo to the TUI."""
        registry = ToolRegistry()
        registry.register_all([GlobFilesTool(str(tmp_path))])
        tier = TierConfig(name="quality", advisor_enabled=True, advisor_checkin_interval=2)
        responses = [
            tool_call_response([("glob_files", {"pattern": "*.py"})]),
            tool_call_response([("glob_files", {"pattern": "*.md"})]),
            tool_response("all done, here is the survey"),
        ]
        executor = Executor(make_stream_router(responses), registry, tier_config=tier)
        executor.advisor_callback = AsyncMock(return_value="- **Approved**: thorough sweep")

        chunks = [c async for c in executor.run_tool_loop_stream(
            [{"role": "user", "content": "survey the repo"}], max_turns=10
        )]

        advisor_chunks = [c for c in chunks if c.advisor_feedback]
        assert len(advisor_chunks) == 1
        assert advisor_chunks[0].advisor_feedback == "- **Approved**: thorough sweep"
        assert "🧭" in advisor_chunks[0].progress_label
        assert "turn 2" in advisor_chunks[0].progress_label
        notes = [m for m in executor._last_history_turns
                 if m.get("role") == "system" and "<advisor_feedback" in (m.get("content") or "")]
        assert len(notes) == 1

    def test_transport_serializes_advisor_feedback(self):
        """The daemon/local transport must carry the memo to remote TUIs."""
        from zirconAgent.cli.daemon.transport import LocalTransport

        d = LocalTransport._chunk_to_dict(StreamChunk(advisor_feedback="memo", progress_label="p"))

        assert d["advisor_feedback"] == "memo"
        assert d["progress_label"] == "p"


class _StubContext:
    repo_map_text = ""

    def __init__(self):
        self.notes: list[str] = []

    def add_note(self, note: str) -> None:
        self.notes.append(note)


class TestAgentAdvisorWiring:
    def _agent(self, router) -> Agent:
        agent = Agent.__new__(Agent)
        agent._advisor = Advisor(router, tier_config=_enabled_tier())
        agent.context = _StubContext()
        return agent

    @pytest.mark.asyncio
    async def test_safe_advise_swallows_errors(self):
        router = make_router()
        router.generate = AsyncMock(side_effect=RuntimeError("provider down"))
        agent = self._agent(router)
        assert await agent._safe_advise("task") is None

    @pytest.mark.asyncio
    async def test_advisor_note_injected_with_strict_adherence(self):
        router = make_router()
        router.generate = AsyncMock(return_value=LLMResponse(content=_PLAN))
        agent = self._agent(router)

        plan = await agent._safe_advise("add a health endpoint")
        assert plan == _PLAN
        agent._inject_advisor_note(plan)

        assert len(agent.context.notes) == 1
        note = agent.context.notes[0]
        assert "### ADVISOR EXECUTION PLAN ###" in note
        assert _PLAN in note
        assert "Strictly adhere" in note


class TestAdvisorConfig:
    def test_models_yaml_advisor_profile_and_role(self):
        import yaml
        from pathlib import Path

        yaml_path = Path(__file__).parent.parent / "models.yaml"
        declared = yaml.safe_load(yaml_path.read_text())["profiles"]["advisor"]

        router_cfg, _ = load_config()
        router = ModelRouter(router_cfg)

        candidates = router.select("advisor")

        assert candidates, "advisor role must resolve to at least one profile"
        assert candidates[0].name == "advisor"
        # Profile mirrors whatever models.yaml declares (robust to model swaps).
        assert candidates[0].model == declared["model"]
        assert candidates[0].reasoning_effort == declared.get("reasoning_effort")
        assert any(p.name == "default" for p in candidates[1:]), \
            "default profile must remain as fallback for the advisor role"

    def test_quality_preset_enables_advisor(self):
        from zirconAgent.core.types import Tier, TIER_PRESETS

        assert TIER_PRESETS[Tier.QUALITY].advisor_enabled is True
        assert TIER_PRESETS[Tier.BALANCED].advisor_enabled is False
        assert TIER_PRESETS[Tier.LOW].advisor_enabled is False


class TestAdvisorModelTracking:
    @pytest.mark.asyncio
    async def test_last_model_from_response(self):
        router = make_router()
        router.generate = AsyncMock(return_value=LLMResponse(content=_PLAN, model="kimi-k3"))
        advisor = Advisor(router, tier_config=_enabled_tier())
        await advisor.advise("task")
        assert advisor.last_model == "kimi-k3"

    @pytest.mark.asyncio
    async def test_last_model_falls_back_to_profile(self):
        router = make_router()
        router.generate = AsyncMock(return_value=LLMResponse(content=_PLAN, model=""))
        advisor = Advisor(router, tier_config=_enabled_tier())
        await advisor.advise("task")
        # make_router's only profile is "test-model" with the default role,
        # which the advisor role falls back to.
        assert advisor.last_model == "test-model"

    def test_agent_exposes_advisor_model_name(self):
        router = make_router()
        agent = Agent.__new__(Agent)
        agent._advisor = Advisor(router, tier_config=_enabled_tier())
        assert agent._advisor_model_name() == "test-model"
        agent._advisor.last_model = "kimi-k3"
        assert agent._advisor_model_name() == "kimi-k3"


class _ChatStubContext:
    repo_map_text = ""
    repo_map_built = True
    repo_map: dict = {}

    def __init__(self):
        self.notes: list[str] = []
        self.history: list[dict] = []

    def add_note(self, note: str) -> None:
        self.notes.append(note)

    def add_user_message(self, m: str) -> None:
        self.history.append({"role": "user", "content": m})

    def add_assistant_message(self, m: str) -> None:
        self.history.append({"role": "assistant", "content": m})

    def build_messages(self, system: str, tool_desc: str = "") -> list[dict]:
        return [{"role": "system", "content": system}] + list(self.history)

    def mark_modified(self, f: str) -> None:
        pass


class TestChatStreamAdvisor:
    """The interactive chat path must run the same advisor step as solve()."""

    def _agent(self, advisor_router, loop_router):
        tier = copy.deepcopy(TIER_PRESETS[Tier.QUALITY])
        agent = Agent.__new__(Agent)
        agent.tier_cfg = tier
        agent._status = TaskStatus.IDLE
        agent._pending_plan = None
        agent._recovery_exhausted = False
        agent._plan_feedback = ""
        agent._project_category = "test"
        agent.sessions = MagicMock()
        agent.context = _ChatStubContext()
        agent._advisor = Advisor(advisor_router, tier_config=tier)
        agent._gatekeeper = MagicMock()
        agent._ensure_project_classified = AsyncMock()
        agent._get_system_prompt = lambda: ""
        agent._dump_messages = lambda *a, **k: None
        registry = ToolRegistry()
        agent.registry = registry
        agent.executor = Executor(loop_router, registry, tier_config=tier)
        agent.executor.advisor_callback = agent._advisor_checkin
        agent.executor.advisor_model = agent._advisor_model_name()
        return agent

    @pytest.mark.asyncio
    async def test_chat_stream_emits_advisor_plan_with_model(self):
        order: list[str] = []
        advisor_router = make_router()

        async def _adv_gen(**kwargs):
            order.append("advisor")
            assert kwargs["role"] == "advisor"
            return LLMResponse(content=_PLAN, model="kimi-k3")

        advisor_router.generate = _adv_gen

        agent = self._agent(
            advisor_router,
            make_stream_router([tool_response("Hello! How can I help you today?")]),
        )

        async def _decide(*a, **k):
            order.append("gatekeeper")
            return PlanDecision(needs_plan=False, reason="greeting")

        agent._gatekeeper.decide = _decide

        chunks = [c async for c in agent.chat_stream("hi there")]

        assert order == ["advisor", "gatekeeper"]
        plan_chunks = [c for c in chunks if c.advisor_plan]
        assert len(plan_chunks) == 1
        assert plan_chunks[0].advisor_plan == _PLAN
        # The chunk carries the advisor model so the TUI shows it, not glm.
        assert plan_chunks[0].model == "kimi-k3"
        labels = [c.progress_label for c in chunks]
        assert any("Consulting advisor (kimi-k3)" in l or "Consulting advisor (test-model)" in l for l in labels)
        assert any("### ADVISOR EXECUTION PLAN ###" in n for n in agent.context.notes)

    @pytest.mark.asyncio
    async def test_chat_stream_without_advisor_when_disabled(self):
        agent = self._agent(
            make_router(),
            make_stream_router([tool_response("Hello!")]),
        )
        agent.tier_cfg.advisor_enabled = False
        agent._advisor.tier = agent.tier_cfg
        agent._gatekeeper.decide = AsyncMock(
            return_value=PlanDecision(needs_plan=False, reason="greeting")
        )

        chunks = [c async for c in agent.chat_stream("hi there")]

        assert not [c for c in chunks if c.advisor_plan]
        assert agent.context.notes == []


class TestReadFileCaps:
    @pytest.mark.asyncio
    async def test_default_window_is_400(self, tmp_path):
        from zirconAgent.tools.file_ops import ReadFileTool

        (tmp_path / "big.py").write_text("\n".join(f"line {i}" for i in range(1, 701)))
        tool = ReadFileTool(str(tmp_path))
        result = await tool.run("big.py")
        assert result.startswith("[Lines 1-400 of 700]")
        assert "more lines available" in result

    @pytest.mark.asyncio
    async def test_explicit_range_exceeds_default_window(self, tmp_path):
        from zirconAgent.tools.file_ops import ReadFileTool

        (tmp_path / "big.py").write_text("\n".join(f"line {i}" for i in range(1, 701)))
        tool = ReadFileTool(str(tmp_path))
        result = await tool.run("big.py", start=1, end=700)
        assert result.startswith("[Lines 1-700 of 700]")
        assert "more lines available" not in result

    @pytest.mark.asyncio
    async def test_explicit_range_hard_capped(self, tmp_path):
        from zirconAgent.tools.file_ops import ReadFileTool

        (tmp_path / "huge.py").write_text("\n".join(f"line {i}" for i in range(1, 3001)))
        tool = ReadFileTool(str(tmp_path))
        result = await tool.run("huge.py", start=1, end=3000)
        assert result.startswith("[Lines 1-2000 of 3000]")
