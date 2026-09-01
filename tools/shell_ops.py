from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

from .base import Tool
from ..core.shell_env import (
    kill_process_tree,
    run_capture,
    resolve_shell,
    sanitize_command_for_shell,
    shell_syntax_hint,
    spawn_kwargs,
)


_PYTHON_REPL_WORKER = r'''import ast, contextlib, io, json, traceback
scope = {"__name__": "__zircon_repl__"}
for raw in iter(input, ""):
    request = json.loads(raw)
    output, errors = io.StringIO(), io.StringIO()
    value = None
    try:
        tree = ast.parse(request["code"], mode="exec")
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                prefix = ast.Module(body=tree.body[:-1], type_ignores=[])
                exec(compile(prefix, "<repl>", "exec"), scope)
                value = eval(compile(ast.Expression(tree.body[-1].value), "<repl>", "eval"), scope)
            else:
                exec(compile(tree, "<repl>", "exec"), scope)
    except BaseException:
        traceback.print_exc(file=errors)
    stdout = output.getvalue()
    stderr = errors.getvalue()
    rendered = repr(value) if value is not None else ""
    print(json.dumps({"stdout": stdout[-12000:], "stderr": stderr[-12000:], "value": rendered[:12000]}), flush=True)
'''


class PythonReplManager:
    """Process-local persistent Python namespaces with framed JSON I/O."""

    def __init__(self) -> None:
        self._sessions: dict[str, asyncio.subprocess.Process] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_id = 0

    async def open(self, cwd: str) -> str:
        encoded = base64.b64encode(_PYTHON_REPL_WORKER.encode("utf-8")).decode("ascii")
        command = f"import base64;exec(base64.b64decode({encoded!r}))"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-c", command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            **spawn_kwargs(),
        )
        self._next_id += 1
        session_id = f"repl_{self._next_id}"
        self._sessions[session_id] = proc
        self._locks[session_id] = asyncio.Lock()
        return session_id

    def get(self, session_id: str) -> asyncio.subprocess.Process | None:
        return self._sessions.get(session_id)

    async def execute(self, session_id: str, code: str, timeout: int) -> dict[str, str] | None:
        proc = self.get(session_id)
        if proc is None or proc.returncode is not None or proc.stdin is None or proc.stdout is None:
            return None
        async with self._locks[session_id]:
            proc.stdin.write((json.dumps({"code": code}) + "\n").encode("utf-8"))
            await proc.stdin.drain()
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=max(1, min(timeout, 120)))
        if not line:
            return None
        return json.loads(line.decode("utf-8", errors="replace"))

    async def close(self, session_id: str) -> bool:
        proc = self._sessions.pop(session_id, None)
        self._locks.pop(session_id, None)
        if proc is None:
            return False
        if proc.returncode is None:
            await kill_process_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        return True


