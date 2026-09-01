import pytest
import asyncio
import time

from zirconAgent.tools.shell_ops import (
    ProcessManager,
    PythonReplManager,
    ReplCloseTool,
    ReplExecTool,
    ReplOpenTool,
    RunCommandTool,
    ShellPollTool,
    ShellStartTool,
    ShellStopTool,
)


@pytest.fixture
def pm():
    return ProcessManager()


@pytest.fixture
def tool(tmp_path, pm):
    return RunCommandTool(str(tmp_path), pm)


class TestRunCommandTool:
    @pytest.mark.asyncio
    async def test_echo(self, tool):
        result = await tool.run(command="echo hello")
        assert "hello" in result
        assert "Exit code: 0" in result

    @pytest.mark.asyncio
    async def test_exit_code(self, tool):
        result = await tool.run(command="exit 1")
        assert "Exit code: 1" in result

    @pytest.mark.asyncio
    async def test_stderr(self, tool):
        result = await tool.run(command="echo error >&2")
        assert "STDERR" in result or "error" in result

    @pytest.mark.asyncio
    async def test_timeout(self, tool):
        result = await tool.run(command="python -c \"import time; time.sleep(10)\"", timeout=1)
        assert "still running" in result.lower() or "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_timeout_captures_partial_output(self, tool):
        result = await tool.run(
            command='python -u -c "import time, sys; print(\'start\'); sys.stdout.flush(); time.sleep(10); print(\'end\')"',
            timeout=1,
        )
        assert "start" in result
        assert "still running" in result.lower() or "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_timeout_with_pm_offers_background_pid(self, tmp_path, pm):
        t = RunCommandTool(str(tmp_path), pm)
        result = await t.run(
            command="python -c \"import time; print('running'); time.sleep(30)\"",
            timeout=1,
        )
        assert "shell_poll" in result or "background" in result.lower()

    @pytest.mark.asyncio
    async def test_cwd(self, tool, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        result = await tool.run(command="echo test", cwd="sub")
        assert "test" in result

    @pytest.mark.asyncio
    async def test_invalid_cwd(self, tool):
        result = await tool.run(command="echo hi", cwd="nonexistent")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_python_version(self, tool):
        result = await tool.run(command="python --version")
        assert "Python" in result


class TestShellBackgroundTools:
    @pytest.mark.asyncio
    async def test_shell_start_and_poll(self, tmp_path, pm):
        start_tool = ShellStartTool(str(tmp_path), pm)
        result = await start_tool.run(
            command="python -c \"import time; print('hello'); time.sleep(30); print('done')\"",
            initial_wait=1,
        )
        assert "bg_" in result
        assert "RUNNING" in result
        assert "hello" in result

        pid = None
        for line in result.splitlines():
            if line.startswith("Background job started:"):
                pid = line.split(":", 1)[1].strip()
                break
        assert pid is not None

        poll_tool = ShellPollTool(pm)
        poll_result = await poll_tool.run(pid=pid, wait=1)
        assert "RUNNING" in poll_result
        assert pid in poll_result

        stop_tool = ShellStopTool(pm)
        stop_result = await stop_tool.run(pid=pid)
        assert "stopped" in stop_result.lower()
        assert pid in stop_result

    @pytest.mark.asyncio
    async def test_shell_start_exited_quickly(self, tmp_path, pm):
        start_tool = ShellStartTool(str(tmp_path), pm)
        started = time.monotonic()
        result = await start_tool.run(command="echo quick", initial_wait=2)
        elapsed = time.monotonic() - started
        assert "bg_" in result
        assert "EXITED" in result
        assert elapsed < 1.5

    @pytest.mark.asyncio
    async def test_shell_poll_returns_when_process_exits(self, tmp_path, pm):
        pid, _ = await pm.start(
            "python -c \"import time; time.sleep(0.2)\"",
            str(tmp_path),
            initial_wait=0,
        )

        started = time.monotonic()
        result = await ShellPollTool(pm).run(pid=pid, wait=3)
        elapsed = time.monotonic() - started

        assert "EXITED (0)" in result
        assert elapsed < 1.5

    @pytest.mark.asyncio
    async def test_shell_poll_missing_pid(self, pm):
        poll_tool = ShellPollTool(pm)
        result = await poll_tool.run(pid="bg_9999")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_shell_stop_missing_pid(self, pm):
        stop_tool = ShellStopTool(pm)
        result = await stop_tool.run(pid="bg_9999")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_multiple_background_jobs(self, tmp_path, pm):
        start_tool = ShellStartTool(str(tmp_path), pm)
        r1 = await start_tool.run(command="python -c \"import time; print('a'); time.sleep(30)\"", initial_wait=1)
        r2 = await start_tool.run(command="python -c \"import time; print('b'); time.sleep(30)\"", initial_wait=1)

        pid1 = r1.split("Background job started:")[1].split()[0].strip()
        pid2 = r2.split("Background job started:")[1].split()[0].strip()

        assert pid1 != pid2
        assert len(pm.list_running()) >= 2

        stop = ShellStopTool(pm)
        await stop.run(pid=pid1)
        await stop.run(pid=pid2)

    @pytest.mark.asyncio
    async def test_shell_start_cwd(self, tmp_path, pm):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        start_tool = ShellStartTool(str(tmp_path), pm)
        result = await start_tool.run(command="python -c \"import os; print(os.getcwd())\"", cwd="sub", initial_wait=1)
        assert "bg_" in result
        assert str(subdir) in result or "sub" in result


class TestPersistentPythonRepl:
    @pytest.mark.asyncio
    async def test_state_survives_across_exec_calls(self, tmp_path):
        manager = PythonReplManager()
        opened = await ReplOpenTool(str(tmp_path), manager).run()
        session_id = opened.split("Python REPL opened:", 1)[1].splitlines()[0].strip()
        execute = ReplExecTool(manager)

        assert "OK" in await execute.run(session_id, "values = [1, 2, 3]")
        result = await execute.run(session_id, "sum(values)")

        assert "VALUE:\n6" in result
        assert "closed" in (await ReplCloseTool(manager).run(session_id)).lower()

    @pytest.mark.asyncio
    async def test_exception_does_not_destroy_session(self, tmp_path):
        manager = PythonReplManager()
        opened = await ReplOpenTool(str(tmp_path), manager).run()
        session_id = opened.split("Python REPL opened:", 1)[1].splitlines()[0].strip()
        execute = ReplExecTool(manager)

        failure = await execute.run(session_id, "1 / 0")
        recovery = await execute.run(session_id, "40 + 2")

        assert "ZeroDivisionError" in failure
        assert "VALUE:\n42" in recovery
        await ReplCloseTool(manager).run(session_id)
