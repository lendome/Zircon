"""Regression tests for the shell_stop orphan / retry-loop bug.

Observed failure (miocai session): shell_stop('bg_3') reported "Job stopped"
but the php.exe dev server — a GRANDCHILD of the bash shell wrapper —
survived and kept listening on port 80. Confronted with "job stopped" vs.
"port still LISTENING", the agent re-issued the identical shell_stop call
six consecutive times until the loop detector killed the turn.

Root causes fixed here:
1. ProcessManager.stop / run_capture killed only the direct child (the
   shell wrapper) — kill_process_tree now kills the whole tree
   (taskkill /T /F on Windows, killpg on POSIX).
2. No breaker covered repeated identical failures of non-command tools —
   IdenticalErrorBreaker now intercepts the 3rd identical failing call.
3. The "not found" error gave no recovery path — it now names known jobs
   and explains how to kill an orphaned OS process directly.
4. ToolFingerprint omitted "pid" — shell_* calls on different jobs looked
   identical to the loop detector.
"""
import sys

import pytest

from zirconAgent.tools.shell_ops import (
    ProcessManager,
    ShellPollTool,
    ShellStartTool,
    ShellStopTool,
)
from zirconAgent.core.shell_env import kill_process_tree, spawn_kwargs
from zirconAgent.core.loop_detector import ToolFingerprint
from zirconAgent.tools.registry import IdenticalErrorBreaker, ToolRegistry


@pytest.fixture
def pm():
    return ProcessManager()


def _extract_pid(result: str) -> str:
    for line in result.splitlines():
        if line.startswith("Background job started:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no pid in: {result}")


class TestStopKillsWholeTree:
    @pytest.mark.asyncio
    async def test_stop_kills_grandchild_server(self, tmp_path, pm):
        """The grandchild process must be dead after shell_stop.

        Starts a job whose shell wrapper spawns a python grandchild that
        binds a localhost port and prints it. After stop(), the port must
        no longer accept connections — before the tree-kill fix the wrapper
        died but the grandchild kept listening (the exact miocai bug).
        """
        import asyncio

        # Written to a file: inline `-c` code gets mangled by the pinned
        # shell's argument quoting (pwsh splits on ';', bash on quotes).
        server_script = tmp_path / "_grandchild_server.py"
        server_script.write_text(
            "import socket, time\n"
            "s = socket.socket()\n"
            "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            "s.bind(('127.0.0.1', 0))\n"
            "s.listen(1)\n"
            "print('PORT=' + str(s.getsockname()[1]), flush=True)\n"
            "time.sleep(120)\n",
            encoding="utf-8",
        )
        start = ShellStartTool(str(tmp_path), pm)
        result = await start.run(
            command=f'"{sys.executable}" _grandchild_server.py', initial_wait=3,
        )
        assert "bg_" in result, result

        port = None
        for line in result.splitlines():
            if "PORT=" in line:
                port = int(line.split("PORT=")[1].strip().split()[0])
                break
        # The port may appear in stdout (or slightly later) — poll once more.
        if port is None:
            pid = _extract_pid(result)
            poll = await ShellPollTool(pm).run(pid=pid, wait=2)
            for line in poll.splitlines():
                if "PORT=" in line:
                    port = int(line.split("PORT=")[1].strip().split()[0])
                    break
        assert port is not None, result

        # Sanity: the grandchild is listening before the stop.
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        pid = _extract_pid(result)
        stop_result = await ShellStopTool(pm).run(pid=pid)
        assert "stopped" in stop_result.lower(), stop_result

        # The port must refuse connections now — the grandchild is dead.
        await asyncio.sleep(0.5)
        refused = False
        try:
            _r, _w = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=2
            )
            _w.close()
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            refused = True
        assert refused, (
            f"port {port} still accepting connections after shell_stop — "
            "the grandchild server survived (orphan bug)"
        )


class TestKillProcessTree:
    @pytest.mark.asyncio
    async def test_kill_process_tree_never_raises_on_dead_proc(self):
        import asyncio as aio

        proc = await aio.create_subprocess_exec(
            sys.executable, "-c", "pass",
            stdout=aio.subprocess.DEVNULL,
            stderr=aio.subprocess.DEVNULL,
        )
        await proc.wait()
        await kill_process_tree(proc)  # must not raise

    def test_spawn_kwargs_shape(self):
        kwargs = spawn_kwargs()
        if sys.platform == "win32":
            assert kwargs.get("creationflags") == 0x08000000  # CREATE_NO_WINDOW
        else:
            assert kwargs.get("start_new_session") is True


