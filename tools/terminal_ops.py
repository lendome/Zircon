"""Run long-running commands in a separate, visible terminal window.

``run_command`` blocks until the process exits, and ``shell_start`` runs the
process hidden with piped output. Neither is right for a server the user
should *see* running. The tools here instead open a NEW console window on the
user's desktop, stream the command's output both to that window and to a log
file, and return control to the agent after a caller-chosen wait — so the
agent can keep working and read the window's output again later.

Implementation notes (Windows):
- The command text is written VERBATIM into a .cmd body file. This preserves
  cmd.exe semantics exactly (``&`` chaining, ``%var%`` expansion, redirects)
  and avoids every quoting problem that embedding the command into another
  shell's command line would cause.
- The body appends an ``__ZIRCON_DONE__ exit_code=N`` marker line, which is
  how completion + exit status are detected without holding a process handle.
- A tiny wrapper pipes the body through ``Tee-Object`` so output appears live
  in the window AND lands in the log file the agent reads back.
- The window is launched with ``Start-Process -PassThru`` so its PID is known
  and can be killed (process tree) by ``terminal_stop``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from .base import Tool

logger = logging.getLogger("agent.tools.terminal_ops")

_DONE_MARKER_RE = re.compile(r"__ZIRCON_DONE__\s+exit_code=(\d+)")
_PID_RE = re.compile(r"ZIRCON_TERM_PID=(\d+)")
_MAX_TAIL_CHARS = 6000
_MAX_WAIT_SECONDS = 120


def _ps_quote(value: str) -> str:
    """Quote a string for embedding in a single-quoted PowerShell literal."""
    return "'" + value.replace("'", "''") + "'"


def _sanitize_title(title: str) -> str:
    """Strip characters that would break the `title` command in a .cmd file."""
    return re.sub(r"[&|<>^\"%\r\n]", " ", title).strip() or "Zircon Terminal"


class TerminalSession:
    """A command running in its own console window."""

    def __init__(
        self,
        term_id: str,
        command: str,
        cwd: str,
        title: str,
        log_path: Path,
        body_path: Path,
        run_path: Path,
    ):
        self.id = term_id
        self.command = command
        self.cwd = cwd
        self.title = title
        self.log_path = log_path
        self.body_path = body_path
        self.run_path = run_path
        self.pid: int | None = None
        self.start_time = time.monotonic()

    def runtime(self) -> float:
        return time.monotonic() - self.start_time

    def read_log(self) -> str:
        try:
            raw = self.log_path.read_bytes()
        except (FileNotFoundError, OSError):
            return ""
        # Tee-Object on Windows PowerShell writes UTF-16LE (with BOM) by
        # default; sniff the BOM so the log decodes correctly regardless.
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16", errors="replace")  # codec consumes the BOM
        if raw.startswith(b"\xef\xbb\xbf"):
            return raw.decode("utf-8-sig", errors="replace")
        return raw.decode("utf-8", errors="replace")

    def exit_code_from_log(self) -> int | None:
        m = _DONE_MARKER_RE.search(self.read_log())
        return int(m.group(1)) if m else None

    def tail(self, max_chars: int = _MAX_TAIL_CHARS) -> str:
        text = self.read_log()
        if len(text) > max_chars:
            text = "..." + text[-max_chars:]
        return text.strip()


async def _pid_alive(pid: int) -> bool:
    try:
        proc = await asyncio.create_subprocess_shell(
            f'tasklist /FI "PID eq {pid}" /NH',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return re.search(rf"\b{pid}\b", out.decode("utf-8", errors="replace")) is not None
    except Exception:
        return False


class TerminalManager:
    """Tracks terminal windows opened by run_in_terminal."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self._sessions: dict[str, TerminalSession] = {}
        self._next_id = 0

    def _alloc_id(self) -> str:
        self._next_id += 1
        return f"term_{self._next_id}"

    def get(self, term_id: str) -> TerminalSession | None:
        return self._sessions.get(term_id)

    def list_ids(self) -> list[str]:
        return list(self._sessions.keys())

    async def launch(
        self,
        command: str,
        cwd: str,
        title: str | None = None,
    ) -> TerminalSession:
        if os.name != "nt":
            raise RuntimeError(
                "run_in_terminal currently requires Windows. "
                "Use shell_start for hidden background processes instead."
            )

        self.base_dir.mkdir(parents=True, exist_ok=True)
        term_id = self._alloc_id()
        title = _sanitize_title(title or f"Zircon {term_id}")

        body_path = self.base_dir / f"{term_id}_body.cmd"
        run_path = self.base_dir / f"{term_id}_run.cmd"
        log_path = self.base_dir / f"{term_id}.log"
        launch_path = self.base_dir / f"{term_id}_launch.ps1"

        # Body: the user's command verbatim, then a completion marker. Each
        # line of a batch file is parsed separately, so %errorlevel% on the
        # marker line expands to the real exit code of the command above.
        body_path.write_text(
            "@echo off\r\n" + command + "\r\n" + "echo __ZIRCON_DONE__ exit_code=%errorlevel%\r\n",
            encoding="utf-8",
        )
        # Wrapper: tee output to the log file AND keep it visible in the window.
        run_path.write_text(
            "@echo off\r\n"
            f"title {title}\r\n"
            f'call "{body_path}" 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath {_ps_quote(str(log_path))}"\r\n',
            encoding="utf-8",
        )
        # Launcher: Start-Process gives us the new window's PID. The window is
        # `/c` (not `/k`) so it closes itself when the command finishes instead
        # of leaving dead windows behind; the log retains all output.
        launch_path.write_text(
            "$p = Start-Process -FilePath cmd.exe "
            f"-ArgumentList {_ps_quote('/c ' + chr(34) + str(run_path) + chr(34))} "
            f"-WorkingDirectory {_ps_quote(cwd)} -PassThru\n"
            'Write-Output "ZIRCON_TERM_PID=$($p.Id)"\n',
            encoding="utf-8",
        )

        session = TerminalSession(term_id, command, cwd, title, log_path, body_path, run_path)

        proc = await asyncio.create_subprocess_shell(
            f'powershell -NoProfile -ExecutionPolicy Bypass -File "{launch_path}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("timed out launching the terminal window")

        m = _PID_RE.search(out.decode("utf-8", errors="replace"))
        if m is None:
            detail = err.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                "failed to launch the terminal window"
                + (f": {detail}" if detail else "")
            )
        session.pid = int(m.group(1))
        self._sessions[term_id] = session
        return session

    async def status(self, session: TerminalSession) -> tuple[str, int | None]:
        """Return (status, exit_code): status is RUNNING or EXITED."""
        code = session.exit_code_from_log()
        if code is not None:
            return "EXITED", code
        if session.pid is not None and await _pid_alive(session.pid):
            return "RUNNING", None
        # No completion marker and the window's process is gone — it was
        # closed by the user or killed externally.
        return "EXITED", None

    async def wait_until_exit(self, session: TerminalSession, timeout: float) -> None:
        """Wait up to timeout seconds, returning as soon as the command exits."""
        deadline = time.monotonic() + timeout
        while timeout > 0:
            status, _ = await self.status(session)
            if status == "EXITED":
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.2, remaining))

    async def stop(self, term_id: str) -> TerminalSession | None:
        session = self._sessions.get(term_id)
        if session is None:
            return None
        if session.pid is not None and await _pid_alive(session.pid):
            try:
                proc = await asyncio.create_subprocess_shell(
                    f"taskkill /PID {session.pid} /T /F",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=10)
            except Exception as e:
                logger.debug("taskkill failed for %s: %s", term_id, e)
            await asyncio.sleep(0.5)
        return session


