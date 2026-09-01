import asyncio
import http.server
import socketserver
import threading

import pytest

from zirconAgent.core.executor import Executor
from zirconAgent.core.types import CompletionDisposition
from zirconAgent.tools.registry import ToolRegistry
from zirconAgent.tools.file_ops import CreateFileTool
from zirconAgent.tools.shell_ops import RunCommandTool, ProcessManager
from zirconAgent.tools.web_ops import FetchUrlTool
from zirconAgent.tests.mocks import make_router, tool_response, tool_call_response
from zirconAgent.core.types import LLMResponse, ToolCall


@pytest.fixture
def registry(tmp_path):
    reg = ToolRegistry()
    reg.register(CreateFileTool(str(tmp_path)))
    reg.register(RunCommandTool(str(tmp_path), ProcessManager()))
    reg.register(FetchUrlTool())
    return reg


class TestCompletionGateInLoop:
    def test_only_verification_commands_satisfy_test_nudge(self, registry):
        executor = Executor(make_router(), registry)

        listing = ToolCall(id="1", name="run_command", arguments={"command": "ls"})
        tests = ToolCall(id="2", name="run_command", arguments={"command": "pytest -q"})

        assert not executor._is_verification_call(listing, "files\nExit code: 0")
        assert executor._is_verification_call(tests, "10 passed\nExit code: 0")
        assert executor._is_verification_call(tests, "2 failed\nExit code: 1")

    def test_nested_test_suite_is_detected(self, tmp_path):
        nested = tmp_path / "packages" / "api" / "tests"
        nested.mkdir(parents=True)
        (nested / "test_api.py").write_text("def test_api(): pass\n")
        reg = ToolRegistry()
        reg.register(CreateFileTool(str(tmp_path)))

        assert Executor(make_router(), reg)._repo_has_tests()

    @pytest.mark.asyncio
    async def test_non_streaming_loop_continues_after_multiple_truncations(self, registry):
        responses = [
            LLMResponse(content="cut off", finish_reason="max_output_tokens")
            for _ in range(4)
        ] + [tool_response("Finished after continuing.")]
        router = make_router(responses)
        executor = Executor(router, registry)

        result = await executor.run_tool_loop(messages=[], max_turns=5)

        assert result.output == "Finished after continuing."
        assert executor._truncation_retries == 4

    @pytest.mark.asyncio
    async def test_non_streaming_continuation_preserves_partial_reasoning(self, registry):
        calls = []
        router = make_router()
        responses = [
            LLMResponse(content="cut off", reasoning_content="partial plan", finish_reason="length"),
            tool_response("Finished after continuing."),
        ]

        async def _generate(**kwargs):
            calls.append(kwargs["messages"])
            return responses.pop(0)

        router.generate = _generate
        executor = Executor(router, registry)

        result = await executor.run_tool_loop(messages=[], max_turns=2)

        assert result.output == "Finished after continuing."
        interrupted = calls[1][-2]
        assert interrupted["role"] == "assistant"
        assert interrupted["reasoning_content"] == "partial plan"

    @pytest.mark.asyncio
    async def test_premature_done_on_build_task_is_continued(self, registry):
        """A text-only 'Done' before any artifact/build evidence is rejected
        and the loop continues until evidence is produced."""
        router = make_router()
        router.generate = lambda **kw: None  # placeholder, replaced below

        sequence = [
            # turn 1: run a no-op command that produces no artifact
            tool_call_response([("run_command", {"command": "echo compiling && echo 'Exit code: 0'"})]),
            # turn 2: model says "Done." prematurely -> gate blocks, nudge
            tool_response("Done."),
            # turn 3: produce a real artifact path in command output
            tool_call_response([("run_command", {"command": "echo 'Built dist/app.exe'; echo 'Exit code: 0'"})]),
            # turn 4: model says "Done." now with evidence present -> accept
            tool_response("Done."),
        ]
        responses = list(sequence)
        import asyncio as _aio

        async def _gen(**kwargs):
            return responses.pop(0)

        router.generate = _gen
        executor = Executor(router, registry)
        result = await executor.run_tool_loop(
            messages=[{"role": "user", "content": "build it as an exe"}],
        )
        assert result.output == "Done."
        assert result.disposition == CompletionDisposition.VERIFIED
        # The executor state should have recorded the artifact.
        assert any("app.exe" in a for a in executor._exec_state.artifacts)

    @pytest.mark.asyncio
    async def test_premature_done_on_generic_implementation_is_continued(self, registry):
        """A bare Done must not finish an implementation task before an edit."""
        responses = [
            tool_response("Done."),
            tool_call_response([("create_file", {"path": "created.txt", "content": "implemented\n"})]),
            tool_response(
                "Implemented the requested change in created.txt. "
                "The file contains the requested implementation and is ready for review."
            ),
        ]

        async def _gen(**kwargs):
            return responses.pop(0)

        router = make_router()
        router.generate = _gen
        executor = Executor(router, registry)

        result = await executor.run_tool_loop(
            messages=[{"role": "user", "content": "implement the requested change"}],
        )

        assert result.output.startswith("Implemented the requested change in created.txt.")
        assert any("premature completion marker" in event.detail for event in result.trace)
        assert "created.txt" in result.files_modified


class TestUrlProbeInLoop:
    @pytest.mark.asyncio
    async def test_run_command_appends_url_health_for_live_server(self, tmp_path, registry):
        # Start a trivial HTTP server on an ephemeral port.
        handler = http.server.SimpleHTTPRequestHandler

        class _Server(socketserver.TCPServer):
            allow_reuse_address = True

        srv = _Server(("127.0.0.1", 0), handler)
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            router = make_router()
            seq = [
                tool_call_response([("run_command", {"command": f"echo Local: http://localhost:{port}/"})]),
                tool_response("Done."),
            ]
            seq_iter = iter(seq)

            async def _gen(**kwargs):
                return next(seq_iter)

            router.generate = _gen
            executor = Executor(router, registry)
            result = await executor.run_tool_loop(
                messages=[{"role": "user", "content": "what is at the local url"}],
            )
            # The tool result (captured in trace) should contain a url-health line.
            tool_traces = [t for t in result.trace if t.phase == "tool_call"]
            assert tool_traces
            preview = tool_traces[0].payload.get("result_preview", "")
            assert "[url-health]" in preview
            assert "HTTP 200" in preview
            assert any(r.ok for r in executor._exec_state.probe_results)
            assert any(r.response_preview for r in executor._exec_state.probe_results)
        finally:
            srv.shutdown()
            thread.join(timeout=5)

    @pytest.mark.asyncio
    async def test_explicit_fetch_records_local_server_evidence(self, registry):
        router = make_router()
        responses = [
            tool_call_response([("run_command", {"command": "npx vite"})]),
            tool_call_response([("fetch_url", {"url": "http://localhost:1420"})]),
            tool_response("The app is open."),
        ]

        async def _gen(**kwargs):
            return responses.pop(0)

        router.generate = _gen
        executor = Executor(router, registry)

        async def _fake_fetch(name, arguments):
            if name == "fetch_url":
                return "<!DOCTYPE html><title>Todo App</title>"
            return "Local: http://localhost:1420\nExit code: 0"

        registry.safe_execute = _fake_fetch
        result = await executor.run_tool_loop(
            messages=[{"role": "user", "content": "open this webapp in my browser"}],
        )

        assert result.disposition == CompletionDisposition.VERIFIED
        assert any("Reachable server: http://localhost:1420" in fact for fact in result.evidence)
