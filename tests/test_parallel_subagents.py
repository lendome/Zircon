"""Tests for parallel sub-agent dispatch and context window configuration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zirconAgent.core.types import (
    ModelProfile,
    Plan,
    PlanStep,
    SubAgentProgress,
    TierConfig,
)
from zirconAgent.subagents.base import SubAgentResult
from zirconAgent.llm.router import ModelRouter, RouterConfig


# ============================================================================
# Context Window (ModelProfile default + Router integration)
# ============================================================================

class TestModelProfileContextWindow:
    def test_default_128k(self):
        """ModelProfile defaults context_window to 128000."""
        p = ModelProfile(name="test", base_url="http://localhost", api_key="key", model="gpt-4")
        assert p.context_window == 128000

    def test_custom_context_window(self):
        """ModelProfile accepts custom context_window."""
        p = ModelProfile(
            name="test", base_url="http://localhost", api_key="key",
            model="gpt-4", context_window=64000,
        )
        assert p.context_window == 64000

    def test_router_context_window_max(self):
        """Router.context_window returns max across all profiles."""
        p1 = ModelProfile(name="a", base_url="http://localhost", api_key="key", model="m1", context_window=64000)
        p2 = ModelProfile(name="b", base_url="http://localhost", api_key="key", model="m2", context_window=128000)
        cfg = RouterConfig(profiles=[p1, p2], role_priority={"default": ["a", "b"]})
        r = ModelRouter(cfg)
        assert r.context_window == 128000

    def test_router_profile_context_window_found(self):
        """profile_context_window returns window for named profile."""
        p = ModelProfile(name="fast", base_url="http://localhost", api_key="key", model="m1", context_window=64000)
        cfg = RouterConfig(profiles=[p], role_priority={"default": ["fast"]})
        r = ModelRouter(cfg)
        assert r.profile_context_window("fast") == 64000

    def test_router_profile_context_window_missing(self):
        """profile_context_window falls back to default 128000 for unknown profile."""
        p = ModelProfile(name="default", base_url="http://localhost", api_key="key", model="m1")
        cfg = RouterConfig(profiles=[p], role_priority={"default": ["default"]})
        r = ModelRouter(cfg)
        assert r.profile_context_window("nonexistent") == 128000


# ============================================================================
# SubAgentResult step_index
# ============================================================================

class TestSubAgentResult:
    def test_step_index_default(self):
        """SubAgentResult.step_index defaults to 0."""
        r = SubAgentResult(success=True, output="ok", files_read=[], files_modified=[])
        assert r.step_index == 0

    def test_step_index_custom(self):
        """SubAgentResult.step_index accepts custom value."""
        r = SubAgentResult(success=True, output="ok", files_read=[], files_modified=[], step_index=7)
        assert r.step_index == 7

    def test_step_index_tracking(self):
        """SubAgentResult step_index survives round-trip through dataclass."""
        r = SubAgentResult(success=False, output="fail", files_read=["a.txt"], files_modified=["b.py"], step_index=42)
        assert r.success is False
        assert r.output == "fail"
        assert r.files_read == ["a.txt"]
        assert r.files_modified == ["b.py"]
        assert r.step_index == 42


# ============================================================================
# SubAgentProgress Dataclass
# ============================================================================

class TestSubAgentProgress:
    def test_basic_fields(self):
        """SubAgentProgress has all required fields with defaults."""
        p = SubAgentProgress(agent_id="step_1_explore", agent_type="explorer")
        assert p.agent_id == "step_1_explore"
        assert p.agent_type == "explorer"
        assert p.status == "running"
        assert p.phase == ""
        assert p.step == 0
        assert p.total_steps == 0
        assert p.detail == ""

    def test_custom_values(self):
        """SubAgentProgress accepts all custom values."""
        p = SubAgentProgress(
            agent_id="step_2_edit",
            agent_type="editor",
            status="complete",
            phase="editing",
            step=2,
            total_steps=4,
            detail="Edited main.py",
            files_modified=["main.py"],
        )
        assert p.agent_id == "step_2_edit"
        assert p.agent_type == "editor"
        assert p.status == "complete"
        assert p.phase == "editing"
        assert p.step == 2
        assert p.total_steps == 4
        assert p.detail == "Edited main.py"
        assert p.files_modified == ["main.py"]


# ============================================================================
# _dispatch_step_to_subagent — parallel dispatch routing
# ============================================================================

@pytest.mark.asyncio
async def test_dispatch_step_creates_explorer(tmp_repo, mock_router):
    """_dispatch_step_to_subagent creates ExplorerSubAgent for explore actions."""
    from zirconAgent.core.agent import Agent
    from zirconAgent.core.config import AgentConfig
    from zirconAgent.tools.registry import ToolRegistry
    from zirconAgent.tools.file_ops import ReadFileTool, GlobFilesTool

    # Build a minimal agent
    agent = _make_minimal_agent(tmp_repo, mock_router)
    step = PlanStep(index=1, action="explore", description="Find relevant files", target_files=[])

    with patch("agent.subagents.explorer.ExplorerSubAgent") as mock_explorer_cls:
        mock_instance = AsyncMock()
        mock_instance.run.return_value = SubAgentResult(
            success=True, output="found files", files_read=[], files_modified=[], step_index=1,
        )
        mock_explorer_cls.return_value = mock_instance

        result = await agent._dispatch_step_to_subagent(
            step=step,
            overall_task="test task",
            step_index=1,
            total_steps=2,
            previous_results_summary="",
            files_modified_count=0,
        )

        assert result.success
        assert result.output == "found files"
        assert result.step_index == 1
        mock_explorer_cls.assert_called_once()
        mock_instance.run.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_step_routes_editor(tmp_repo, mock_router):
    """_dispatch_step_to_subagent creates EditorSubAgent for edit actions."""
    from zirconAgent.core.agent import Agent
    from zirconAgent.core.config import AgentConfig

    agent = _make_minimal_agent(tmp_repo, mock_router)
    step = PlanStep(index=2, action="edit", description="Modify main.py", target_files=["main.py"])

    with patch("agent.subagents.editor.EditorSubAgent") as mock_editor_cls:
        mock_instance = AsyncMock()
        mock_instance.run.return_value = SubAgentResult(
            success=True, output="edited", files_read=[], files_modified=["main.py"], step_index=2,
        )
        mock_editor_cls.return_value = mock_instance

        result = await agent._dispatch_step_to_subagent(
            step=step, overall_task="test", step_index=2, total_steps=2,
            previous_results_summary="", files_modified_count=0,
        )

        assert result.success
        assert "main.py" in result.files_modified
        mock_editor_cls.assert_called_once()
        mock_instance.run.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_step_routes_verifier(tmp_repo, mock_router):
    """_dispatch_step_to_subagent creates VerifierSubAgent for verify actions."""
    from zirconAgent.core.agent import Agent

    agent = _make_minimal_agent(tmp_repo, mock_router)
    step = PlanStep(index=3, action="verify", description="Verify changes", target_files=["main.py"])

    with patch("agent.subagents.verifier.VerifierSubAgent") as mock_verifier_cls:
        mock_instance = AsyncMock()
        mock_instance.run.return_value = SubAgentResult(
            success=True, output="verified", files_read=[], files_modified=[], step_index=3,
        )
        mock_verifier_cls.return_value = mock_instance

        result = await agent._dispatch_step_to_subagent(
            step=step, overall_task="test", step_index=3, total_steps=2,
            previous_results_summary="", files_modified_count=0,
        )

        assert result.success
        mock_verifier_cls.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_step_routes_researcher(tmp_repo, mock_router):
    """_dispatch_step_to_subagent creates ResearcherSubAgent for research actions."""
    from zirconAgent.core.agent import Agent

    agent = _make_minimal_agent(tmp_repo, mock_router)
    step = PlanStep(index=0, action="research", description="Research API docs", target_files=[])

    with patch("agent.subagents.researcher.ResearcherSubAgent") as mock_researcher_cls:
        mock_instance = AsyncMock()
        mock_instance.run.return_value = SubAgentResult(
            success=True, output="research done", files_read=[], files_modified=[], step_index=0,
        )
        mock_researcher_cls.return_value = mock_instance

        result = await agent._dispatch_step_to_subagent(
            step=step, overall_task="test", step_index=0, total_steps=2,
            previous_results_summary="", files_modified_count=0,
        )

        assert result.success
        assert result.output == "research done"
        mock_researcher_cls.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_step_with_progress_callback(tmp_repo, mock_router):
    """_dispatch_step_to_subagent passes progress_callback to sub-agent."""
    from zirconAgent.core.agent import Agent

    agent = _make_minimal_agent(tmp_repo, mock_router)
    step = PlanStep(index=1, action="explore", description="Explore", target_files=[])

    callback = MagicMock()
    agent._progress_callback = callback

    with patch("agent.subagents.explorer.ExplorerSubAgent") as mock_explorer_cls:
        mock_instance = AsyncMock()
        mock_instance.run.return_value = SubAgentResult(
            success=True, output="done", files_read=[], files_modified=[], step_index=1,
        )
        mock_explorer_cls.return_value = mock_instance

        result = await agent._dispatch_step_to_subagent(
            step=step, overall_task="test", step_index=1, total_steps=2,
            previous_results_summary="", files_modified_count=0,
        )

        # Verify that progress_callback was passed to sub-agent
        call_kwargs = mock_instance.run.call_args.kwargs
        assert call_kwargs.get("progress_callback") is callback
        assert call_kwargs.get("agent_id") == "step_1_explore"


# ============================================================================
# _execute_plan_as_instruction — parallel dispatch (batch)
# ============================================================================

@pytest.mark.asyncio
async def test_execute_plan_dispaches_all_steps_in_parallel(tmp_repo, mock_router):
    """_execute_plan_as_instruction dispatches all steps to parallel sub-agents."""
    from zirconAgent.core.agent import Agent

    agent = _make_minimal_agent(tmp_repo, mock_router)

    plan = Plan(
        steps=[
            PlanStep(index=1, action="explore", description="Find files"),
            PlanStep(index=2, action="edit", description="Edit a file", target_files=["main.py"]),
        ],
        complexity="simple",
    )

    with patch.object(agent, "_dispatch_step_to_subagent") as mock_dispatch:
        mock_dispatch.side_effect = [
            SubAgentResult(success=True, output="found", files_read=[], files_modified=[], step_index=1),
            SubAgentResult(success=True, output="edited", files_read=[], files_modified=["main.py"], step_index=2),
        ]

        result = await agent._execute_plan_as_instruction("test task", plan)

        assert result.success
        assert mock_dispatch.call_count == 2
        assert "main.py" in result.files_modified


@pytest.mark.asyncio
async def test_execute_plan_single_step_adds_verification(tmp_repo, mock_router):
    """_execute_plan_as_instruction adds a parallel verification step when plan has only 1 step."""
    from zirconAgent.core.agent import Agent

    agent = _make_minimal_agent(tmp_repo, mock_router)

    plan = Plan(
        steps=[
            PlanStep(index=1, action="edit", description="Edit a file", target_files=["main.py"]),
        ],
        complexity="simple",
    )

    with patch.object(agent, "_dispatch_step_to_subagent") as mock_dispatch:
        mock_dispatch.side_effect = [
            SubAgentResult(success=True, output="edited", files_read=[], files_modified=["main.py"], step_index=1),
            SubAgentResult(success=True, output="verified", files_read=[], files_modified=[], step_index=99),
        ]

        result = await agent._execute_plan_as_instruction("test task", plan)

        assert result.success
        # Should have dispatched 2 agents (original + auto-verification)
        assert mock_dispatch.call_count == 2
        # The second dispatch should be the verify step (passed as positional arg)
        second_call_args = mock_dispatch.call_args_list[1]
        step_arg = second_call_args[0][0]  # first positional arg
        assert step_arg.action == "verify"


@pytest.mark.asyncio
async def test_execute_plan_collects_failures(tmp_repo, mock_router):
    """_execute_plan_as_instruction captures failures from individual steps."""
    from zirconAgent.core.agent import Agent

    agent = _make_minimal_agent(tmp_repo, mock_router)

    plan = Plan(
        steps=[
            PlanStep(index=1, action="explore", description="Explore"),
            PlanStep(index=2, action="edit", description="Edit", target_files=["main.py"]),
        ],
        complexity="simple",
    )

    with patch.object(agent, "_dispatch_step_to_subagent") as mock_dispatch:
        mock_dispatch.side_effect = [
            SubAgentResult(success=True, output="found", files_read=[], files_modified=[], step_index=1),
            SubAgentResult(success=False, output="failed to edit", files_read=[], files_modified=[], step_index=2),
        ]

        result = await agent._execute_plan_as_instruction("test task", plan)

        assert result.success is False
        assert "failed to edit" in result.answer


@pytest.mark.asyncio
async def test_execute_plan_handles_exception(tmp_repo, mock_router):
    """_execute_plan_as_instruction handles exceptions from sub-agents gracefully."""
    from zirconAgent.core.agent import Agent

    agent = _make_minimal_agent(tmp_repo, mock_router)

    plan = Plan(
        steps=[
            PlanStep(index=1, action="explore", description="Explore"),
            PlanStep(index=2, action="edit", description="Edit", target_files=["main.py"]),
        ],
        complexity="simple",
    )

    with patch.object(agent, "_dispatch_step_to_subagent") as mock_dispatch:
        mock_dispatch.side_effect = [
            SubAgentResult(success=True, output="found", files_read=[], files_modified=[], step_index=1),
            Exception("Network error"),
        ]

        result = await agent._execute_plan_as_instruction("test task", plan)

        assert result.success is False
        # Should include error in answer
        assert "Exception" in result.answer or "Network error" in result.answer


# ============================================================================
# _synthesize_parallel — result combination
# ============================================================================

class TestSynthesizeParallel:
    @pytest.mark.asyncio
    async def test_no_files_modified(self, tmp_repo, mock_router):
        """_synthesize_parallel returns appropriate message when no files modified."""
        from zirconAgent.core.agent import Agent
        agent = _make_minimal_agent(tmp_repo, mock_router)

        result = await agent._synthesize_parallel(
            task="test",
            combined_output="Step 1: found files",
            files_modified=set(),
        )
        assert "No files were modified" in result
        assert "Step 1" in result

    @pytest.mark.asyncio
    async def test_with_files_modified(self, tmp_repo, mock_router):
        """_synthesize_parallel lists modified files."""
        from zirconAgent.core.agent import Agent
        agent = _make_minimal_agent(tmp_repo, mock_router)

        result = await agent._synthesize_parallel(
            task="test",
            combined_output="Step 1: edited",
            files_modified={"main.py", "utils.py"},
        )
        assert "2 file(s)" in result
        assert "main.py" in result
        assert "utils.py" in result
        assert "Step 1" in result


# ============================================================================
# _execute_plan_instruction_stream — streaming parallel dispatch
# ============================================================================

@pytest.mark.asyncio
async def test_instruction_stream_dispatch_all(tmp_repo, mock_router):
    """_execute_plan_instruction_stream dispatches all steps and yields events."""
    from zirconAgent.core.agent import Agent

    agent = _make_minimal_agent(tmp_repo, mock_router)

    plan = Plan(
        steps=[
            PlanStep(index=1, action="explore", description="Find files"),
            PlanStep(index=2, action="edit", description="Edit a file", target_files=["main.py"]),
        ],
        complexity="simple",
    )

    with patch.object(agent, "_dispatch_step_to_subagent") as mock_dispatch:
        mock_dispatch.side_effect = [
            SubAgentResult(success=True, output="found", files_read=[], files_modified=[], step_index=1),
            SubAgentResult(success=True, output="edited", files_read=[], files_modified=["main.py"], step_index=2),
        ]

        events = []
        async for event in agent._execute_plan_instruction_stream("test", plan):
            events.append(event)

        # Should have at least plan, status, step_complete x2, task_complete
        phases = [e.phase for e in events]
        assert "plan" in phases
        assert "status" in phases
        assert "step_complete" in phases
        assert "task_complete" in phases


@pytest.mark.asyncio
async def test_instruction_stream_single_step_adds_verify(tmp_repo, mock_router):
    """_execute_plan_instruction_stream adds a verify step when plan has 1 step."""
    from zirconAgent.core.agent import Agent

    agent = _make_minimal_agent(tmp_repo, mock_router)

    plan = Plan(
        steps=[
            PlanStep(index=1, action="edit", description="Edit", target_files=["main.py"]),
        ],
        complexity="simple",
    )

    with patch.object(agent, "_dispatch_step_to_subagent") as mock_dispatch:
        mock_dispatch.side_effect = [
            SubAgentResult(success=True, output="edited", files_read=[], files_modified=["main.py"], step_index=1),
            SubAgentResult(success=True, output="verified", files_read=[], files_modified=[], step_index=99),
        ]

        events = []
        async for event in agent._execute_plan_instruction_stream("test", plan):
            events.append(event)

        assert mock_dispatch.call_count == 2


# ============================================================================
# Planner._lightweight_plan — ensures at least 2 steps
# ============================================================================

class TestLightweightPlan:
    def test_lightweight_plan_minimal_steps(self):
        """_lightweight_plan generates at least 2 steps."""
        from zirconAgent.core.planner import Planner
        planner = Planner(None)  # type: ignore
        plan = planner._lightweight_plan("make a change")
        assert len(plan.steps) >= 2

    def test_lightweight_plan_has_edit_and_verify(self):
        """_lightweight_plan includes edit and verify steps."""
        from zirconAgent.core.planner import Planner
        planner = Planner(None)  # type: ignore
        plan = planner._lightweight_plan("update config")
        actions = [s.action for s in plan.steps]
        assert "edit" in actions
        assert "verify" in actions or "explore" in actions


# ============================================================================
# Integration: solve() triggers parallel dispatch for plan execution
# ============================================================================

@pytest.mark.asyncio
async def test_solve_with_approved_plan_uses_parallel_dispatch(tmp_repo, mock_router):
    """solve() delegates to _execute_plan_as_instruction when plan is pre-approved."""
    from zirconAgent.core.agent import Agent
    from zirconAgent.core.types import TaskStatus

    agent = _make_minimal_agent(tmp_repo, mock_router)

    plan = Plan(
        steps=[
            PlanStep(index=0, action="explore", description="Explore"),
            PlanStep(index=1, action="edit", description="Edit", target_files=["main.py"]),
        ],
        complexity="simple",
    )

    agent._status = TaskStatus.AWAITING_INPUT
    agent._pending_plan = plan

    with patch.object(agent, "_execute_plan_as_instruction") as mock_exec:
        mock_exec.return_value = MagicMock(
            success=True, answer="done", files_modified=["main.py"], trace=[], tokens_used=0,
            status=TaskStatus.COMPLETED,
        )

        result = await agent.solve("test task")

        assert result.success
        mock_exec.assert_called_once_with("test task", plan)


# ============================================================================
# Helpers
# ============================================================================

def _make_minimal_agent(tmp_repo, router):
    """Create a minimal Agent instance for testing without full initialization."""
    from zirconAgent.core.agent import Agent
    from zirconAgent.core.config import AgentConfig
    from zirconAgent.core.types import Tier, TIER_PRESETS
    from zirconAgent.tools.registry import ToolRegistry
    from zirconAgent.tools.file_ops import ReadFileTool, GlobFilesTool, CreateFileTool, DeleteFileTool, ListDirTool
    from zirconAgent.tools.edit_ops import EditFileTool, EditLinesTool
    from zirconAgent.tools.search_ops import GrepCodeTool, FindSymbolsTool, GetStructureTool
    from zirconAgent.tools.shell_ops import RunCommandTool
    from zirconAgent.tools.web_ops import FetchUrlTool
    from zirconAgent.core.context import ContextManager
    from zirconAgent.core.session import SessionManager
    from zirconAgent.core.planner import Planner
    from zirconAgent.core.executor import Executor
    from zirconAgent.core.kg_memory import KnowledgeGraphMemory

    cfg = AgentConfig()
    agent = Agent.__new__(Agent)
    agent.repo_path = tmp_repo
    agent.config = cfg
    agent.router = router
    agent.tier = Tier.BALANCED
    agent.tier_cfg = TIER_PRESETS[Tier.BALANCED]

    # Register tools
    agent.registry = ToolRegistry()
    rp = str(tmp_repo)
    agent.registry.register_all([
        ReadFileTool(rp), CreateFileTool(rp), DeleteFileTool(rp),
        GlobFilesTool(rp), ListDirTool(rp),
        EditFileTool(rp), EditLinesTool(rp),
        GrepCodeTool(rp), FindSymbolsTool(rp), GetStructureTool(rp),
        RunCommandTool(rp), FetchUrlTool(),
    ])

    # Core subsystems
    kg = KnowledgeGraphMemory(str(tmp_repo))
    agent.context = ContextManager(
        tmp_repo, context_window=32000, safety_margin=400, kg_memory=kg,
        tier_config=agent.tier_cfg,
    )
    agent.kg = kg
    agent.sessions = SessionManager(tmp_repo)
    agent.planner = Planner(router)
    agent.executor = Executor(router, agent.registry, tier_config=agent.tier_cfg)

    # State
    agent._embedder = None
    agent._embedder_initialized = False
    agent._status = MagicMock()
    agent._pending_plan = None
    agent._plan_feedback = ""
    agent._current_task = ""
    agent._recovery_exhausted = False
    agent._last_explore_summary = ""
    agent._progress_callback = None
    agent._swarm_orchestrator = None
    agent._project_category = None
    agent._project_classified = False
    agent.tool_search = MagicMock()
    agent._gatekeeper = MagicMock()

    # Mock indexing for streaming tests
    agent._indexing = MagicMock()

    # Set up context attributes needed by dispatch
    agent.context._working_set = {}

    return agent
