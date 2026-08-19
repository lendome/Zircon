"""
Terminal suspend/resume — Unix only (SIGTSTP/SIGCONT).

Ctrl+Z suspends the TUI. `fg` in the shell resumes it.
Disabled on Windows (no SIGTSTP).
"""

from __future__ import annotations

import signal
import sys
from typing import Any, Callable


def supports_suspend() -> bool:
    """Check if terminal suspend is supported (Unix only)."""
    return sys.platform != "win32" and hasattr(signal, "SIGTSTP")


def suspend_terminal(
    on_suspend: Callable[[], None] | None = None,
    on_resume: Callable[[], None] | None = None,
) -> bool:
    """
    Suspend the terminal (Ctrl+Z equivalent).

    Args:
        on_suspend: Called before suspending (e.g., stop renderer)
        on_resume: Called after resuming (e.g., resume renderer)

    Returns True if suspension was initiated, False if unsupported.
    """
    if not supports_suspend():
        return False

    def _handle_sigcont(signum: int, frame: Any) -> None:
        if on_resume is not None:
            on_resume()
        # Restore default handler
        signal.signal(signal.SIGCONT, signal.SIG_DFL)

    signal.signal(signal.SIGCONT, _handle_sigcont)

    if on_suspend is not None:
        on_suspend()

    # Send SIGTSTP to self
    try:
        import os
        os.kill(os.getpid(), signal.SIGTSTP)
        return True
    except (OSError, ProcessLookupError):
        return False
