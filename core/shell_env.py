"""Shell environment detection and unified command capture.

The agent wasted turns fighting cmd.exe vs. PowerShell vs. bash syntax because
every command ran through the platform default shell (cmd.exe on Windows)
while the model wrote bash- or PowerShell-flavored commands. This module:

1. Detects ONE working shell per machine and pins it for the session:
   Git Bash -> pwsh -> powershell -> cmd on Windows; $SHELL -> bash -> sh
   elsewhere. Candidates are probe-verified (trivial echo round-trip) so a
   broken stub (e.g. the WindowsApps WSL launcher) is never selected.
2. Provides a single ``run_capture`` helper that executes a command through
   the pinned shell and returns structured stdout/stderr/exit-code data —
   tools never hand-roll pipes, drains, or timeout adoption again.
3. Formats results in ONE canonical layout (``STDOUT:`` / ``STDERR:`` /
   ``Exit code: N``) with CRLF normalization so downstream parsers
   (``runtime_probe.extract_exit_code``) keep working.

Nothing here changes behavior when detection lands on the platform default:
``cmd`` specs execute via ``asyncio.create_subprocess_shell`` exactly as
before.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import platform
import re
import shutil
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("agent.core.shell_env")

_PROBE_MARKER = "__zircon_shell_ok__"
_PROBE_TIMEOUT = 5.0


@dataclass(frozen=True)
class ShellSpec:
    """A resolved, probe-verified shell."""

    name: str
    """Human label: 'git-bash', 'pwsh', 'powershell', 'cmd', 'sh'."""
    kind: str
    """Syntax family: 'bash', 'powershell', 'cmd', 'sh'."""
    exe: str
    """Executable path (empty for the platform-default shell)."""
    prefix_args: tuple[str, ...] = ()
    """Args placed before the command string (e.g. ('-c',) for bash)."""

    @property
    def uses_default_shell(self) -> bool:
        """True when execution should go through create_subprocess_shell."""
        return not self.exe


@dataclass
class CaptureResult:
    """Structured result of a single command execution."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration: float = 0.0
    timed_out: bool = False
    # Set only when the process outlived a timeout AND kill_on_timeout=False
    # (caller adopts it as a background job; buffers/tasks stay live).
    proc: Any = None
    stdout_buf: list[str] = field(default_factory=list)
    stderr_buf: list[str] = field(default_factory=list)
    drain_tasks: list[Any] = field(default_factory=list)
    pipe_held_open: bool = False
    # Set when the command was rewritten by sanitize_command_for_shell —
    # surfaced in format_capture so the model learns the correct syntax.
    sanitize_note: str = ""


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _windows_candidates() -> list[ShellSpec]:
    """Ordered shell candidates for Windows, best first.

    Git Bash gives the model the bash syntax it knows best; pwsh is second.
    The WindowsApps ``bash.exe`` stub (WSL launcher) is explicitly excluded —
    without WSL installed it errors out, and probing it can hang.
    """
    candidates: list[ShellSpec] = []
    seen: set[str] = set()

    def _add_bash(path: str, name: str = "git-bash") -> None:
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen or "windowsapps" in norm:
            return
        if os.path.isfile(path):
            seen.add(norm)
            candidates.append(ShellSpec(name=name, kind="bash", exe=path, prefix_args=("-c",)))

    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env_var)
        if base:
            _add_bash(os.path.join(base, "Git", "bin", "bash.exe"))

    which_bash = shutil.which("bash")
    if which_bash:
        _add_bash(which_bash, name="bash")

    for exe, name in (("pwsh.exe", "pwsh"), ("powershell.exe", "powershell")):
        found = shutil.which(exe)
        if found:
            candidates.append(ShellSpec(
                name=name,
                kind="powershell",
                exe=found,
                prefix_args=("-NoProfile", "-NonInteractive", "-Command"),
            ))
            break  # prefer pwsh; only fall back to Windows PowerShell

    return candidates


def _posix_candidates() -> list[ShellSpec]:
    candidates: list[ShellSpec] = []
    shell = os.environ.get("SHELL")
    if shell and os.path.isfile(shell):
        candidates.append(ShellSpec(name=Path(shell).name, kind="sh", exe=shell, prefix_args=("-c",)))
    for path in ("/bin/bash", "/usr/bin/bash", "/bin/sh"):
        if os.path.isfile(path):
            candidates.append(ShellSpec(name=Path(path).name, kind="sh", exe=path, prefix_args=("-c",)))
    return candidates


