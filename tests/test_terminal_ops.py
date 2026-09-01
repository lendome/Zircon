"""Tests for the run_in_terminal / terminal_output / terminal_stop tools.

NOTE: these tests open REAL (short-lived) console windows on the desktop —
that is exactly what the tool under test does. Windows-only.
"""

import os
import time

import pytest

from zirconAgent.tools.terminal_ops import (
    TerminalManager,
    RunInTerminalTool,
    TerminalOutputTool,
    TerminalStopTool,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="separate terminal windows are Windows-only"
)


@pytest.fixture
def tm(tmp_path):
    return TerminalManager(tmp_path / "terminals")


@pytest.fixture
def tool(tmp_path, tm):
    return RunInTerminalTool(str(tmp_path), tm)


def _term_id(result: str) -> str:
    for line in result.splitlines():
        if line.startswith("Terminal:"):
            # "Terminal: term_1 (PID 1234)"
            return line.split(":", 1)[1].strip().split()[0]
    raise AssertionError(f"no terminal id in result:\n{result}")


async def _poll_until(out_tool, tid, predicate, attempts=20):
    final = ""
    for _ in range(attempts):
        final = await out_tool.run(id=tid, wait_seconds=1)
        if predicate(final):
            break
    return final


class TestRunInTerminal:
    @pytest.mark.asyncio
    async def test_quick_command_completes_with_exit_code(self, tool, tm):
        started = time.monotonic()
        result = await tool.run(command="echo TERM_OK", wait_seconds=5)
        elapsed = time.monotonic() - started
        assert "Opened terminal window" in result
        assert "EXITED (exit code 0)" in result
        assert elapsed < 4
        tid = _term_id(result)

        out = TerminalOutputTool(tm)
        final = await _poll_until(out, tid, lambda s: "EXITED" in s)
        assert "TERM_OK" in final
        assert "EXITED (exit code 0)" in final

    @pytest.mark.asyncio
    async def test_failing_command_reports_exit_code(self, tool, tm):
        # NB: `cmd /c exit 3` — a BARE `exit 3` would kill the batch
        # interpreter before the completion-marker line could run.
        result = await tool.run(command="cmd /c exit 3", wait_seconds=2)
        tid = _term_id(result)

        out = TerminalOutputTool(tm)
        final = await _poll_until(out, tid, lambda s: "EXITED" in s)
        assert "EXITED (exit code 3)" in final

    @pytest.mark.asyncio
    async def test_long_running_stays_running_then_stops(self, tool, tm):
        result = await tool.run(
            command="ping -n 60 127.0.0.1 >nul", wait_seconds=2, title="pytest term"
        )
        assert "RUNNING" in result
        tid = _term_id(result)

        out = await TerminalOutputTool(tm).run(id=tid, wait_seconds=1)
        assert "RUNNING" in out
        assert "keeps running" in result or "still running" in result.lower()

        stop = await TerminalStopTool(tm).run(id=tid)
        assert "Stopped terminal" in stop
        assert tid in stop

        after = await TerminalOutputTool(tm).run(id=tid, wait_seconds=0)
        assert "EXITED" in after

    @pytest.mark.asyncio
    async def test_output_grows_between_polls(self, tool, tm):
        result = await tool.run(
            command="echo FIRST & ping -n 5 127.0.0.1 >nul & echo SECOND",
            wait_seconds=1,
        )
        tid = _term_id(result)

        out = TerminalOutputTool(tm)
        final = await _poll_until(out, tid, lambda s: "SECOND" in s)
        assert "FIRST" in final
        assert "SECOND" in final

    @pytest.mark.asyncio
    async def test_output_visible_after_wait_timeout(self, tool, tm):
        """The core contract: a still-running command's output is readable
        after the tool-call-defined wait_seconds elapses."""
        result = await tool.run(
            command="echo READY & ping -n 30 127.0.0.1 >nul", wait_seconds=3
        )
        assert "RUNNING" in result
        assert "READY" in result  # output captured so far is returned
        tid = _term_id(result)
        await TerminalStopTool(tm).run(id=tid)

    @pytest.mark.asyncio
    async def test_unknown_id(self, tm):
        out = await TerminalOutputTool(tm).run(id="term_999", wait_seconds=0)
        assert "not found" in out.lower()

        stop = await TerminalStopTool(tm).run(id="term_999")
        assert "not found" in stop.lower()

    @pytest.mark.asyncio
    async def test_invalid_cwd(self, tool):
        result = await tool.run(command="echo hi", cwd="nonexistent_dir_xyz")
        assert "Error" in result
