import pytest
from unittest.mock import AsyncMock

from zirconAgent.core.executor import Executor
from zirconAgent.core.types import LLMResponse, StreamChunk, ToolCall, CompletionDisposition
from zirconAgent.tools.registry import ToolRegistry
from zirconAgent.tools.file_ops import ReadFileTool, CreateFileTool
from zirconAgent.tests.mocks import (
    make_router,
    make_stream_router,
    tool_response,
    tool_call_response,
)


@pytest.fixture
def registry(tmp_path):
    reg = ToolRegistry()
    reg.register(ReadFileTool(str(tmp_path)))
    reg.register(CreateFileTool(str(tmp_path)))
    return reg




class TestStreamingBasic:
    @pytest.mark.asyncio
    async def test_stream_yields_text_chunks(self, registry):
        router = make_stream_router([tool_response("Hello world")])
        executor = Executor(router, registry)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        texts = [c.text for c in chunks if c.text]
        assert "".join(texts) == "Hello world"
        assert any(c.done for c in chunks)

    @pytest.mark.asyncio
    async def test_stream_done_chunk_has_usage(self, registry):
        router = make_stream_router([
            LLMResponse(content="ok", tool_calls=[], usage={"prompt_tokens": 10, "completion_tokens": 5}),
        ])
        executor = Executor(router, registry)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[]):
            chunks.append(chunk)

        done_chunks = [c for c in chunks if c.done]
        assert len(done_chunks) >= 1
        assert done_chunks[0].usage.get("prompt_tokens") == 10

    @pytest.mark.asyncio
    async def test_stream_yields_reasoning_chunks(self, registry):
        async def _stream_with_reasoning(**kwargs):
            yield StreamChunk(reasoning="hmm")
            yield StreamChunk(text="answer")
            yield StreamChunk(done=True, usage={})

        router = make_stream_router([tool_response("x")])
        router.generate_stream = _stream_with_reasoning
        executor = Executor(router, registry)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[]):
            chunks.append(chunk)

        reasoning = [c for c in chunks if c.reasoning]
        assert len(reasoning) == 1
        assert reasoning[0].reasoning == "hmm"
        texts = "".join(c.text for c in chunks if c.text)
        assert texts == "answer"