def _probe(spec: ShellSpec) -> bool:
    """Verify a candidate shell actually runs a trivial command."""
    import subprocess

    from .proc_spawn import popen_kwargs

    try:
        proc = subprocess.run(
            [spec.exe, *spec.prefix_args, f"echo {_PROBE_MARKER}"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            **popen_kwargs(),
        )
        ok = proc.returncode == 0 and _PROBE_MARKER in (proc.stdout or "")
        if not ok:
            logger.debug("shell probe failed for %s (rc=%s)", spec.exe, proc.returncode)
        return ok
    except Exception as e:
        logger.debug("shell probe error for %s: %s", spec.exe, e)
        return False


def _detect() -> ShellSpec:
    is_windows = platform.system() == "Windows"
    candidates = _windows_candidates() if is_windows else _posix_candidates()
    for spec in candidates:
        if _probe(spec):
            logger.info("Pinned shell: %s (%s)", spec.name, spec.exe)
            return spec
    # Platform default: cmd.exe on Windows, POSIX sh elsewhere. Both are
    # executed via create_subprocess_shell (empty exe sentinel).
    fallback = ShellSpec(name="cmd" if is_windows else "sh", kind="cmd" if is_windows else "sh", exe="")
    logger.info("Pinned shell: %s (platform default)", fallback.name)
    return fallback


@functools.lru_cache(maxsize=2)
def _cached_detect(pinning_enabled: bool) -> ShellSpec:
    if not pinning_enabled:
        is_windows = platform.system() == "Windows"
        return ShellSpec(name="cmd" if is_windows else "sh", kind="cmd" if is_windows else "sh", exe="")
    return _detect()


def resolve_shell(pinning_enabled: bool = True) -> ShellSpec:
    """Return the pinned shell for this machine (detected once, then cached)."""
    return _cached_detect(bool(pinning_enabled))


def reset_shell_cache() -> None:
    """Clear the detection cache (tests; explicit re-detection)."""
    _cached_detect.cache_clear()


# ---------------------------------------------------------------------------
# Command sanitization
# ---------------------------------------------------------------------------

# cmd's null device is `nul`; POSIX shells have no such device, so `2>nul`
# under Git Bash silently creates a LITERAL FILE named `nul` in the cwd
# (observed in the wild: the agent's first probe command polluted the repo
# root with a stray `nul` file). Rewrite the redirect instead of letting the
# command run with a cmd-ism the pinned shell cannot understand.
_CMD_NUL_REDIRECT_RE = re.compile(r"(>>?)\s*nul\b", re.IGNORECASE)


def sanitize_command_for_shell(command: str, spec: ShellSpec) -> tuple[str, str | None]:
    """Rewrite cmd-only constructs that are harmful under POSIX shells.

    Returns ``(command, note)`` — *note* is a human/model-readable explanation
    of the rewrite, or None when the command was already clean. Never rewrites
    when the pinned shell is cmd/PowerShell (where `>nul` is valid).
    """
    if spec.kind not in ("bash", "sh"):
        return command, None
    rewritten = _CMD_NUL_REDIRECT_RE.sub(lambda m: f"{m.group(1)}/dev/null", command)
    if rewritten != command:
        return rewritten, (
            "Note: cmd-style '>nul' redirect was rewritten to '>/dev/null' "
            f"for {spec.name} — POSIX shells have no NUL device, and '>nul' "
            "would have created a literal file named 'nul' in the working "
            "directory. Use >/dev/null and 2>/dev/null in bash."
        )
    return command, None


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def spawn_kwargs() -> dict:
    """Subprocess kwargs for detached, tree-killable children.

    Windows: CREATE_NO_WINDOW console isolation (see proc_spawn).
    POSIX: start_new_session so the child leads its own process group —
    required for kill_process_tree to signal the whole tree without ever
    touching the agent's own process group.
    """
    from .proc_spawn import popen_kwargs

    kwargs = popen_kwargs()
    if os.name != "nt":
        kwargs["start_new_session"] = True
    return kwargs


async def kill_process_tree(proc: asyncio.subprocess.Process, *, timeout: float = 5.0) -> None:
    """Terminate a spawned process AND its entire descendant tree.

    ``proc.terminate()``/``proc.kill()`` signal only the direct child — which,
    for shell-pinned execution, is the shell WRAPPER (``bash -c`` / ``cmd /c``),
    not the actual command. The wrapper dies while the real process (e.g. a
    dev server it launched) keeps running as an orphan, still holding its
    port. That orphaned-server state is what made shell_stop report success
    while the server kept listening, sending the agent into a retry loop.

    Windows: ``taskkill /T /F`` kills the whole tree from the wrapper down.
    POSIX: children spawned via run_capture / ProcessManager are process-group
    leaders (start_new_session=True), so ``killpg`` reaches the whole tree;
    the group-leader check guarantees we never signal our own process group.
    Never raises.
    """
    if proc.returncode is not None:
        return
    if platform.system() == "Windows":
        try:
            from .proc_spawn import popen_kwargs

            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                **popen_kwargs(),
            )
            await asyncio.wait_for(killer.wait(), timeout=timeout)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return
    # POSIX
    try:
        if os.getpgid(proc.pid) == proc.pid:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        pass


