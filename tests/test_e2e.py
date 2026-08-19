import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from zirconAgent.core.agent import Agent
from zirconAgent.core.config import AgentConfig, RouterConfig
from zirconAgent.core.types import LLMResponse, ToolCall, TaskStatus
from zirconAgent.llm.router import ModelRouter
from zirconAgent.tests.mocks import make_profile, tool_response, tool_call_response


def make_e2e_router(responses: list[LLMResponse]) -> ModelRouter:
    profile = make_profile("default", ["default", "planner", "fast"])
    cfg = RouterConfig(
        profiles=[profile],
        role_priority={"default": ["default"], "planner": ["default"], "fast": ["default"]},
        rate_limit_delay=0,
        max_retries=1,
    )
    router = ModelRouter(cfg)
    router.generate = AsyncMock(side_effect=responses)
    return router


def make_e2e_agent(tmp_path, responses: list[LLMResponse]) -> Agent:
    router = make_e2e_router(responses)
    cfg = AgentConfig()
    a = Agent.__new__(Agent)
    a.repo_path = tmp_path
    a.config = cfg
    a.router = router

    from zirconAgent.tools.registry import ToolRegistry
    from zirconAgent.tools.file_ops import ReadFileTool, CreateFileTool, GlobFilesTool, ListDirTool, DeleteFileTool
    from zirconAgent.tools.edit_ops import EditFileTool, EditLinesTool
    from zirconAgent.tools.search_ops import GrepCodeTool, FindSymbolsTool, GetStructureTool
    from zirconAgent.tools.shell_ops import RunCommandTool
    from zirconAgent.tools.web_ops import FetchUrlTool

    a.registry = ToolRegistry()
    rp = str(tmp_path)
    a.registry.register_all([
        ReadFileTool(rp), CreateFileTool(rp), DeleteFileTool(rp),
        GlobFilesTool(rp), ListDirTool(rp),
        EditFileTool(rp), EditLinesTool(rp),
        GrepCodeTool(rp), FindSymbolsTool(rp), GetStructureTool(rp),
        RunCommandTool(rp), FetchUrlTool(),
    ])

    from zirconAgent.core.context import ContextManager
    from zirconAgent.core.session import SessionManager
    from zirconAgent.core.planner import Planner, PlanGatekeeper
    from zirconAgent.core.executor import Executor
    from zirconAgent.core.kg_memory import KnowledgeGraphMemory
    from zirconAgent.core.types import Tier, TIER_PRESETS
    kg = KnowledgeGraphMemory(str(tmp_path))
    a.tier = Tier.BALANCED
    a.tier_cfg = TIER_PRESETS[Tier.BALANCED]
    a.tier_cfg.gatekeeper_mode = "llm_only"
    a.tier_cfg.replanning_enabled = False
    a.tier_cfg.skip_final_verification = True
    a.context = ContextManager(tmp_path, context_window=32000, safety_margin=400, kg_memory=kg, tier_config=a.tier_cfg)
    a.kg = kg
    a.sessions = SessionManager(tmp_path)
    a.planner = Planner(router, tier_config=a.tier_cfg)
    a.executor = Executor(router, a.registry, tier_config=a.tier_cfg)
    a._gatekeeper = PlanGatekeeper(router, tier_config=a.tier_cfg)
    a._status = TaskStatus.IDLE
    a._pending_plan = None
    a._plan_feedback = ""
    a._current_task = ""
    return a


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "calculator.py").write_text(
        "class Calculator:\n"
        "    def add(self, a, b):\n"
        "        return a + b\n"
        "\n"
        "    def subtract(self, a, b):\n"
        "        return a - b\n"
        "\n"
        "    def multiply(self, a, b):\n"
        "        return a * b\n"
    )
    (src / "__init__.py").write_text("")
    return tmp_path