class BackgroundProcess:
    def __init__(self, proc: asyncio.subprocess.Process, command: str, cwd: str | None):
        self.proc = proc
        self.command = command
        self.cwd = cwd
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.start_time = time.monotonic()
        self._reader_tasks: list[asyncio.Task] = []
        self._done = False

    async def send_input(self, data: str) -> str:
        """Send a line of text to the process's stdin.
        
        Returns a confirmation message.
        """
        if self.proc.returncode is not None:
            return f"Process has already exited with code {self.proc.returncode}"
        if self.proc.stdin is None:
            return "Error: process was not started with stdin pipe enabled"
        try:
            self.proc.stdin.write((data + "\n").encode("utf-8", errors="replace"))
            await self.proc.stdin.drain()
            return f"Sent input to process: {data[:200]}"
        except BrokenPipeError:
            return "Error: broken pipe — process may have exited"
        except Exception as e:
            return f"Error sending input: {e}"

    async def close_stdin(self) -> str:
        """Close the stdin pipe, signaling EOF to the process."""
        if self.proc.stdin is None:
            return "stdin already closed or not available"
        try:
            self.proc.stdin.close()
            return "stdin closed (EOF sent)"
        except Exception as e:
            return f"Error closing stdin: {e}"

    def runtime(self) -> float:
        return time.monotonic() - self.start_time

    def is_running(self) -> bool:
        return self.proc.returncode is None

    async def wait_until_exit(self, timeout: float) -> None:
        """Wait up to timeout seconds, returning early when the process exits."""
        if timeout <= 0 or not self.is_running():
            return
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=timeout)
            # Give the pipe readers a turn to consume output buffered at exit.
            await asyncio.sleep(0)
        except asyncio.TimeoutError:
            pass

    def snapshot(self, max_chars: int = 8000) -> dict[str, Any]:
        out = "".join(self.stdout_lines)
        err = "".join(self.stderr_lines)
        if len(out) > max_chars:
            out = "..." + out[-max_chars:]
        if len(err) > max_chars:
            err = "..." + err[-max_chars:]
        return {
            "running": self.is_running(),
            "returncode": self.proc.returncode,
            "runtime_seconds": round(self.runtime(), 1),
            "command": self.command,
            "cwd": self.cwd,
            "stdout": out,
            "stderr": err,
        }