async def run_capture(
    command: str,
    cwd: str | None = None,
    timeout: float = 60.0,
    stdin_text: str | None = None,
    spec: ShellSpec | None = None,
    kill_on_timeout: bool = True,
) -> CaptureResult:
    """Run *command* through the pinned shell and capture structured output.

    On timeout the process is killed by default. With
    ``kill_on_timeout=False`` the live process plus its drain tasks and
    buffers are handed back on the result so the caller can adopt it as a
    background job (see ``RunCommandTool`` -> ``ProcessManager.adopt``).
    Never raises; a launch failure is reported as exit_code=-1 with the
    error on stderr.
    """
    spec = spec or resolve_shell()
    t0 = time.monotonic()
    result = CaptureResult()

    # Rewrite cmd-isms the pinned shell cannot understand (e.g. `2>nul`
    # under Git Bash creates a literal file named `nul`). The note is
    # surfaced in the formatted output so the model learns the right syntax.
    command, sanitize_note = sanitize_command_for_shell(command, spec)
    if sanitize_note:
        result.sanitize_note = sanitize_note
        logger.info("sanitized command for %s: %s", spec.name, sanitize_note)

    try:
        if spec.uses_default_shell:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                **spawn_kwargs(),
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                spec.exe, *spec.prefix_args, command,
                stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                **spawn_kwargs(),
            )
    except Exception as e:
        result.stderr = f"Error launching command: {e}"
        result.duration = time.monotonic() - t0
        return result

    stdout_buf: list[str] = []
    stderr_buf: list[str] = []

    async def _drain(stream: asyncio.StreamReader, buf: list[str]) -> None:
        try:
            while True:
                data = await stream.read(4096)
                if not data:
                    break
                buf.append(data.decode("utf-8", errors="replace"))
        except Exception:
            pass

    out_task = asyncio.create_task(_drain(proc.stdout, stdout_buf))
    err_task = asyncio.create_task(_drain(proc.stderr, stderr_buf))

    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write((stdin_text + "\n").encode("utf-8", errors="replace"))
            await proc.stdin.drain()
            proc.stdin.close()
        except Exception:
            pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        if not kill_on_timeout and proc.returncode is None:
            # Hand the live process to the caller for background adoption.
            # Drain tasks keep running so shell_poll sees new output.
            result.stdout = _normalize("".join(stdout_buf))
            result.stderr = _normalize("".join(stderr_buf))
            result.timed_out = True
            result.duration = time.monotonic() - t0
            result.proc = proc
            result.stdout_buf = stdout_buf
            result.stderr_buf = stderr_buf
            result.drain_tasks = [out_task, err_task]
            return result
        if proc.returncode is None:
            # Kill the whole tree, not just the shell wrapper — otherwise the
            # real command (server, watcher) survives as an orphan.
            await kill_process_tree(proc)
        await asyncio.sleep(0.3)
        for t in (out_task, err_task):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        result.stdout = _normalize("".join(stdout_buf))
        result.stderr = _normalize("".join(stderr_buf))
        result.timed_out = True
        result.duration = time.monotonic() - t0
        return result

    # The child exited — but a detached grandchild can inherit the pipe
    # handles and hold them open forever. Bound the drain so the call can't
    # hang even though the command itself finished.
    try:
        await asyncio.wait_for(asyncio.gather(out_task, err_task), timeout=2.0)
    except asyncio.TimeoutError:
        result.pipe_held_open = True
        for t in (out_task, err_task):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    result.stdout = _normalize("".join(stdout_buf))
    result.stderr = _normalize("".join(stderr_buf))
    result.exit_code = proc.returncode if proc.returncode is not None else -1
    result.duration = time.monotonic() - t0
    return result


def _normalize(text: str) -> str:
    """CRLF -> LF so tools never fight line-ending differences."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_capture(
    result: CaptureResult,
    *,
    max_chars: int = 10000,
    timeout: float | None = None,
) -> str:
    """Canonical tool-output layout: STDOUT / STDERR / Exit code sections.

    Keeps the legacy ``Exit code: N`` trailer so
    ``runtime_probe.extract_exit_code`` and friends keep parsing.
    """
    parts: list[str] = []
    if result.sanitize_note:
        parts.append(result.sanitize_note)
    if result.timed_out:
        secs = timeout if timeout is not None else result.duration
        parts.append(f"Command timed out after {secs:.0f}s and was killed.")
    out = result.stdout.strip()
    err = result.stderr.strip()
    if out:
        parts.append(out)
    if err:
        parts.append(f"STDERR:\n{err}")
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
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text


def shell_syntax_hint(spec: ShellSpec) -> str:
    """One-line syntax guidance for tool descriptions, naming the live shell."""
    if spec.kind == "bash":
        return (
            f"Active shell: {spec.name} (bash syntax). Use ./tool.exe, ls, &&, "
            "single quotes. Do NOT use cmd-only syntax (>nul, dir /b) or "
            "PowerShell-only operators (2>&1 works; *> does not)."
        )
    if spec.kind == "powershell":
        return (
            f"Active shell: {spec.name} (PowerShell syntax). Use .\\tool.exe, "
            "Get-ChildItem, ; between commands. Do NOT use bash-only syntax "
            "(./, export VAR=) or cmd-only >nul redirects."
        )
    if spec.kind == "cmd":
        return (
            "Active shell: cmd.exe (Windows batch syntax). Use .\\tool.exe or "
            "tool.exe (NOT ./tool.exe), dir, >nul 2>&1 for silence. Do NOT use "
            "bash syntax (./, ls, && is OK on modern cmd) or PowerShell operators."
        )
    return f"Active shell: {spec.name} (POSIX sh syntax)."