class TestE2ESimpleEdit:
    @pytest.mark.asyncio
    async def test_add_method(self, repo):
        responses = [
            LLMResponse(content="PLAN_REQUIRED: test"),
            LLMResponse(
                content='{"steps": [{"index": 0, "description": "read calculator.py", "action": "explore", "target_files": ["src/calculator.py"]}, {"index": 1, "description": "add divide method", "action": "edit", "target_files": ["src/calculator.py"]}]}',
            ),
            tool_call_response([("read_file", {"path": "src/calculator.py"})]),
            tool_response("I can see the Calculator class with add, subtract, multiply methods."),
            tool_call_response([("edit_file", {"path": "src/calculator.py", "search": "    def multiply(self, a, b):\n        return a * b", "replace": "    def multiply(self, a, b):\n        return a * b\n\n    def divide(self, a, b):\n        if b == 0:\n            raise ValueError(\"Cannot divide by zero\")\n        return a / b"})]),
            tool_response("Added the divide method to the Calculator class."),
            tool_response("Verification passed."),
        ]

        agent = make_e2e_agent(repo, responses)
        result = await agent.solve("Add a divide method to the Calculator class")
        assert result.status == TaskStatus.AWAITING_INPUT

        agent.submit_feedback("approved")
        result = await agent.solve("Add a divide method to the Calculator class")

        assert result.success
        assert "src/calculator.py" in result.files_modified
        content = (repo / "src" / "calculator.py").read_text()
        assert "divide" in content
        assert "Cannot divide by zero" in content


class TestE2ECreateFile:
    @pytest.mark.asyncio
    async def test_create_new_file(self, repo):
        responses = [
            LLMResponse(content="PLAN_REQUIRED: test"),
            LLMResponse(
                content='{"steps": [{"index": 0, "description": "create test file", "action": "edit", "target_files": ["tests/test_calc.py"]}]}',
            ),
            tool_call_response([("create_file", {"path": "tests/test_calc.py", "content": "from src.calculator import Calculator\n\ndef test_add():\n    calc = Calculator()\n    assert calc.add(1, 2) == 3\n"})]),
            tool_response("Created the test file."),
            tool_response("Verification passed."),
        ]

        agent = make_e2e_agent(repo, responses)
        result = await agent.solve("Create a test file for the Calculator class")
        assert result.status == TaskStatus.AWAITING_INPUT

        agent.submit_feedback("approved")
        result = await agent.solve("Create a test file for the Calculator class")

        assert result.success
        assert "tests/test_calc.py" in result.files_modified
        assert (repo / "tests" / "test_calc.py").exists()
        content = (repo / "tests" / "test_calc.py").read_text()
        assert "Calculator" in content


class TestE2EExploreOnly:
    @pytest.mark.asyncio
    async def test_explore_question(self, repo):
        responses = [
            LLMResponse(content="PLAN_REQUIRED: test"),
            LLMResponse(
                content='{"steps": [{"index": 0, "description": "find all methods in Calculator", "action": "explore"}]}',
            ),
            tool_call_response([("get_structure", {"path": "src/calculator.py"})]),
            tool_response("The Calculator class has methods: add, subtract, multiply."),
        ]

        agent = make_e2e_agent(repo, responses)
        result = await agent.solve("What methods does the Calculator class have?")
        assert result.status == TaskStatus.AWAITING_INPUT

        agent.submit_feedback("approved")
        result = await agent.solve("What methods does the Calculator class have?")

        assert result.success
        assert len(result.files_modified) == 0


class TestE2EMultiStep:
    @pytest.mark.asyncio
    async def test_explore_then_edit(self, repo):
        responses = [
            LLMResponse(content="PLAN_REQUIRED: test"),
            LLMResponse(
                content='{"steps": [{"index": 0, "description": "explore calculator", "action": "explore"}, {"index": 1, "description": "add power method", "action": "edit"}, {"index": 2, "description": "verify syntax", "action": "verify"}]}',
            ),
            tool_call_response([("read_file", {"path": "src/calculator.py"})]),
            tool_response("Found the Calculator class with 3 methods."),
            tool_call_response([("edit_file", {"path": "src/calculator.py", "search": "    def multiply(self, a, b):\n        return a * b", "replace": "    def multiply(self, a, b):\n        return a * b\n\n    def power(self, base, exp):\n        return base ** exp"})]),
            tool_response("Added power method."),
            tool_call_response([("run_command", {"command": "python -c \"import ast; ast.parse(open('src/calculator.py').read()); print('OK')\""})]),
            tool_response("Syntax check passed. OK."),
            tool_response("Verification passed."),
        ]

        agent = make_e2e_agent(repo, responses)
        result = await agent.solve("Add an exponentiation (power) method to the Calculator")
        assert result.status == TaskStatus.AWAITING_INPUT

        agent.submit_feedback("approved")
        result = await agent.solve("Add an exponentiation (power) method to the Calculator")

        assert result.success
        assert "src/calculator.py" in result.files_modified
        content = (repo / "src" / "calculator.py").read_text()
        assert "power" in content
        assert "**" in content