class ProcessManager:
    def __init__(self):
        self._processes: dict[str, BackgroundProcess] = {}
        self._next_id = 0

    def _alloc_id(self) -> str:
        self._next_id += 1
        return f"bg_{self._next_id}"

    def list_running(self) -> list[str]:
        return [pid for pid, bp in self._processes.items() if bp.is_running()]

    async def start(
        self,
        command: str,
        cwd: str | None,
        initial_wait: float = 3.0,
        stdin_input: str | None = None,
        pinning_enabled: bool = True,
    ) -> tuple[str, BackgroundProcess]:
        spec = resolve_shell(pinning_enabled)
        # Rewrite cmd-isms the pinned shell cannot understand (e.g. `2>nul`
        # under Git Bash creates a literal file named `nul`).
        command, sanitize_note = sanitize_command_for_shell(command, spec)
        # spawn_kwargs: CREATE_NO_WINDOW on Windows, start_new_session on
        # POSIX — the child leads its own process group so stop() can kill
        # the whole tree (wrapper + server) without touching ours.
        if spec.uses_default_shell:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                **spawn_kwargs(),
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                spec.exe, *spec.prefix_args, command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                **spawn_kwargs(),
            )
        bp = BackgroundProcess(proc, command, cwd)
        if sanitize_note:
            # Surface the rewrite in the job's output so the model learns
            # the correct syntax for the pinned shell.
            bp.stderr_lines.append(sanitize_note + "\n")

        async def _drain(stream: asyncio.StreamReader, buf: list[str]) -> None:
            try:
                while True:
                    data = await stream.read(4096)
                    if not data:
                        break
                    buf.append(data.decode("utf-8", errors="replace"))
            except Exception:
                pass

        bp._reader_tasks = [
            asyncio.create_task(_drain(proc.stdout, bp.stdout_lines)),
            asyncio.create_task(_drain(proc.stderr, bp.stderr_lines)),
        ]

        # Send initial stdin if provided
        if stdin_input is not None:
            await bp.send_input(stdin_input)

        await bp.wait_until_exit(initial_wait)

        pid = self._alloc_id()
        self._processes[pid] = bp
        return pid, bp

    def get(self, pid: str) -> BackgroundProcess | None:
        return self._processes.get(pid)

    def adopt(
        self,
        proc: asyncio.subprocess.Process,
        command: str,
        cwd: str | None,
        *,
        stdout_lines: list[str] | None = None,
        stderr_lines: list[str] | None = None,
        reader_tasks: list[asyncio.Task] | None = None,
    ) -> str:
        """Adopt an already-running process (e.g. a run_command that timed out)
        as a background job WITHOUT restarting it.

        The existing output buffers and drain tasks are attached so shell_poll
        keeps seeing new output. This replaces the old behaviour of spawning a
        SECOND copy of the command on timeout, which duplicated servers and
        caused port conflicts.
        """
        bp = BackgroundProcess(proc, command, cwd)
        if stdout_lines is not None:
            bp.stdout_lines = stdout_lines
        if stderr_lines is not None:
            bp.stderr_lines = stderr_lines
        if reader_tasks is not None:
            bp._reader_tasks = list(reader_tasks)
        pid = self._alloc_id()
        self._processes[pid] = bp
        return pid

    def snapshot(self, pid: str) -> dict[str, Any] | None:
        bp = self._processes.get(pid)
        if bp is None:
            return None
        return bp.snapshot()

    async def stop(self, pid: str) -> dict[str, Any] | None:
        bp = self._processes.get(pid)
        if bp is None:
            return None

        if bp.is_running():
            # Kill the whole process TREE, not just the shell wrapper.
            # proc.terminate() only signals the direct child (bash -c /
            # cmd /c) — the actual server it launched survived as an
            # orphan still holding its port, so "Job stopped" was a lie
            # and the agent retried shell_stop forever. taskkill /T /F on
            # Windows, killpg on POSIX (see core/shell_env.py).
            await kill_process_tree(bp.proc)
            try:
                await asyncio.wait_for(bp.proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                bp.proc.kill()
                await bp.proc.wait()

        for t in bp._reader_tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        await asyncio.sleep(0.2)

        snap = bp.snapshot(max_chars=12000)
        del self._processes[pid]
        return snap



class RunCommandTool(Tool):
    def __init__(self, repo_path: str, process_manager: ProcessManager | None = None, pinning_enabled: bool = True):
        self.repo_path = Path(repo_path).resolve()
        self.pm = process_manager
        self._pinning = pinning_enabled

    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        hint = shell_syntax_hint(resolve_shell(self._pinning))
        return (
            "Execute a shell command and wait for it to finish. "
            "Use ONLY for short-lived commands that exit on their own: tests, linters, "
            "pip install, git status, etc. "
            "NEVER use this to start servers, daemons, watchers, or anything that keeps "
            "running: it blocks until the process exits. Use run_in_terminal (opens a "
            "separate visible terminal window) or shell_start (hidden background job) "
            "for those instead. " + hint
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "cwd": {"type": "string", "description": "Working directory (optional, default: repo root)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
                "input": {"type": "string", "description": "Text to send to the command's stdin (optional). Use this when the command prompts for input (e.g., yes/no confirmations, passwords, interactive prompts)."},
            },
            "required": ["command"],
        }

    async def run(self, command: str, cwd: str | None = None, timeout: int = 30, input: str | None = None) -> str:
        work_dir = self._resolve(cwd) if cwd else self.repo_path
        if not work_dir.is_dir():
            return f"Error: directory not found: {cwd or '.'}"

        spec = resolve_shell(self._pinning)
        # Keep the process alive on timeout so it can be ADOPTED as a
        # background job instead of restarted (restarting duplicated servers
        # and caused port conflicts).
        result = await run_capture(
            command,
            cwd=str(work_dir),
            timeout=float(timeout),
            stdin_text=input,
            spec=spec,
            kill_on_timeout=False,
        )

        if result.timed_out and result.proc is not None and self.pm is not None:
            pid = self.pm.adopt(
                result.proc, command, str(work_dir),
                stdout_lines=result.stdout_buf,
                stderr_lines=result.stderr_buf,
                reader_tasks=result.drain_tasks,
            )
            out_text = result.stdout.strip()
            err_text = result.stderr.strip()
            parts = [
                f"Command still running after {timeout}s timeout — moved to "
                f"background job '{pid}' (original process kept, NOT restarted)."
            ]
            if out_text:
                parts.append(f"STDOUT (so far):\n{out_text}")
            if err_text:
                parts.append(f"STDERR (so far):\n{err_text}")
            parts.append(
                f"Use shell_poll(pid='{pid}') to check its output, or "
                f"shell_stop(pid='{pid}') to stop it. "
                "TIP: for servers and other long-running commands, prefer "
                "run_in_terminal — it opens a separate terminal window and "
                "never blocks."
            )
            return "\n\n".join(parts)

        # No process manager to adopt the survivor — kill it so it never
        # lingers, then report the partial output captured so far.
        if result.timed_out and result.proc is not None:
            try:
                result.proc.kill()
            except Exception:
                pass
            for t in result.drain_tasks:
                t.cancel()

        return self._format(result, timeout)

    @staticmethod
    def _format(result, timeout: int) -> str:
        parts: list[str] = []
        if getattr(result, "sanitize_note", ""):
            parts.append(result.sanitize_note)
        if result.timed_out:
            parts.append(f"Command still running after {timeout}s timeout and was killed.")
        out_text = result.stdout.strip()
        err_text = result.stderr.strip()
        if out_text:
            parts.append(out_text)
        if err_text:
            parts.append(f"STDERR:\n{err_text}")
        if not result.timed_out:
            parts.append(f"Exit code: {result.exit_code}")
        if result.pipe_held_open:
            parts.append(
                "Note: the command exited, but a detached child process is still "
                "holding its output pipe open, so the captured output may be "
                "incomplete. For long-running or background processes use "
                "run_in_terminal (separate terminal window) or shell_start."
            )
        text = "\n".join(parts)
        if len(text) > 10000:
            text = text[:10000] + "\n... (truncated)"
        return text

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p



class ShellStartTool(Tool):
    def __init__(self, repo_path: str, process_manager: ProcessManager, pinning_enabled: bool = True):
        self.repo_path = Path(repo_path).resolve()
        self.pm = process_manager
        self._pinning = pinning_enabled

    @property
    def name(self) -> str:
        return "shell_start"

    @property
    def description(self) -> str:
        hint = shell_syntax_hint(resolve_shell(self._pinning))
        return (
            "Start a long-running or blocking command as a background job. "
            "Returns a PID you can use with shell_poll and shell_stop. "
            "Use this for dev servers, build watchers, test runners, etc. "
            "After the initial_wait period the command's startup output is returned. " + hint
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run in background"},
                "cwd": {"type": "string", "description": "Working directory (optional)"},
                "initial_wait": {"type": "integer", "description": "Seconds to wait collecting startup output (default: 3)"},
            },
            "required": ["command"],
        }

    async def run(self, command: str, cwd: str | None = None, initial_wait: int = 3) -> str:
        work_dir = self._resolve(cwd) if cwd else self.repo_path
        if not work_dir.is_dir():
            return f"Error: directory not found: {cwd or '.'}"

        try:
            pid, bp = await self.pm.start(command, str(work_dir), initial_wait=initial_wait, pinning_enabled=self._pinning)
            snap = bp.snapshot(max_chars=6000)
            lines = [
                f"Background job started: {pid}",
                f"Command: {command}",
                f"Status: {'RUNNING' if snap['running'] else 'EXITED (' + str(snap['returncode']) + ')'}",
                f"Runtime: {snap['runtime_seconds']}s",
            ]
            if snap["stdout"]:
                lines.append(f"STDOUT (first {initial_wait}s):\n{snap['stdout']}")
            if snap["stderr"]:
                lines.append(f"STDERR (first {initial_wait}s):\n{snap['stderr']}")
            lines.append(f"Use shell_poll(pid='{pid}') to read more output.")
            lines.append(f"Use shell_stop(pid='{pid}') to stop the job.")
            return "\n\n".join(lines)
        except Exception as e:
            return f"Error starting background command: {e}"

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class ShellPollTool(Tool):
    def __init__(self, process_manager: ProcessManager):
        self.pm = process_manager

    @property
    def name(self) -> str:
        return "shell_poll"

    @property
    def description(self) -> str:
        return (
            "Read the latest output from a background job started with shell_start. "
            "Use this to check if a dev server has finished starting, to tail logs, etc."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pid": {"type": "string", "description": "Background job ID returned by shell_start"},
                "wait": {"type": "integer", "description": "Seconds to wait for new output before returning (default: 2)"},
            },
            "required": ["pid"],
        }

    async def run(self, pid: str, wait: int = 2) -> str:
        bp = self.pm.get(pid)
        if bp is None:
            running = self.pm.list_running()
            known = (
                f" Known running jobs: {', '.join(running)}."
                if running else " No background jobs are currently tracked."
            )
            return (
                f"Error: background job '{pid}' not found — it was already "
                f"stopped or never started.{known} Do NOT retry this pid; "
                f"it will keep failing."
            )

        if wait > 0 and bp.is_running():
            await bp.wait_until_exit(wait)

        snap = bp.snapshot(max_chars=8000)
        lines = [
            f"Job: {pid}",
            f"Status: {'RUNNING' if snap['running'] else 'EXITED (' + str(snap['returncode']) + ')'}",
            f"Runtime: {snap['runtime_seconds']}s",
        ]
        if snap["stdout"]:
            lines.append(f"STDOUT:\n{snap['stdout']}")
        if snap["stderr"]:
            lines.append(f"STDERR:\n{snap['stderr']}")
        if snap["running"]:
            lines.append("Process is still running. Use shell_poll again to read more, or shell_stop to kill it.")
        return "\n\n".join(lines)


