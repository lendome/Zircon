"""Regression tests for the shell_stop infinite-loop bug.

Background (the observed failure): a background shell job (bg_3) went stale —
its wrapper process died but the job stayed registered — and shell_stop(bg_3)
returned "Error: background job 'bg_3' not found". The model retried the
byte-identical call six consecutive times:

  shell_stop({"pid": "bg_3"}) -> "...not found"   (x6)

The ToolRegistry circuit breakers did NOT fire because:
1. The command circuit breaker only intercepts run_command-family tools.
2. The edit-failure breaker only intercepts edit tools.
3. No breaker covered "any non-command tool that returns an identical error
   three times in a row".
4. LoopDetector fingerprints did not include the `pid` argument, so distinct
   stop/poll targets collided into one fingerprint.

These tests pin the fixes.
"""
from __future__ import annotations

import pytest

from zirconAgent.core.loop_detector import LoopDetector, ToolFingerprint
from zirconAgent.core.types import TierConfig
from zirconAgent.tests.mocks import make_router, tool_call_response, tool_response
from zirconAgent.core.executor import Executor
from zirconAgent.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# 1. LoopDetector fingerprint must include `pid`
# ---------------------------------------------------------------------------

class TestPidFingerprint:
    def test_distinct_pids_are_distinct_fingerprints(self):
        a = ToolFingerprint.from_call("shell_stop", {"pid": "bg_1"})
        b = ToolFingerprint.from_call("shell_stop", {"pid": "bg_3"})
        assert a != b

    def test_same_pid_same_fingerprint(self):
        a = ToolFingerprint.from_call("shell_stop", {"pid": "bg_3"})
        b = ToolFingerprint.from_call("shell_stop", {"pid": "bg_3"})
        assert a == b

    def test_poll_targets_are_distinguished(self):
        a = ToolFingerprint.from_call("shell_poll", {"pid": "bg_1", "wait": 2})
        b = ToolFingerprint.from_call("shell_poll", {"pid": "bg_2", "wait": 2})
        assert a != b


# ---------------------------------------------------------------------------
# 2. Generic identical-error circuit breaker (registry level)
# ---------------------------------------------------------------------------

class _FailingTool:
    name = "shell_stop"
    description = "stops a job"
    schema = {"properties": {"pid": {"type": "string"}}}

    def to_openai_schema(self):
        return {"name": self.name, "parameters": self.schema}

    async def run(self, **kwargs):
        return f"Error: background job '{kwargs.get('pid')}' not found."


class _OkTool(_FailingTool):
    name = "read_file"

    async def run(self, **kwargs):
        return "file contents"



class TestIdenticalErrorBreaker:
    @pytest.mark.asyncio
    async def test_third_identical_error_is_intercepted(self, tmp_path):
        reg = ToolRegistry()
        reg.register(_FailingTool())
        reg.circuit_breaker_enabled = True

        r1 = await reg.safe_execute("shell_stop", {"pid": "bg_3"})
        r2 = await reg.safe_execute("shell_stop", {"pid": "bg_3"})
        r3 = await reg.safe_execute("shell_stop", {"pid": "bg_3"})

        assert "not found" in r1
        assert "not found" in r2
        assert "CIRCUIT-BREAKER" in r3
        assert "do NOT repeat this call" in r3
        # The underlying tool must NOT have been executed a third time.
        assert "not found" not in r3

    @pytest.mark.asyncio
    async def test_different_args_reset_the_streak(self, tmp_path):
        reg = ToolRegistry()
        reg.register(_FailingTool())
        reg.circuit_breaker_enabled = True

        await reg.safe_execute("shell_stop", {"pid": "bg_1"})
        await reg.safe_execute("shell_stop", {"pid": "bg_2"})
        # Different pid each time — the streak never reaches 3 identical calls.
        r3 = await reg.safe_execute("shell_stop", {"pid": "bg_3"})
        assert "not found" in r3
        assert "CIRCUIT-BREAKER" not in r3

    @pytest.mark.asyncio
    async def test_success_resets_the_streak(self, tmp_path):
        reg = ToolRegistry()
        tool = _FailingTool()
        reg.register(tool)
        reg.circuit_breaker_enabled = True

        await reg.safe_execute("shell_stop", {"pid": "bg_3"})
        await reg.safe_execute("shell_stop", {"pid": "bg_3"})

        # A success between failures must reset the streak.
        orig = tool.run

        async def _ok(**kw):
            return "Background job bg_3 stopped."

        tool.run = _ok
        ok = await reg.safe_execute("shell_stop", {"pid": "bg_3"})
        assert "stopped" in ok

        tool.run = orig

        r = await reg.safe_execute("shell_stop", {"pid": "bg_3"})
        assert "not found" in r
        assert "CIRCUIT-BREAKER" not in r

    @pytest.mark.asyncio
    async def test_successful_calls_never_trigger_breaker(self, tmp_path):
        reg = ToolRegistry()
        reg.register(_OkTool())
        reg.circuit_breaker_enabled = True
        for _ in range(5):
            r = await reg.safe_execute("read_file", {"path": "x.py"})
            assert "CIRCUIT-BREAKER" not in r

    @pytest.mark.asyncio
    async def test_disabled_flag_bypasses_breaker(self, tmp_path):
        reg = ToolRegistry()
        reg.register(_FailingTool())
        reg.circuit_breaker_enabled = False
        for _ in range(4):
            r = await reg.safe_execute("shell_stop", {"pid": "bg_3"})
            assert "not found" in r
            assert "CIRCUIT-BREAKER" not in r