class TestE2EVerifyFailure:
    @pytest.mark.asyncio
    async def test_verify_catches_issue(self, repo):
        responses = [
            LLMResponse(content="PLAN_REQUIRED: test"),
            LLMResponse(
                content='{"steps": [{"index": 0, "description": "edit the file", "action": "edit"}, {"index": 1, "description": "verify", "action": "verify"}]}',
            ),
            tool_call_response([("edit_file", {"path": "src/calculator.py", "search": "return a * b", "replace": "return a ** b"})]),
            tool_response("Changed multiply to use **."),
            tool_call_response([("run_command", {"command": "python -c \"from src.calculator import Calculator; c = Calculator(); assert c.multiply(2, 3) == 6; print('PASS')\""})]),
            tool_response("TEST FAILED: multiply(2,3) returned 8, expected 6."),
        ]

        agent = make_e2e_agent(repo, responses)
        agent.tier_cfg.skip_final_verification = False
        result = await agent.solve("Change the multiply method")
        assert result.status == TaskStatus.AWAITING_INPUT

        agent.submit_feedback("approved")
        result = await agent.solve("Change the multiply method")

        assert not result.success
        assert len(result.trace) > 0


class TestE2EStreaming:
    @pytest.mark.asyncio
    async def test_streaming_events(self, repo):
        responses = [
            LLMResponse(content="PLAN_REQUIRED: test"),
            LLMResponse(
                content='{"steps": [{"index": 0, "description": "read file", "action": "explore"}]}',
            ),
            tool_call_response([("read_file", {"path": "src/calculator.py"})]),
            tool_response("I read the file."),
        ]

        agent = make_e2e_agent(repo, responses)
        events = []
        async for event in agent.solve_stream("What is in calculator.py?"):
            events.append(event)

        phases = [e.phase for e in events]
        assert "start" in phases
        assert "awaiting_input" in phases

        agent.submit_feedback("approved")
        events2 = []
        async for event in agent.solve_stream("What is in calculator.py?"):
            events2.append(event)

        phases2 = [e.phase for e in events2]
        assert "plan" in phases2
        assert "step" in phases2
        assert "task_complete" in phases2


class TestE2EChat:
    @pytest.mark.asyncio
    async def test_chat_with_tools(self, repo):
        responses = [
            LLMResponse(content="DIRECT_OK: test"),
            tool_call_response([("read_file", {"path": "src/calculator.py"})]),
            tool_response("The Calculator class has add, subtract, and multiply methods."),
        ]

        agent = make_e2e_agent(repo, responses)
        answer = await agent.chat("What methods does the Calculator have?")

        assert "Calculator" in answer or "add" in answer or "multiply" in answer

    @pytest.mark.asyncio
    async def test_chat_no_tools(self, repo):
        agent = make_e2e_agent(repo, [
            LLMResponse(content="DIRECT_OK: test"),
            tool_response("The sky is blue because of Rayleigh scattering."),
        ])
        answer = await agent.chat("Why is the sky blue?")
        assert "sky" in answer.lower() or "blue" in answer.lower() or "Rayleigh" in answer


class TestE2ESessionTracking:
    @pytest.mark.asyncio
    async def test_session_created_and_closed(self, repo):
        responses = [
            LLMResponse(content="PLAN_REQUIRED: test"),
            LLMResponse(content='{"steps": [{"index": 0, "description": "explore", "action": "explore"}]}'),
            tool_response("done"),
        ]

        agent = make_e2e_agent(repo, responses)
        result = await agent.solve("explore the codebase")
        assert result.status == TaskStatus.AWAITING_INPUT

        agent.submit_feedback("approved")
        result = await agent.solve("explore the codebase")

        assert agent.sessions.current is not None
        assert agent.sessions.current.status == TaskStatus.COMPLETED
