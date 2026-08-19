"""
SIGHUP handler — clean up when terminal closes or SSH drops.

SIGHUP fires when the terminal closes or the SSH session drops.
Clean up the renderer to avoid leaving the terminal in a broken state.
"""

from __future__ import annotations

import signal
import sys
from typing import Any, Callable


class SighupHandler:
    """
    Registers a SIGHUP handler that cleans up resources.

    Usage:
        handler = SighupHandler(on_sighup=lambda: renderer.destroy())
        handler.register()
        # ... later ...
        handler.unregister()
    """

    def __init__(self, on_sighup: Callable[[], None] | None = None) -> None:
        self._on_sighup = on_sighup
        self._registered = False
        self._old_handler: Any = None

    def register(self) -> None:
        """Register the SIGHUP handler."""
        if self._registered:
            return
        if not hasattr(signal, "SIGHUP"):
            return  # Windows doesn't have SIGHUP
        try:
            self._old_handler = signal.getsignal(signal.SIGHUP)
            signal.signal(signal.SIGHUP, self._handle_sighup)
            self._registered = True
        except (ValueError, OSError):
            pass

    def unregister(self) -> None:
        """Restore the original SIGHUP handler."""
        if not self._registered:
            return
        if not hasattr(signal, "SIGHUP"):
            return
        try:
            signal.signal(signal.SIGHUP, self._old_handler or signal.SIG_DFL)
        except (ValueError, OSError):
            pass
        self._registered = False

    def _handle_sighup(self, signum: int, frame: Any) -> None:
        if self._on_sighup is not None:
            try:
                self._on_sighup()
            except Exception:
                pass
        # Re-raise default behavior
        if self._old_handler is not None and self._old_handler != signal.SIG_DFL:
            try:
                self._old_handler(signum, frame)
            except Exception:
                pass