class TestNotFoundGuidance:
    @pytest.mark.asyncio
    async def test_stop_unknown_pid_lists_known_jobs_and_recovery(self, tmp_path, pm):
        sleeper = tmp_path / "_sleeper.py"
        sleeper.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        start = ShellStartTool(str(tmp_path), pm)
        result = await start.run(
            command=f'"{sys.executable}" _sleeper.py',
            initial_wait=1,
        )
        live_pid = _extract_pid(result)
        try:
            msg = await ShellStopTool(pm).run(pid="bg_9999")
            assert "not found" in msg.lower()
            assert live_pid in msg  # known running jobs are surfaced
            assert "Do NOT retry" in msg
            assert "taskkill" in msg or "kill -9" in msg  # orphan recovery path
        finally:
            await ShellStopTool(pm).run(pid=live_pid)

    @pytest.mark.asyncio
    async def test_stop_unknown_pid_no_tracked_jobs(self, pm):
        msg = await ShellStopTool(pm).run(pid="bg_9999")
        assert "not found" in msg.lower()
        assert "No background jobs are currently tracked" in msg

    @pytest.mark.asyncio
    async def test_poll_unknown_pid_lists_known_jobs(self, pm):
        msg = await ShellPollTool(pm).run(pid="bg_9999", wait=0)
        assert "not found" in msg.lower()
        assert "Do NOT retry" in msg


class TestIdenticalErrorBreaker:
    def test_first_two_failures_pass_third_intercepted(self):
        breaker = IdenticalErrorBreaker()
        args = {"pid": "bg_3"}
        err = "Error: background job 'bg_3' not found."
        assert breaker.check("shell_stop", args) is None
        breaker.record("shell_stop", args, err)
        assert breaker.check("shell_stop", args) is None  # one retry allowed
        breaker.record("shell_stop", args, err)
        msg = breaker.check("shell_stop", args)
        assert msg is not None and msg.startswith("CIRCUIT-BREAKER:")
        assert "shell_stop" in msg

    def test_different_args_not_intercepted(self):
        breaker = IdenticalErrorBreaker()
        err = "Error: background job 'bg_3' not found."
        breaker.record("shell_stop", {"pid": "bg_3"}, err)
        breaker.record("shell_stop", {"pid": "bg_3"}, err)
        assert breaker.check("shell_stop", {"pid": "bg_4"}) is None

    def test_success_resets_streak(self):
        breaker = IdenticalErrorBreaker()
        args = {"pid": "bg_3"}
        breaker.record("shell_stop", args, "Error: not found")
        breaker.record("shell_stop", args, "Job bg_3 stopped.")
        breaker.record("shell_stop", args, "Error: not found")
        assert breaker.check("shell_stop", args) is None

    def test_mutation_clears_state(self):
        breaker = IdenticalErrorBreaker()
        args = {"pid": "bg_3"}
        breaker.record("shell_stop", args, "Error: not found")
        breaker.record("shell_stop", args, "Error: not found")
        breaker.note_mutation()
        assert breaker.check("shell_stop", args) is None

    def test_interception_message_not_counted_as_failure(self):
        breaker = IdenticalErrorBreaker()
        args = {"pid": "bg_3"}
        breaker.record("shell_stop", args, "Error: not found")
        breaker.record("shell_stop", args, "Error: not found")
        intercepted = breaker.check("shell_stop", args)
        breaker.record("shell_stop", args, intercepted)  # guidance, not failure
        # Streak must NOT have grown past the interception.
        assert breaker._entries[("shell_stop", (("pid", "bg_3"),))][0] == 2


class TestGenericBreakerRegistryIntegration:
    @pytest.mark.asyncio
    async def test_third_identical_shell_stop_intercepted(self, tmp_path):
        registry = ToolRegistry()
        pm = ProcessManager()
        registry.register(ShellStopTool(pm))

        first = await registry.execute("shell_stop", {"pid": "bg_3"})
        assert "not found" in first.lower()
        second = await registry.execute("shell_stop", {"pid": "bg_3"})
        assert "not found" in second.lower()
        third = await registry.execute("shell_stop", {"pid": "bg_3"})
        assert third.startswith("CIRCUIT-BREAKER:"), third
        # A DIFFERENT pid still executes (and fails normally).
        other = await registry.execute("shell_stop", {"pid": "bg_4"})
        assert "not found" in other.lower()

    @pytest.mark.asyncio
    async def test_breaker_disabled_flag(self, tmp_path):
        registry = ToolRegistry()
        registry.circuit_breaker_enabled = False
        registry.register(ShellStopTool(ProcessManager()))
        for _ in range(3):
            result = await registry.execute("shell_stop", {"pid": "bg_3"})
            assert "not found" in result.lower()
            assert not result.startswith("CIRCUIT-BREAKER:")


class TestFingerprintPid:
    def test_shell_calls_on_different_jobs_are_distinct(self):
        fp3 = ToolFingerprint.from_call("shell_stop", {"pid": "bg_3"})
        fp4 = ToolFingerprint.from_call("shell_stop", {"pid": "bg_4"})
        assert fp3 != fp4

    def test_shell_calls_on_same_job_are_identical(self):
        a = ToolFingerprint.from_call("shell_stop", {"pid": "bg_3"})
        b = ToolFingerprint.from_call("shell_stop", {"pid": "bg_3"})
        assert a == b and hash(a) == hash(b)