class TestStreamingWithToolCalls:
    @pytest.mark.asyncio
    async def test_stream_tool_call_then_response(self, tmp_path):
        (tmp_path / "data.txt").write_text("some data")
        reg = ToolRegistry()
        reg.register(ReadFileTool(str(tmp_path)))

        router = make_stream_router([
            tool_call_response([("read_file", {"path": "data.txt"})]),
            tool_response("The file contains: some data"),
        ])
        executor = Executor(router, reg)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[]):
            chunks.append(chunk)

        tool_chunks = [c for c in chunks if c.tool_calls]
        assert len(tool_chunks) == 1
        assert tool_chunks[0].tool_calls[0].name == "read_file"

        texts = "".join(c.text for c in chunks if c.text)
        assert "some data" in texts

    @pytest.mark.asyncio
    async def test_history_keeps_exact_tool_output_and_final_response(self, tmp_path):
        exact_output = "start\n" + ("x" * 20_000) + "\nend"
        (tmp_path / "data.txt").write_text(exact_output)
        reg = ToolRegistry()
        reg.register(ReadFileTool(str(tmp_path)))
        router = make_stream_router([
            tool_call_response([("read_file", {"path": "data.txt"})]),
            tool_response("Final answer after reading the file."),
        ])
        executor = Executor(router, reg)

        async for _chunk in executor.run_tool_loop_stream(messages=[]):
            pass

        tool_message = next(
            message for message in executor.last_history_turns
            if message.get("role") == "tool"
        )
        assert ("x" * 20_000) in tool_message["content"]
        assert tool_message["content"].startswith("[Lines 1-3 of 3]\n1: start")
        assert tool_message["content"].endswith("3: end")
        assert executor.last_history_turns[-1] == {
            "role": "assistant",
            "content": "Final answer after reading the file.",
        }

    @pytest.mark.asyncio
    async def test_stream_max_turns(self, registry):
        router = make_stream_router([
            tool_call_response([("read_file", {"path": "x.py"})]),
        ])
        executor = Executor(router, registry)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[], max_turns=2):
            chunks.append(chunk)

        assert any(c.done for c in chunks)
        # Hitting the tool-turn budget now reports an honest TURN_LIMIT
        # disposition rather than fabricating a silent success.
        done_chunks = [c for c in chunks if c.done]
        assert done_chunks
        assert done_chunks[-1].disposition == CompletionDisposition.TURN_LIMIT

    @pytest.mark.asyncio
    async def test_tool_call_arguments_are_valid_json(self, tmp_path):
        (tmp_path / "t.py").write_text("x=1")
        reg = ToolRegistry()
        reg.register(ReadFileTool(str(tmp_path)))

        calls_seen = []
        call_idx = 0

        async def _track_generate(**kwargs):
            nonlocal call_idx
            calls_seen.append(list(kwargs["messages"]))
            call_idx += 1
            if call_idx == 1:
                return tool_call_response([("read_file", {"path": "t.py"})])
            return tool_response("done")

        router = make_router()
        router.generate = _track_generate

        async def _fail(**kwargs):
            raise RuntimeError("no stream")
            yield

        router.generate_stream = _fail
        executor = Executor(router, reg)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[]):
            chunks.append(chunk)

        assert len(calls_seen) == 2
        assistant_msg = [m for m in calls_seen[1] if m.get("tool_calls")]
        assert len(assistant_msg) == 1
        args_str = assistant_msg[0]["tool_calls"][0]["function"]["arguments"]
        import json
        parsed = json.loads(args_str)
        assert parsed == {"path": "t.py"}

    @pytest.mark.asyncio
    async def test_blocked_completion_text_is_not_streamed(self, registry):
        router = make_router([
            tool_response("Done."),
            tool_call_response([("read_file", {"path": "missing.py"})]),
        ])

        async def _failing_stream(**kwargs):
            raise RuntimeError("no stream")
            yield

        router.generate_stream = _failing_stream
        executor = Executor(router, registry)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(
            messages=[{"role": "user", "content": "build it as an exe"}],
            max_turns=1,
        ):
            chunks.append(chunk)

        assert "Done." not in "".join(chunk.text for chunk in chunks)
        assert any("Completion evidence missing" in chunk.progress_label for chunk in chunks)
        assert [chunk for chunk in chunks if chunk.done][-1].disposition == CompletionDisposition.TURN_LIMIT


