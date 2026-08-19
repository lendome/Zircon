import pytest
from unittest.mock import AsyncMock

from zirconAgent.subagents.base import BaseSubAgent
from zirconAgent.core.types import SubAgentResult, LLMResponse
from zirconAgent.tools.registry import ToolRegistry
from zirconAgent.tools.file_ops import ReadFileTool, GlobFilesTool
from zirconAgent.tests.mocks import make_router, tool_response, tool_call_response


class ConcreteSubAgent(BaseSubAgent):
    system_prompt = "You are a test sub-agent."
    tool_names = ["read_file", "glob_files"]


@pytest.fixture
def registry(tmp_path):
    reg = ToolRegistry()
    reg.register(ReadFileTool(str(tmp_path)))
    reg.register(GlobFilesTool(str(tmp_path)))
    return reg


class TestBaseSubAgent:
    @pytest.mark.asyncio
    async def test_immediate_text_response(self, tmp_path, registry):
        router = make_router([tool_response("The answer is 42")])
        sub = ConcreteSubAgent(router, registry, str(tmp_path))
        result = await sub.run("what is 6*7?", "")
        assert result.success
        assert "42" in result.output

    @pytest.mark.asyncio
    async def test_tool_call_then_answer(self, tmp_path, registry):
        (tmp_path / "hello.py").write_text("print('hi')")
        router = make_router([
            tool_call_response([("read_file", {"path": "hello.py"})]),
            tool_response("The file prints 'hi'."),
        ])
        sub = ConcreteSubAgent(router, registry, str(tmp_path))
        result = await sub.run("read hello.py", "")
        assert result.success
        assert "hello.py" in result.files_read

    @pytest.mark.asyncio
    async def test_max_turns(self, tmp_path, registry):
        infinite = tool_call_response([("glob_files", {"pattern": "*.py"})])
        router = make_router([infinite] * 20)
        sub = ConcreteSubAgent(router, registry, str(tmp_path))
        sub.max_turns = 3
        result = await sub.run("search forever", "")
        assert not result.success
        assert "Max turns" in result.output

    @pytest.mark.asyncio
    async def test_context_injected(self, tmp_path, registry):
        router = make_router([tool_response("done")])
        sub = ConcreteSubAgent(router, registry, str(tmp_path))
        await sub.run("task", "Extra context here")
        call_args = router.generate.call_args
        messages = call_args[1]["messages"] if "messages" in call_args[1] else call_args[0][0] if call_args[0] else None
        if messages is None:
            messages = router.generate.call_args_list[0].kwargs.get("messages", [])
        system_content = messages[0]["content"]
        assert "Extra context here" in system_content

    @pytest.mark.asyncio
    async def test_llm_error(self, tmp_path, registry):
        router = make_router()
        router.generate = AsyncMock(side_effect=RuntimeError("API broken"))
        sub = ConcreteSubAgent(router, registry, str(tmp_path))
        result = await sub.run("task", "")
        assert not result.success
        assert "LLM error" in result.output


class TestSubAgentFileTracking:
    @pytest.mark.asyncio
    async def test_tracks_file_reads(self, tmp_path, registry):
        (tmp_path / "data.py").write_text("x = 1")
        router = make_router([
            tool_call_response([("read_file", {"path": "data.py"})]),
            tool_response("done"),
        ])
        sub = ConcreteSubAgent(router, registry, str(tmp_path))
        result = await sub.run("read data.py", "")
        assert "data.py" in result.files_read

    @pytest.mark.asyncio
    async def test_empty_file_lists_by_default(self, tmp_path, registry):
        router = make_router([tool_response("no tools needed")])
        sub = ConcreteSubAgent(router, registry, str(tmp_path))
        result = await sub.run("simple question", "")
        assert result.files_read == []
        assert result.files_modified == []
