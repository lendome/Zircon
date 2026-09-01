import pytest
from unittest.mock import AsyncMock

from zirconAgent.core.executor import Executor
from zirconAgent.core.types import LLMResponse, ToolCall
from zirconAgent.tools.registry import ToolRegistry
from zirconAgent.tools.file_ops import ReadFileTool, CreateFileTool
from zirconAgent.tools.image_ops import ViewImageTool
from zirconAgent.tests.mocks import make_router, tool_response, tool_call_response


@pytest.fixture
def registry(tmp_path):
    reg = ToolRegistry()
    reg.register(ReadFileTool(str(tmp_path)))
    reg.register(CreateFileTool(str(tmp_path)))
    return reg


@pytest.fixture
def executor(tmp_path):
    reg = ToolRegistry()
    reg.register(ReadFileTool(str(tmp_path)))
    reg.register(CreateFileTool(str(tmp_path)))
    router = make_router()
    return Executor(router, reg)


class TestExecutorBasicLoop:
    def test_aider_edit_path_extraction(self, executor):
        call = ToolCall(
            id="edit-1",
            name="aider_edit",
            arguments={
                "content": (
                    "zirconAgent/README.md\n"
                    "<<<<<<< SEARCH\n"
                    "old text\n"
                    "=======\n"
                    "new text\n"
                    ">>>>>>> REPLACE"
                )
            },
        )

        assert executor._paths_from_tool_call(call) == ["zirconAgent/README.md"]

    @pytest.mark.asyncio
    async def test_immediate_text_response(self, executor):
        executor.router.generate = AsyncMock(
            return_value=tool_response("The answer is 42")
        )
        result = await executor.run_tool_loop(
            messages=[{"role": "user", "content": "what is the answer"}],
        )
        assert result.success
        assert "42" in result.output

    @pytest.mark.asyncio
    async def test_single_tool_call(self, tmp_path):
        (tmp_path / "hello.py").write_text("print('hello')")
        reg = ToolRegistry()
        reg.register(ReadFileTool(str(tmp_path)))
        router = make_router()

        router.generate = AsyncMock(side_effect=[
            tool_call_response([("read_file", {"path": "hello.py"})]),
            tool_response("I read the file, it prints hello."),
        ])

        executor = Executor(router, reg)
        result = await executor.run_tool_loop(
            messages=[{"role": "user", "content": "read hello.py"}],
            tools=[{"name": "read_file", "parameters": {}}],
        )
        assert result.success
        assert result.tool_calls_made == 1

    @pytest.mark.asyncio
    async def test_view_image_is_injected_into_next_model_request(self, tmp_path):
        (tmp_path / "screen.png").write_bytes(b"\x89PNG\r\n\x1a\nimage-data")
        reg = ToolRegistry()
        reg.register(ViewImageTool(str(tmp_path)))
        router = make_router()
        router._profiles["default"].supports_vision = True
        router.generate = AsyncMock(side_effect=[
            tool_call_response([("view_image", {"source": "screen.png"})]),
            tool_response("The screenshot shows the app."),
        ])
        executor = Executor(router, reg)

        result = await executor.run_tool_loop(
            messages=[{"role": "user", "content": "inspect screen.png"}],
            tools=reg.get_schemas(),
        )

        assert result.success
        second_messages = router.generate.await_args_list[1].kwargs["messages"]
        assert second_messages[-2]["role"] == "tool"
        assert isinstance(second_messages[-2]["content"], str)
        assert second_messages[-1]["role"] == "user"
        assert second_messages[-1]["content"][1]["type"] == "image_url"

    def test_view_image_schema_hidden_for_text_only_role(self, tmp_path):
        reg = ToolRegistry()
        reg.register(ViewImageTool(str(tmp_path)))
        router = make_router()
        router._profiles["default"].supports_vision = False
        executor = Executor(router, reg)

        assert executor._apply_tool_gates(reg.get_schemas()) == []

    @pytest.mark.asyncio
    async def test_multi_tool_call_sequence(self, tmp_path):
        (tmp_path / "a.txt").write_text("content A")
        (tmp_path / "b.txt").write_text("content B")
        reg = ToolRegistry()
        reg.register(ReadFileTool(str(tmp_path)))
        router = make_router()

        router.generate = AsyncMock(side_effect=[
            tool_call_response([("read_file", {"path": "a.txt"})]),
            tool_call_response([("read_file", {"path": "b.txt"})]),
            tool_response("I read both files."),
        ])

        executor = Executor(router, reg)
        result = await executor.run_tool_loop(
            messages=[{"role": "user", "content": "read both files"}],
        )
        assert result.success
        assert result.tool_calls_made == 2

    @pytest.mark.asyncio
    async def test_max_turns_reached(self, executor):
        """The last allowed provider turn is reserved for a text-only result."""
        varied_responses = [
            tool_call_response([("read_file", {"path": "a.py"})]),
            tool_call_response([("read_file", {"path": "b.py"})]),
            tool_call_response([("read_file", {"path": "c.py"})]),
        ]
        executor.router.generate = AsyncMock(side_effect=varied_responses)
        result = await executor.run_tool_loop(
            messages=[{"role": "user", "content": "loop forever"}],
            max_turns=3,
        )
        assert result.success
        assert result.tool_calls_made == 2
        assert "a.py" in result.files_read
        assert "b.py" in result.files_read
        assert "c.py" not in result.files_read

    @pytest.mark.asyncio
    async def test_text_only_response_completes_without_disabling_tools(self, executor):
        """Tools remain available for every turn; a text-only response is a
        legitimate completion when no evidence category is required."""
        calls = []

        async def generate(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return tool_call_response([("read_file", {"path": "a.py"})])
            return tool_response("final summary")

        executor.router.generate = generate
        result = await executor.run_tool_loop(messages=[], tools=[{"name": "read_file"}], max_turns=2)
        assert result.output == "final summary"
        # Tools are no longer force-disabled on the final turn.
        assert calls[-1]["tools"] is not None

    @pytest.mark.asyncio
    async def test_loop_detection_warnings_injected_on_repetition(self, executor):
        """Repeated identical calls trigger warnings injected into messages, but
        the executor finishes gracefully (loop detector never returns 'critical')."""
        infinite_loop_response = tool_call_response([("read_file", {"path": "x.py"})])
        executor.router.generate = AsyncMock(return_value=infinite_loop_response)
        result = await executor.run_tool_loop(
            messages=[{"role": "user", "content": "loop forever"}],
            max_turns=10,
        )
        # Graceful success — warnings were injected, no critical loop stop
        assert result.success
        # anti-loop warnings should have been emitted in the trace
        anti_loop_events = [t for t in result.trace if t.phase == "anti_loop"]
        assert len(anti_loop_events) > 0
        assert "WARNING" in anti_loop_events[0].detail.upper() or "repeated" in anti_loop_events[0].detail.lower()


class TestExecutorFileTracking:
    @pytest.mark.asyncio
    async def test_tracks_read_files(self, tmp_path):
        (tmp_path / "target.py").write_text("x = 1")
        reg = ToolRegistry()
        reg.register(ReadFileTool(str(tmp_path)))
        router = make_router()

        router.generate = AsyncMock(side_effect=[
            tool_call_response([("read_file", {"path": "target.py"})]),
            tool_response("done"),
        ])

        executor = Executor(router, reg)
        result = await executor.run_tool_loop(messages=[])
        assert "target.py" in result.files_read

    @pytest.mark.asyncio
    async def test_tracks_modified_files(self, tmp_path):
        reg = ToolRegistry()
        reg.register(CreateFileTool(str(tmp_path)))
        router = make_router()

        router.generate = AsyncMock(side_effect=[
            tool_call_response([("create_file", {"path": "new.py", "content": "pass"})]),
            tool_response("created"),
        ])

        executor = Executor(router, reg)
        result = await executor.run_tool_loop(messages=[])
        assert "new.py" in result.files_modified


class TestExecutorErrorHandling:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, executor):
        executor.router.generate = AsyncMock(side_effect=[
            tool_call_response([("nonexistent_tool", {})]),
            tool_response("okay"),
        ])
        result = await executor.run_tool_loop(messages=[])
        assert result.success

    @pytest.mark.asyncio
    async def test_llm_error(self, executor):
        executor.router.generate = AsyncMock(side_effect=RuntimeError("API down"))
        result = await executor.run_tool_loop(messages=[])
        assert not result.success
        assert "LLM error" in result.output