class TestStreamingFallback:
    @pytest.mark.asyncio
    async def test_stream_continues_after_multiple_truncated_responses(self, registry):
        """Truncation should continue the task instead of ending it after three retries."""
        responses = [
            LLMResponse(content="cut off", finish_reason="length")
            for _ in range(4)
        ] + [tool_response("Finished after continuing.")]
        router = make_router(responses)

        async def _failing_stream(**kwargs):
            raise RuntimeError("no stream")
            yield

        router.generate_stream = _failing_stream
        executor = Executor(router, registry)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[], max_turns=5):
            chunks.append(chunk)

        assert any("Response truncated" in chunk.text for chunk in chunks)
        assert "Finished after continuing." in "".join(chunk.text for chunk in chunks)
        assert [chunk for chunk in chunks if chunk.done][-1].disposition == CompletionDisposition.DECLARED

    @pytest.mark.asyncio
    async def test_stream_continuation_preserves_partial_reasoning(self, registry):
        calls = []
        router = make_router()
        responses = [
            LLMResponse(content="cut off", reasoning_content="partial plan", finish_reason="length"),
            tool_response("Finished after continuing."),
        ]

        async def _generate(**kwargs):
            calls.append(kwargs["messages"])
            return responses.pop(0)

        async def _failing_stream(**kwargs):
            raise RuntimeError("no stream")
            yield

        router.generate = _generate
        router.generate_stream = _failing_stream
        executor = Executor(router, registry)

        async for _ in executor.run_tool_loop_stream(messages=[], max_turns=2):
            pass

        interrupted = calls[1][-2]
        assert interrupted["role"] == "assistant"
        assert interrupted["reasoning_content"] == "partial plan"

    @pytest.mark.asyncio
    async def test_fallback_yields_words(self, registry):
        router = make_router([tool_response("Hello world test")])

        async def _failing_stream(**kwargs):
            raise RuntimeError("no stream")
            yield

        router.generate_stream = _failing_stream
        executor = Executor(router, registry)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[]):
            chunks.append(chunk)

        texts = "".join(c.text for c in chunks if c.text)
        assert texts == "Hello world test"
        text_chunks = [c for c in chunks if c.text]
        assert len(text_chunks) >= 2
        assert any(c.done for c in chunks)

    @pytest.mark.asyncio
    async def test_fallback_with_tool_calls(self, tmp_path):
        (tmp_path / "f.txt").write_text("hello")
        reg = ToolRegistry()
        reg.register(ReadFileTool(str(tmp_path)))

        router = make_router([
            tool_call_response([("read_file", {"path": "f.txt"})]),
            tool_response("Read the file"),
        ])

        async def _failing_stream(**kwargs):
            raise RuntimeError("no stream")
            yield

        router.generate_stream = _failing_stream
        executor = Executor(router, reg)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[]):
            chunks.append(chunk)

        tool_chunks = [c for c in chunks if c.tool_calls]
        assert len(tool_chunks) == 1
        texts = "".join(c.text for c in chunks if c.text)
        assert "Read the file" in texts

    @pytest.mark.asyncio
    async def test_both_fail_yields_error(self, registry):
        async def _failing_stream(**kwargs):
            raise RuntimeError("stream down")
            yield

        router = make_router([])
        router.generate = AsyncMock(side_effect=RuntimeError("API down"))
        router.generate_stream = _failing_stream
        executor = Executor(router, registry)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[]):
            chunks.append(chunk)

        errors = [c for c in chunks if c.error]
        assert len(errors) == 1
        assert "API down" in errors[0].error

    @pytest.mark.asyncio
    async def test_streaming_ok_flag_disables_after_failure(self, registry):
        call_count = {"stream": 0, "generate": 0}

        async def _failing_stream(**kwargs):
            call_count["stream"] += 1
            raise RuntimeError("fail")
            yield

        original_generate = router.generate if False else None
        router = make_router([
            tool_response("first"),
        ])

        async def _counting_generate(**kwargs):
            call_count["generate"] += 1
            return tool_response("response " + str(call_count["generate"]))

        router.generate_stream = _failing_stream
        router.generate = _counting_generate
        executor = Executor(router, registry)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[]):
            chunks.append(chunk)

        assert call_count["stream"] == 1
        assert call_count["generate"] == 1

    @pytest.mark.asyncio
    async def test_empty_stream_falls_back(self, registry):
        router = make_router([tool_response("recovered")])

        async def _empty_stream(**kwargs):
            return
            yield  # noqa

        router.generate_stream = _empty_stream
        executor = Executor(router, registry)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[]):
            chunks.append(chunk)

        texts = "".join(c.text for c in chunks if c.text)
        assert texts == "recovered"
        assert any(c.done for c in chunks)


class TestStreamChunkTypes:
    @pytest.mark.asyncio
    async def test_reasoning_field(self):
        chunk = StreamChunk(reasoning="thinking hard", text="", done=False)
        assert chunk.reasoning == "thinking hard"

    def test_tool_calls_is_list(self):
        chunk = StreamChunk(tool_calls=[ToolCall(id="1", name="test", arguments={})])
        assert isinstance(chunk.tool_calls, list)

    def test_tool_calls_default_none(self):
        chunk = StreamChunk()
        assert chunk.tool_calls is None

    def test_tool_result_default_empty(self):
        chunk = StreamChunk()
        assert chunk.tool_result == ""

    def test_tool_result_field(self):
        chunk = StreamChunk(tool_result="Applied fuzzy to file.py")
        assert chunk.tool_result == "Applied fuzzy to file.py"


class TestToolResultStreaming:
    @pytest.mark.asyncio
    async def test_tool_result_yielded_after_call(self, tmp_path):
        (tmp_path / "x.py").write_text("old code")
        reg = ToolRegistry()
        reg.register(ReadFileTool(str(tmp_path)))

        router = make_stream_router([
            tool_call_response([("read_file", {"path": "x.py"})]),
            tool_response("done"),
        ])
        executor = Executor(router, reg)

        chunks = []
        async for chunk in executor.run_tool_loop_stream(messages=[]):
            chunks.append(chunk)

        result_chunks = [c for c in chunks if c.tool_result]
        assert len(result_chunks) >= 1
        assert "old code" in result_chunks[0].tool_result