class ShellStopTool(Tool):
    def __init__(self, process_manager: ProcessManager):
        self.pm = process_manager

    @property
    def name(self) -> str:
        return "shell_stop"

    @property
    def description(self) -> str:
        return (
            "Stop a background job started with shell_start and return its final output. "
            "Always stop background servers when you are done testing."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pid": {"type": "string", "description": "Background job ID returned by shell_start"},
            },
            "required": ["pid"],
        }

    async def run(self, pid: str) -> str:
        snap = await self.pm.stop(pid)
        if snap is None:
            running = self.pm.list_running()
            known = (
                f" Known running jobs: {', '.join(running)}."
                if running else " No background jobs are currently tracked."
            )
            return (
                f"Error: background job '{pid}' not found — it was already "
                f"stopped or never started.{known} Do NOT retry shell_stop "
                f"with this pid; it will keep failing. If a process from "
                f"that job is STILL running (orphaned child holding a "
                f"port), find it by port (netstat -ano | grep LISTEN on "
                f"Windows, lsof -i :PORT on POSIX) and kill the OS PID "
                f"directly via run_command, e.g. 'taskkill //PID <os_pid> "
                f"//F //T' under Git Bash ('taskkill /PID <os_pid> /F /T' "
                f"under cmd) or 'kill -9 <os_pid>' on POSIX."
            )

        lines = [
            f"Job {pid} stopped.",
            f"Final exit code: {snap['returncode']}",
            f"Total runtime: {snap['runtime_seconds']}s",
        ]
        if snap["stdout"]:
            lines.append(f"Final STDOUT:\n{snap['stdout']}")
        if snap["stderr"]:
            lines.append(f"Final STDERR:\n{snap['stderr']}")
        return "\n\n".join(lines)


