"""Windows-safe subprocess spawn flags.

Every non-interactive child process the agent spawns (shell commands, git,
ripgrep, syntax checkers, clipboard helpers) pipes its stdio — it never
needs a console. On Windows, a child that *shares* the TUI's console can
call ``SetConsoleMode`` on it: cmd.exe and the CPython runtime both restore
cooked mode (line input + echo + processed input) on startup/exit. While
the TUI key reader is blocked in ``os.read``, that mode clobber makes the
console driver echo typed characters at the physical cursor — outside the
input box — blocks single keystrokes until Enter, and turns Ctrl+C into a
process-killing KeyboardInterrupt instead of a readable key.

Spawning with ``CREATE_NO_WINDOW`` allocates no console for the child at
all, so it cannot read or clobber the TUI's console modes. The existing
``_rearm_windows_vt_input`` safety net in the TUI key reader stays as
defense in depth, but with detached children the clobber never happens.

Intentionally NOT applied to ``tools/terminal_ops.py``: ``run_in_terminal``
exists precisely to open visible console windows (see AGENTS.md gotcha 14).
"""

from __future__ import annotations

import sys

_IS_WINDOWS = sys.platform == "win32"

# Win32 process-creation flag: run the child with no console window and no
# attachment to the parent's console.
_CREATE_NO_WINDOW = 0x08000000


def popen_kwargs() -> dict:
    """Keyword args for subprocess.Popen / asyncio subprocess helpers that
    detach the child from the current console. Empty on POSIX (the
    ``creationflags`` parameter is Windows-only)."""
    if not _IS_WINDOWS:
        return {}
    return {"creationflags": _CREATE_NO_WINDOW}