def _format_snapshot(
    session: TerminalSession,
    status: str,
    exit_code: int | None,
    *,
    header: str,
) -> str:
    if status == "RUNNING":
        status_line = "RUNNING"
    elif exit_code is not None:
        status_line = f"EXITED (exit code {exit_code})"
    else:
        status_line = "EXITED (window closed or process killed)"
    lines = [
        header,
        f"Terminal: {session.id} (PID {session.pid})",
        f"Command: {session.command}",
        f"Status: {status_line}",
        f"Runtime: {round(session.runtime(), 1)}s",
        f"Log file: {session.log_path}",
    ]
    tail = session.tail()
    if tail:
        lines.append(f"--- output so far ---\n{tail}")
    else:
        lines.append("--- no output captured yet ---")
    return "\n\n".join(lines)


class RunInTerminalTool(Tool):
    def __init__(self, repo_path: str, terminal_manager: TerminalManager):
        self.repo_path = Path(repo_path).resolve()
        self.tm = terminal_manager

    @property
    def name(self) -> str:
        return "run_in_terminal"

    @property
    def description(self) -> str:
        return (
            "OPEN A SEPARATE TERMINAL WINDOW and run the command there. "
            "This is the tool for LONG-RUNNING commands: servers, daemons, watchers, "
            "long builds/installers, or anything that does not exit quickly on its own. "
            "A new visible console window opens; the tool waits wait_seconds, then returns "
            "the output captured so far while the command KEEPS RUNNING in that window. "
            "Afterwards use terminal_output(id=..., wait_seconds=...) to read more of the "
            "window's output at any time, and terminal_stop(id=...) to kill it. "
            "NEVER start servers with run_command — it blocks until the process exits and "
            "can hang the whole session."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to run in the new terminal window"},
                "cwd": {"type": "string", "description": "Working directory (optional, default: repo root)"},
                "wait_seconds": {
                    "type": "integer",
                    "description": "Seconds to wait before returning the output captured so far (default: 5, max: 120). The command keeps running in the window afterwards.",
                },
                "title": {"type": "string", "description": "Optional title for the terminal window"},
            },
            "required": ["command"],
        }

    async def run(
        self,
        command: str,
        cwd: str | None = None,
        wait_seconds: int = 5,
        title: str | None = None,
    ) -> str:
        work_dir = self._resolve(cwd) if cwd else self.repo_path
        if not work_dir.is_dir():
            return f"Error: directory not found: {cwd or '.'}"

        try:
            session = await self.tm.launch(command, str(work_dir), title)
        except Exception as e:
            return f"Error opening terminal window: {e}"

        wait = max(0, min(int(wait_seconds), _MAX_WAIT_SECONDS))
        if wait:
            await self.tm.wait_until_exit(session, wait)

        status, code = await self.tm.status(session)
        result = _format_snapshot(
            session, status, code,
            header=f"Opened terminal window '{session.title}' and started the command.",
        )
        if status == "RUNNING":
            result += (
                f"\n\nThe command is still running in the other window. "
                f"Use terminal_output(id='{session.id}', wait_seconds=...) to read more output later, "
                f"or terminal_stop(id='{session.id}') to kill it."
            )
        return result

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class TerminalOutputTool(Tool):
    def __init__(self, terminal_manager: TerminalManager):
        self.tm = terminal_manager

    @property
    def name(self) -> str:
        return "terminal_output"

    @property
    def description(self) -> str:
        return (
            "Read the latest output from a command running in a separate terminal window "
            "started with run_in_terminal. Waits wait_seconds first, then returns whatever "
            "the window has printed — use this to give a long-running command time to work "
            "and then see its output (server startup banners, build progress, errors)."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Terminal ID returned by run_in_terminal (e.g. term_1)"},
                "wait_seconds": {
                    "type": "integer",
                    "description": "Seconds to wait before reading the output (default: 2, max: 120)",
                },
            },
            "required": ["id"],
        }

    async def run(self, id: str, wait_seconds: int = 2) -> str:
        session = self.tm.get(id)
        if session is None:
            known = ", ".join(self.tm.list_ids()) or "(none)"
            return f"Error: terminal '{id}' not found. Known terminals: {known}"

        wait = max(0, min(int(wait_seconds), _MAX_WAIT_SECONDS))
        if wait:
            await self.tm.wait_until_exit(session, wait)

        status, code = await self.tm.status(session)
        return _format_snapshot(session, status, code, header=f"Output of terminal '{id}':")


class TerminalStopTool(Tool):
    def __init__(self, terminal_manager: TerminalManager):
        self.tm = terminal_manager

    @property
    def name(self) -> str:
        return "terminal_stop"

    @property
    def description(self) -> str:
        return (
            "Kill a command running in a separate terminal window started with "
            "run_in_terminal (terminates the whole process tree) and return its "
            "final output. Always stop servers when you are done testing them."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Terminal ID returned by run_in_terminal (e.g. term_1)"},
            },
            "required": ["id"],
        }

    async def run(self, id: str) -> str:
        session = await self.tm.stop(id)
        if session is None:
            known = ", ".join(self.tm.list_ids()) or "(none)"
            return f"Error: terminal '{id}' not found. Known terminals: {known}"

        status, code = await self.tm.status(session)
        return _format_snapshot(
            session, status, code,
            header=f"Stopped terminal '{id}' (process tree killed).",
        )