class ShellInputTool(Tool):
    """Send input to a running background process started with shell_start."""

    def __init__(self, process_manager: ProcessManager):
        self.pm = process_manager

    @property
    def name(self) -> str:
        return "shell_input"

    @property
    def description(self) -> str:
        return (
            "Send a line of text to a running background process's stdin. "
            "Use this when a background process is waiting for interactive input "
            "(e.g., yes/no prompts, passwords, or other interactive prompts). "
            "The input is sent followed by a newline. "
            "Use shell_poll to see the process's response after sending input."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pid": {"type": "string", "description": "Background job ID returned by shell_start"},
                "input": {"type": "string", "description": "Text to send to the process's stdin"},
            },
            "required": ["pid", "input"],
        }

    async def run(self, pid: str, input: str) -> str:
        bp = self.pm.get(pid)
        if bp is None:
            return f"Error: background job '{pid}' not found."
        return await bp.send_input(input)


class ShellCloseStdinTool(Tool):
    """Close stdin on a running background process, signaling EOF."""

    def __init__(self, process_manager: ProcessManager):
        self.pm = process_manager

    @property
    def name(self) -> str:
        return "shell_close_stdin"

    @property
    def description(self) -> str:
        return (
            "Close the stdin pipe of a background process, sending EOF. "
            "Use this when you're done sending input via shell_input "
            "and want the process to know no more input is coming."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pid": {"type": "string", "description": "Background job ID returned by shell_start"},
            },
            "required": ["pid"],
        }

    async def run(self, pid: str) -> str:
        bp = self.pm.get(pid)
        if bp is None:
            return f"Error: background job '{pid}' not found."
        return await bp.close_stdin()