# ---------------------------------------------------------------------------
# 3. End-to-end: the executor loop must not repeat the failing call forever
# ---------------------------------------------------------------------------

class TestExecutorDoesNotLoopOnStaleJob:
    @pytest.mark.asyncio
    async def test_stale_shell_stop_does_not_loop(self, tmp_path):
        """Model keeps emitting the identical shell_stop for a stale job.

        After the breaker intercepts, the loop must terminate (model receives
        a CIRCUIT-BREAKER error and, in this mock, stops calling tools).
        """
        from zirconAgent.tools.shell_ops import ShellStopTool, ProcessManager

        pm = ProcessManager()
        reg = ToolRegistry()
        reg.register(ShellStopTool(pm))

        # The model wants to call shell_stop(bg_3) forever.
        stale_stop = tool_call_response([("shell_stop", {"pid": "bg_3"})])
        done = tool_response("The job is already gone; moving on with a different approach.")

        calls: list[str] = []

        async def _generate(**kw):
            # Once a CIRCUIT-BREAKER message is present in the conversation,
            # the (mock) model stops calling the tool.
            for m in kw.get("messages", []):
                c = m.get("content") or ""
                if "CIRCUIT-BREAKER" in str(c):
                    return done
            calls.append("stop")
            return stale_stop

        router = make_router()
        router.generate = _generate

        executor = Executor(router, reg, tier_config=TierConfig(name="balanced"))
        result = await executor.run_tool_loop(
            [{"role": "user", "content": "stop the bg_3 job"}],
            reg.get_schemas(),
            max_turns=10,
        )

        # The breaker fired well before the 10-turn cap — the observed bug
        # needed 6 identical calls before the external watchdog stepped in.
        assert len(calls) <= 3
        assert result.success
        assert "CIRCUIT-BREAKER" not in (result.output or "")


# ---------------------------------------------------------------------------
# 4. shell_stop on a stale-but-registered job must clean up, not error-loop
# ---------------------------------------------------------------------------

class TestStaleJobStop:
    @pytest.mark.asyncio
    async def test_stop_on_stale_job_is_cleaned_up_and_reported(self, tmp_path):
        """A job whose wrapper died (orphan grandchild) should be removable:
        stopping it must succeed in cleaning the registry entry rather than
        returning 'not found' forever."""
        from zirconAgent.tools.shell_ops import ProcessManager

        pm = ProcessManager()
        # Simulate a registered job whose process object is already dead.
        class _DeadProc:
            returncode = 1

            def terminate(self):
                raise ProcessLookupError("No such process")

            def kill(self):
                raise ProcessLookupError("No such process")

        pm._jobs["bg_3"] = {
            "proc": _DeadProc(),
            "command": "sleep 999",
            "cwd": str(tmp_path),
            "stdout": [],
            "stderr": [],
        }
        output = await pm.stop("bg_3")
        assert "bg_3" not in pm._jobs
        # The report should mention it was already gone, not a bare error.
        assert "not found" not in output.lower() or "already" in output.lower()

    @pytest.mark.asyncio
    async def test_stop_unknown_job_gives_actionable_guidance(self, tmp_path):
        from zirconAgent.tools.shell_ops import ProcessManager

        pm = ProcessManager()
        output = await pm.stop("bg_99")
        assert "bg_99" in output
        # Must tell the agent what to do next instead of a bare error string.
        assert "list" in output.lower() or "known" in output.lower() or "no background" in output.lower()