class ReplOpenTool(Tool):
    def __init__(self, repo_path: str, manager: PythonReplManager):
        self.repo_path = Path(repo_path).resolve()
        self.manager = manager

    @property
    def name(self) -> str:
        return "repl_open"

    @property
    def description(self) -> str:
        return "Open a persistent Python REPL for iterative data exploration. Variables and imports survive repl_exec calls."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"cwd": {"type": "string", "description": "Working directory (default: repo root)"}},
        }

    async def run(self, cwd: str | None = None) -> str:
        work_dir = Path(cwd) if cwd and Path(cwd).is_absolute() else self.repo_path / (cwd or "")
        if not work_dir.is_dir():
            return f"Error: directory not found: {cwd}"
        session_id = await self.manager.open(str(work_dir))
        return f"Python REPL opened: {session_id}\nWorking directory: {work_dir}"


class ReplExecTool(Tool):
    def __init__(self, manager: PythonReplManager):
        self.manager = manager

    @property
    def name(self) -> str:
        return "repl_exec"

    @property
    def description(self) -> str:
        return "Execute Python in an existing persistent REPL. Returns bounded stdout, stderr, and the final expression value."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "code": {"type": "string"},
                "timeout": {"type": "integer", "description": "Seconds, capped at 120 (default: 30)"},
            },
            "required": ["session_id", "code"],
        }

    async def run(self, session_id: str, code: str, timeout: int = 30) -> str:
        try:
            result = await self.manager.execute(session_id, code, timeout)
        except asyncio.TimeoutError:
            await self.manager.close(session_id)
            return f"Error: REPL execution timed out after {min(timeout, 120)}s; session was closed to preserve framing."
        except Exception as exc:
            return f"Error: REPL execution failed: {exc}"
        if result is None:
            return f"Error: REPL session '{session_id}' not found or exited."
        parts = []
        if result.get("stdout"):
            parts.append("STDOUT:\n" + result["stdout"].rstrip())
        if result.get("stderr"):
            parts.append("STDERR:\n" + result["stderr"].rstrip())
        if result.get("value"):
            parts.append("VALUE:\n" + result["value"])
        text = "\n\n".join(parts) or "OK (no output)"
        return text[:12000] + ("\n... (truncated)" if len(text) > 12000 else "")


class ReplCloseTool(Tool):
    def __init__(self, manager: PythonReplManager):
        self.manager = manager

    @property
    def name(self) -> str:
        return "repl_close"

    @property
    def description(self) -> str:
        return "Close a persistent Python REPL and its process tree."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        }

    async def run(self, session_id: str) -> str:
        if not await self.manager.close(session_id):
            return f"Error: REPL session '{session_id}' not found."
        return f"Python REPL closed: {session_id}"
